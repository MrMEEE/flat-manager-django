from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.shortcuts import render, redirect
from django.views import View
from django.views.generic import ListView, DetailView, CreateView, UpdateView
from django.urls import reverse_lazy
from django.contrib import messages
from .models import User, UserProfile


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
        context = {
            'user': request.user,
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
    fields = ['username', 'email', 'first_name', 'last_name', 'is_repo_admin', 'is_build_admin', 'is_staff']
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
