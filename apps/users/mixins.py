"""
Permission mixins for class-based views.

Hierarchy (highest → lowest):
  AdminRequiredMixin      – admin role, Django is_staff / is_superuser
  RepoAdminRequiredMixin  – repo_admin, admin, superuser roles
  BuildAdminRequiredMixin – build_admin, admin, superuser roles
  WriteRequiredMixin      – any of the above write roles
  LoginRequiredMixin      – authenticated only (read-only pages use this)

All write mixins redirect unauthenticated users to the login page and return
403 Forbidden for authenticated users who lack the required role.
"""

from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin


class AdminRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Requires the 'admin' role, Django is_staff, or Django is_superuser."""

    def test_func(self):
        u = self.request.user
        return u.is_staff or u.is_superuser or u.roles.filter(role='admin').exists()


class RepoAdminRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Requires repo_admin (or higher) role."""

    def test_func(self):
        return self.request.user.is_authenticated and self.request.user.can_repo_admin()


class BuildAdminRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Requires build_admin (or higher) role."""

    def test_func(self):
        return self.request.user.is_authenticated and self.request.user.can_build()


class WriteRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Requires any write role (build_admin, repo_admin, admin, superuser)."""

    def test_func(self):
        return self.request.user.is_authenticated and self.request.user.can_write()
