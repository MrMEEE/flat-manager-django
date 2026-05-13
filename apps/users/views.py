from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.views import View
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.urls import reverse_lazy, reverse
from django.contrib import messages
from django.db import models
from .models import User, UserProfile, UserRole, LDAPSource, LDAPGroupMapping, ROLE_CHOICES
from .forms import (
    UserCreateForm, UserUpdateForm, SetPasswordForm,
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


class AdminRequiredMixin(UserPassesTestMixin):
    """Mixin to require admin role (or Django superuser / staff)."""
    def test_func(self):
        u = self.request.user
        return u.is_staff or u.is_superuser or u.roles.filter(role='admin').exists()


class UserListView(LoginRequiredMixin, AdminRequiredMixin, ListView):
    """List all users (admin only)."""
    model = User
    template_name = 'users/user_list.html'
    context_object_name = 'users'
    paginate_by = 20


class UserDetailView(LoginRequiredMixin, AdminRequiredMixin, DetailView):
    """User detail view (admin only)."""
    model = User
    template_name = 'users/user_detail.html'
    context_object_name = 'user_obj'


class UserCreateView(LoginRequiredMixin, AdminRequiredMixin, CreateView):
    """Create new user (admin only)."""
    model = User
    template_name = 'users/user_form.html'
    form_class = UserCreateForm
    success_url = reverse_lazy('users:user_list')

    def form_valid(self, form):
        messages.success(self.request, 'User created successfully.')
        return super().form_valid(form)


class UserUpdateView(LoginRequiredMixin, AdminRequiredMixin, UpdateView):
    """Update user (admin only)."""
    model = User
    template_name = 'users/user_form.html'
    form_class = UserUpdateForm
    success_url = reverse_lazy('users:user_list')

    def form_valid(self, form):
        messages.success(self.request, 'User updated successfully.')
        return super().form_valid(form)


class UserSetPasswordView(LoginRequiredMixin, AdminRequiredMixin, View):
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
    def get(self, request):
        return render(request, 'users/profile.html', {'profile': request.user.profile})
    
    def post(self, request):
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

class UserRoleView(LoginRequiredMixin, AdminRequiredMixin, View):
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

class LDAPSourceListView(LoginRequiredMixin, AdminRequiredMixin, ListView):
    model = LDAPSource
    template_name = 'users/ldap_source_list.html'
    context_object_name = 'sources'


class LDAPSourceCreateView(LoginRequiredMixin, AdminRequiredMixin, View):
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


class LDAPSourceDetailView(LoginRequiredMixin, AdminRequiredMixin, DetailView):
    model = LDAPSource
    template_name = 'users/ldap_source_detail.html'
    context_object_name = 'source'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['mappings'] = self.object.group_mappings.select_related('organisation').order_by('ldap_group_dn')
        ctx['mapping_form'] = LDAPGroupMappingForm()
        return ctx


class LDAPSourceUpdateView(LoginRequiredMixin, AdminRequiredMixin, View):
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


class LDAPSourceDeleteView(LoginRequiredMixin, AdminRequiredMixin, View):
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
# LDAP Group Mapping
# ---------------------------------------------------------------------------

class LDAPGroupMappingCreateView(LoginRequiredMixin, AdminRequiredMixin, View):
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


class LDAPGroupMappingDeleteView(LoginRequiredMixin, AdminRequiredMixin, View):
    """Remove a group mapping from an LDAP source."""

    def post(self, request, source_pk, pk):
        mapping = get_object_or_404(LDAPGroupMapping, pk=pk, source_id=source_pk)
        mapping.delete()
        messages.success(request, 'Group mapping removed.')
        return redirect('users:ldap_detail', pk=source_pk)

