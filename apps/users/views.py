from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.shortcuts import render, redirect, get_object_or_404
from django.views import View
from django.views.generic import ListView, DetailView, CreateView, UpdateView
from django.urls import reverse_lazy
from django.contrib import messages
from django.db import models
from .models import User, UserProfile
from .forms import UserCreateForm, SetPasswordForm


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
            'recent_builds':       recent_builds,
            'clients_online':      clients_online,
            'clients_offline':     clients_offline,
            'clients_uptodate':    clients_uptodate,
            'clients_outdated':    clients_outdated,
            'clients_foreign':     clients_foreign,
        }
        return render(request, 'users/dashboard.html', context)


class AdminRequiredMixin(UserPassesTestMixin):
    """Mixin to require staff or superuser status."""
    def test_func(self):
        return self.request.user.is_staff or self.request.user.is_superuser


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
    fields = ['username', 'email', 'first_name', 'last_name', 'is_repo_admin', 'is_build_admin', 'is_staff', 'is_active']
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
