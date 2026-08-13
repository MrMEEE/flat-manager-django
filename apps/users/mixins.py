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


class ResourceActionRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Require an explicit resource/action permission for a protected view."""
    resource = None
    action = None

    def test_func(self):
        if not self.request.user.is_authenticated:
            return False
        if not self.resource or not self.action:
            return False
        return self.request.user.has_permission(self.resource, self.action)


class AdminRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Requires admin access for the user-management surface."""

    def test_func(self):
        u = self.request.user
        return u.is_authenticated and (u.is_staff or u.is_superuser or u.has_permission('users', 'admin'))


class RepoAdminRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Requires repo administration capabilities."""

    def test_func(self):
        return self.request.user.is_authenticated and self.request.user.has_permission('repositories', 'admin')


class BuildAdminRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Requires build capabilities for the build flow."""

    def test_func(self):
        return self.request.user.is_authenticated and self.request.user.has_permission('builds', 'build')


class WriteRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Requires any write-level permission for general edit actions."""

    def test_func(self):
        return self.request.user.is_authenticated and (
            self.request.user.has_permission('repositories', 'update')
            or self.request.user.has_permission('flatpaks', 'build')
            or self.request.user.has_permission('config', 'admin')
        )
