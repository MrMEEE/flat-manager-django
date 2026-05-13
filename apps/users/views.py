from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.mixins import LoginRequiredMixin
from .mixins import AdminRequiredMixin
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.views import View
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.urls import reverse_lazy, reverse
from django.contrib import messages
from django.db import models
from .models import User, UserProfile, UserRole, LDAPSource, LDAPGroupMapping, ROLE_CHOICES
from .forms import (
    UserCreateForm, UserUpdateForm, SetPasswordForm, ChangePasswordForm,
    LDAPSourceForm, LDAPGroupMappingForm, UserRoleForm,
)


class IndexView(View):
    """Landing page view."""
    def get(self, request):
        if request.user.is_authenticated:
            return redirect('users:dashboard')
        return render(request, 'users/index.html')


class LoginView(View):
    """Custom login view."""
    def get(self, request):
        if request.user.is_authenticated:
            return redirect('users:dashboard')
        return render(request, 'users/login.html')
    
    def post(self, request):
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)
        
        if user is not None:
            login(request, user)
            messages.success(request, f'Welcome back, {user.username}!')
            return redirect('users:dashboard')
        else:
            messages.error(request, 'Invalid username or password.')
            return render(request, 'users/login.html')


class LogoutView(LoginRequiredMixin, View):
    """Logout view."""
    def post(self, request):
        logout(request)
        messages.info(request, 'You have been logged out.')
        return redirect('users:index')


class DashboardView(LoginRequiredMixin, View):
    """Main dashboard view."""
    def get(self, request):
        from apps.flatpak.models import Repository, Package, Build, Client, SiteConfig, ExternalRef
        from django.db.models import Count, Max, F
        from django.utils import timezone
        from datetime import timedelta

        repo_count = Repository.objects.filter(is_active=True).count()
        package_count = Package.objects.count()

        packages_building  = Package.objects.filter(status__in=['building', 'committing', 'committed', 'publishing']).count()
        packages_built     = Package.objects.filter(status='built').count()
        packages_failed    = Package.objects.filter(status__in=['failed', 'cancelled']).count()
        packages_published = Package.objects.filter(status='published').count()
        packages_outdated  = Package.objects.filter(
            upstream_version__isnull=False
        ).exclude(upstream_version='').exclude(upstream_version=F('version')).count()
        packages_deps_outdated = Package.objects.filter(deps_need_rebuild=True).count()

        external_count      = ExternalRef.objects.count()
        externals_importing = ExternalRef.objects.filter(status__in=['pulling', 'publishing']).count()
        externals_imported  = ExternalRef.objects.filter(status__in=['pulled', 'published']).count()
        externals_failed    = ExternalRef.objects.filter(status='failed').count()
        externals_outdated  = ExternalRef.objects.filter(update_available=True).count()
        externals_published = ExternalRef.objects.filter(status='published').count()

        recent_builds = (
            Build.objects
            .select_related('package', 'package__repository', 'bst_source', 'bst_source__repository')
            .order_by('-started_at')[:10]
        )

        # Client stats
        stale_hours = SiteConfig.get_solo().client_stale_hours
        stale_threshold = timezone.now() - timedelta(hours=stale_hours)
        clients_online   = Client.objects.filter(last_checkin__gte=stale_threshold).count()
        clients_offline  = Client.objects.filter(
            models.Q(last_checkin__lt=stale_threshold) | models.Q(last_checkin__isnull=True)
        ).count()
        clients_uptodate = Client.objects.filter(outdated_count=0).count()
        clients_outdated = Client.objects.filter(outdated_count__gt=0).count()
        clients_foreign  = Client.objects.filter(foreign_count__gt=0).count()

        context = {
            'user': request.user,
            'repo_count':          repo_count,
            'package_count':       package_count,
            'packages_building':   packages_building,
            'packages_built':      packages_built,
            'packages_failed':     packages_failed,
            'packages_published':  packages_published,
            'packages_outdated':   packages_outdated,
            'packages_deps_outdated': packages_deps_outdated,
            'external_count':       external_count,
            'externals_importing':  externals_importing,
            'externals_imported':   externals_imported,
            'externals_failed':     externals_failed,
            'externals_outdated':   externals_outdated,
            'externals_published':  externals_published,
            'recent_builds':       recent_builds,
            'clients_online':      clients_online,
            'clients_offline':     clients_offline,
            'clients_uptodate':    clients_uptodate,
            'clients_outdated':    clients_outdated,
            'clients_foreign':     clients_foreign,
        }
        return render(request, 'users/dashboard.html', context)


class DashboardStatsApiView(LoginRequiredMixin, View):
    """Lightweight JSON endpoint: returns all dashboard stat counters."""

    def get(self, request):
        from apps.flatpak.models import Repository, Package, Build, Client, SiteConfig, ExternalRef
        from django.db.models import F
        from django.utils import timezone
        from datetime import timedelta

        stale_hours = SiteConfig.get_solo().client_stale_hours
        stale_threshold = timezone.now() - timedelta(hours=stale_hours)

        return JsonResponse({
            'repo_count':             Repository.objects.filter(is_active=True).count(),
            'package_count':          Package.objects.count(),
            'packages_building':      Package.objects.filter(status__in=['building', 'committing', 'committed', 'publishing']).count(),
            'packages_built':         Package.objects.filter(status='built').count(),
            'packages_failed':        Package.objects.filter(status__in=['failed', 'cancelled']).count(),
            'packages_published':     Package.objects.filter(status='published').count(),
            'packages_outdated':      Package.objects.filter(
                upstream_version__isnull=False
            ).exclude(upstream_version='').exclude(upstream_version=F('version')).count(),
            'packages_deps_outdated': Package.objects.filter(deps_need_rebuild=True).count(),
            'external_count':         ExternalRef.objects.count(),
            'externals_importing':    ExternalRef.objects.filter(status__in=['pulling', 'publishing']).count(),
            'externals_imported':     ExternalRef.objects.filter(status__in=['pulled', 'published']).count(),
            'externals_failed':       ExternalRef.objects.filter(status='failed').count(),
            'externals_outdated':     ExternalRef.objects.filter(update_available=True).count(),
            'externals_published':    ExternalRef.objects.filter(status='published').count(),
            'clients_online':         Client.objects.filter(last_checkin__gte=stale_threshold).count(),
            'clients_offline':        Client.objects.filter(
                models.Q(last_checkin__lt=stale_threshold) | models.Q(last_checkin__isnull=True)
            ).count(),
            'clients_uptodate':       Client.objects.filter(outdated_count=0).count(),
            'clients_outdated':       Client.objects.filter(outdated_count__gt=0).count(),
            'clients_foreign':        Client.objects.filter(foreign_count__gt=0).count(),
        })


class UserListView(AdminRequiredMixin, ListView):
    """List all users (admin only)."""
    model = User
    template_name = 'users/user_list.html'
    context_object_name = 'users'
    paginate_by = 20


class UserDetailView(AdminRequiredMixin, DetailView):
    """User detail view (admin only)."""
    model = User
    template_name = 'users/user_detail.html'
    context_object_name = 'user_obj'


class UserCreateView(AdminRequiredMixin, CreateView):
    """Create new user (admin only)."""
    model = User
    template_name = 'users/user_form.html'
    form_class = UserCreateForm
    success_url = reverse_lazy('users:user_list')

    def form_valid(self, form):
        messages.success(self.request, 'User created successfully.')
        return super().form_valid(form)


class UserUpdateView(AdminRequiredMixin, UpdateView):
    """Update user (admin only)."""
    model = User
    template_name = 'users/user_form.html'
    form_class = UserUpdateForm
    success_url = reverse_lazy('users:user_list')

    def form_valid(self, form):
        messages.success(self.request, 'User updated successfully.')
        return super().form_valid(form)


class UserSetPasswordView(AdminRequiredMixin, View):
    """Set or change a user's password (admin only)."""
    template_name = 'users/user_password.html'

    def get_user(self, pk):
        return get_object_or_404(User, pk=pk)

    def get(self, request, pk):
        user_obj = self.get_user(pk)
        form = SetPasswordForm()
        return render(request, self.template_name, {'form': form, 'user_obj': user_obj})

    def post(self, request, pk):
        user_obj = self.get_user(pk)
        form = SetPasswordForm(request.POST)
        if form.is_valid():
            user_obj.set_password(form.cleaned_data['password1'])
            user_obj.save(update_fields=['password'])
            messages.success(request, f'Password updated for {user_obj.username}.')
            return redirect('users:user_detail', pk=pk)
        return render(request, self.template_name, {'form': form, 'user_obj': user_obj})


class ProfileView(LoginRequiredMixin, View):
    """User profile view."""

    def _context(self, request, pw_form=None):
        return {
            'profile': request.user.profile,
            'pw_form': pw_form or ChangePasswordForm(user=request.user),
        }

    def get(self, request):
        return render(request, 'users/profile.html', self._context(request))

    def post(self, request):
        action = request.POST.get('action')

        if action == 'change_password':
            if not request.user.is_local:
                messages.error(request, 'Password changes are only available for local accounts.')
                return redirect('users:profile')
            form = ChangePasswordForm(user=request.user, data=request.POST)
            if form.is_valid():
                form.save()
                # Re-authenticate so the session stays valid after the password change.
                from django.contrib.auth import update_session_auth_hash
                update_session_auth_hash(request, request.user)
                messages.success(request, 'Password changed successfully.')
                return redirect('users:profile')
            return render(request, 'users/profile.html', self._context(request, pw_form=form))

        # Default: profile update
        profile = request.user.profile
        profile.bio = request.POST.get('bio', '')
        profile.phone = request.POST.get('phone', '')
        profile.organization = request.POST.get('organization', '')
        profile.save()
        messages.success(request, 'Profile updated successfully.')
        return redirect('users:profile')


# ---------------------------------------------------------------------------
# User Role Management
# ---------------------------------------------------------------------------

class UserRoleView(AdminRequiredMixin, View):
    """Manage roles for a single user."""
    template_name = 'users/user_roles.html'

    def get_user(self, pk):
        return get_object_or_404(User, pk=pk)

    def get(self, request, pk):
        user_obj = self.get_user(pk)
        roles = user_obj.roles.select_related('organisation').order_by('role', 'organisation__name')
        form = UserRoleForm()
        return render(request, self.template_name, {
            'user_obj': user_obj,
            'roles': roles,
            'form': form,
            'role_choices': ROLE_CHOICES,
        })

    def post(self, request, pk):
        user_obj = self.get_user(pk)
        action = request.POST.get('action', 'add')

        if action == 'delete':
            role_pk = request.POST.get('role_pk')
            UserRole.objects.filter(pk=role_pk, user=user_obj).delete()
            messages.success(request, 'Role removed.')
        else:
            form = UserRoleForm(request.POST)
            if form.is_valid():
                role = form.save(commit=False)
                role.user = user_obj
                role.save()
                messages.success(request, 'Role added.')
            else:
                roles = user_obj.roles.select_related('organisation').order_by('role', 'organisation__name')
                return render(request, self.template_name, {
                    'user_obj': user_obj,
                    'roles': roles,
                    'form': form,
                    'role_choices': ROLE_CHOICES,
                })

        return redirect('users:user_roles', pk=pk)


# ---------------------------------------------------------------------------
# LDAP Source CRUD
# ---------------------------------------------------------------------------

class LDAPSourceListView(AdminRequiredMixin, ListView):
    model = LDAPSource
    template_name = 'users/ldap_source_list.html'
    context_object_name = 'sources'


class LDAPSourceCreateView(AdminRequiredMixin, View):
    template_name = 'users/ldap_source_form.html'

    def get(self, request):
        return render(request, self.template_name, {'form': LDAPSourceForm(), 'action': 'Create'})

    def post(self, request):
        form = LDAPSourceForm(request.POST)
        if form.is_valid():
            source = form.save(commit=False)
            plaintext = form.cleaned_data.get('bind_password')
            if plaintext:
                source.set_bind_password(plaintext)
            source.save()
            messages.success(request, f'LDAP source "{source.name}" created.')
            return redirect('users:ldap_detail', pk=source.pk)
        return render(request, self.template_name, {'form': form, 'action': 'Create'})


class LDAPSourceDetailView(AdminRequiredMixin, DetailView):
    model = LDAPSource
    template_name = 'users/ldap_source_detail.html'
    context_object_name = 'source'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['mappings'] = self.object.group_mappings.select_related('organisation').order_by('ldap_group_dn')
        ctx['mapping_form'] = LDAPGroupMappingForm()
        return ctx


class LDAPSourceUpdateView(AdminRequiredMixin, View):
    template_name = 'users/ldap_source_form.html'

    def get_source(self, pk):
        return get_object_or_404(LDAPSource, pk=pk)

    def get(self, request, pk):
        source = self.get_source(pk)
        form = LDAPSourceForm(instance=source)
        return render(request, self.template_name, {'form': form, 'source': source, 'action': 'Edit'})

    def post(self, request, pk):
        source = self.get_source(pk)
        form = LDAPSourceForm(request.POST, instance=source)
        if form.is_valid():
            source = form.save(commit=False)
            plaintext = form.cleaned_data.get('bind_password')
            if plaintext:
                source.set_bind_password(plaintext)
            source.save()
            messages.success(request, f'LDAP source "{source.name}" updated.')
            return redirect('users:ldap_detail', pk=source.pk)
        return render(request, self.template_name, {'form': form, 'source': source, 'action': 'Edit'})


class LDAPSourceDeleteView(AdminRequiredMixin, View):
    template_name = 'users/ldap_source_confirm_delete.html'

    def get_source(self, pk):
        return get_object_or_404(LDAPSource, pk=pk)

    def get(self, request, pk):
        source = self.get_source(pk)
        return render(request, self.template_name, {'source': source})

    def post(self, request, pk):
        source = self.get_source(pk)
        name = source.name
        source.delete()
        messages.success(request, f'LDAP source "{name}" deleted.')
        return redirect('users:ldap_list')


# ---------------------------------------------------------------------------
# LDAP connection / search tests (AJAX)
# ---------------------------------------------------------------------------

def _build_ldap_server(hostname, port, protocol, verify_certs):
    """Return an ldap3.Server instance (or raise on import error)."""
    import ldap3  # noqa: PLC0415
    return ldap3.Server(
        hostname,
        port=port,
        use_ssl=(protocol == 'ldaps'),
        connect_timeout=5,
        tls=ldap3.Tls(validate=2 if verify_certs else 0),
    )


def _resolve_bind_password(post_password: str, source_pk: str) -> str:
    """Return the bind password: use posted value or fall back to stored one."""
    if post_password:
        return post_password
    if source_pk:
        try:
            return LDAPSource.objects.get(pk=source_pk).get_bind_password() or ''
        except LDAPSource.DoesNotExist:
            pass
    return ''


class LDAPSourceTestConnectionView(AdminRequiredMixin, View):
    """AJAX POST — test LDAP server reachability and service-account bind."""

    def post(self, request):
        try:
            import ldap3
        except ImportError:
            return JsonResponse({'ok': False, 'message': 'ldap3 is not installed on this server.'})

        hostname = request.POST.get('hostname', '').strip()
        if not hostname:
            return JsonResponse({'ok': False, 'message': 'Hostname is required.'})

        try:
            port = int(request.POST.get('port') or 389)
        except (TypeError, ValueError):
            return JsonResponse({'ok': False, 'message': 'Invalid port number.'})

        protocol     = request.POST.get('protocol', 'ldap')
        verify_certs = request.POST.get('verify_certs') in ('on', 'true', '1')
        bind_dn      = request.POST.get('bind_dn', '').strip()
        bind_password = _resolve_bind_password(
            request.POST.get('bind_password', '').strip(),
            request.POST.get('source_pk', '').strip(),
        )

        try:
            server = _build_ldap_server(hostname, port, protocol, verify_certs)
            conn = ldap3.Connection(
                server,
                user=bind_dn or None,
                password=bind_password or None,
                authentication=ldap3.SIMPLE if bind_dn else ldap3.ANONYMOUS,
                auto_bind=ldap3.AUTO_BIND_NO_TLS,
            )
            if not conn.bind():
                return JsonResponse({'ok': False, 'message': f'Bind failed: {conn.result}'})
            conn.unbind()
            msg = 'Connection and bind successful.'
            return JsonResponse({'ok': True, 'message': msg})
        except Exception as exc:
            return JsonResponse({'ok': False, 'message': str(exc)})


class LDAPSourceTestSearchView(AdminRequiredMixin, View):
    """AJAX POST — search for a user in the LDAP directory."""

    def post(self, request):
        try:
            import ldap3
        except ImportError:
            return JsonResponse({'ok': False, 'message': 'ldap3 is not installed on this server.'})

        hostname = request.POST.get('hostname', '').strip()
        if not hostname:
            return JsonResponse({'ok': False, 'message': 'Hostname is required.'})

        try:
            port = int(request.POST.get('port') or 389)
        except (TypeError, ValueError):
            return JsonResponse({'ok': False, 'message': 'Invalid port number.'})

        protocol       = request.POST.get('protocol', 'ldap')
        verify_certs   = request.POST.get('verify_certs') in ('on', 'true', '1')
        bind_dn        = request.POST.get('bind_dn', '').strip()
        bind_password  = _resolve_bind_password(
            request.POST.get('bind_password', '').strip(),
            request.POST.get('source_pk', '').strip(),
        )
        base_dn        = request.POST.get('base_dn', '').strip()
        ldap_filter    = request.POST.get('ldap_filter', '').strip() or '(objectClass=person)'
        server_type    = request.POST.get('server_type', 'ad')
        search_username = request.POST.get('search_username', '').strip()

        if not base_dn:
            return JsonResponse({'ok': False, 'message': 'Base DN is required to perform a search.'})
        if not search_username:
            return JsonResponse({'ok': False, 'message': 'Enter a username to search for.'})

        uid_attr = 'sAMAccountName' if server_type == 'ad' else 'uid'
        escaped  = ldap3.utils.conv.escape_filter_chars(search_username)
        search_filter = f'(&{ldap_filter}({uid_attr}={escaped}))'
        attrs = ['cn', 'givenName', 'sn', 'mail', 'sAMAccountName', 'uid', 'memberOf']

        try:
            server = _build_ldap_server(hostname, port, protocol, verify_certs)
            conn = ldap3.Connection(
                server,
                user=bind_dn or None,
                password=bind_password or None,
                authentication=ldap3.SIMPLE if bind_dn else ldap3.ANONYMOUS,
                auto_bind=ldap3.AUTO_BIND_NO_TLS,
            )
            if not conn.bind():
                return JsonResponse({'ok': False, 'message': f'Service bind failed: {conn.result}'})

            if not conn.search(
                search_base=base_dn,
                search_filter=search_filter,
                search_scope=ldap3.SUBTREE,
                attributes=attrs,
            ):
                conn.unbind()
                return JsonResponse({'ok': False, 'message': f'Search failed: {conn.result}'})

            entries = [e for e in conn.entries if e.entry_dn]
            conn.unbind()

            if not entries:
                return JsonResponse({'ok': False, 'message': f'No entry found for {search_username!r}.'})

            entry = entries[0]
            result_attrs = {}
            for attr in attrs:
                try:
                    vals = list(entry[attr].values)
                    if vals:
                        result_attrs[attr] = vals[0] if len(vals) == 1 else vals
                except Exception:
                    pass

            return JsonResponse({
                'ok': True,
                'message': f'Found: {entry.entry_dn}',
                'dn': entry.entry_dn,
                'attributes': result_attrs,
            })
        except Exception as exc:
            return JsonResponse({'ok': False, 'message': str(exc)})


# ---------------------------------------------------------------------------
# LDAP Group Mapping
# ---------------------------------------------------------------------------

class LDAPGroupMappingCreateView(AdminRequiredMixin, View):
    """Add a group mapping to an LDAP source."""

    def post(self, request, source_pk):
        source = get_object_or_404(LDAPSource, pk=source_pk)
        form = LDAPGroupMappingForm(request.POST)
        if form.is_valid():
            mapping = form.save(commit=False)
            mapping.source = source
            mapping.save()
            messages.success(request, 'Group mapping added.')
        else:
            messages.error(request, 'Invalid group mapping data.')
        return redirect('users:ldap_detail', pk=source_pk)


class LDAPGroupMappingDeleteView(AdminRequiredMixin, View):
    """Remove a group mapping from an LDAP source."""

    def post(self, request, source_pk, pk):
        mapping = get_object_or_404(LDAPGroupMapping, pk=pk, source_id=source_pk)
        mapping.delete()
        messages.success(request, 'Group mapping removed.')
        return redirect('users:ldap_detail', pk=source_pk)

