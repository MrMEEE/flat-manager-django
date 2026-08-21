"""
DRF permission classes that mirror the view-layer mixins in apps.users.mixins.
"""

from rest_framework.permissions import BasePermission, IsAuthenticated


class IsAdmin(BasePermission):
    """Requires admin access for the user-management surface."""

    def has_permission(self, request, view):
        u = request.user
        return bool(u and u.is_authenticated and (u.is_staff or u.is_superuser or u.has_permission('users', 'admin')))


class CanBuild(BasePermission):
    """Requires build capabilities on the build workflow."""

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.has_permission('builds', 'build'))


class CanRepoAdmin(BasePermission):
    """Requires repo administration capabilities."""

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.has_permission('repositories', 'admin'))


class CanWrite(BasePermission):
    """Requires any general write-level permission."""

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and (
            request.user.has_permission('repositories', 'update')
            or request.user.has_permission('flatpaks', 'build')
            or request.user.has_permission('config', 'admin')
        ))


class IsAuthenticatedOrReadOnly(BasePermission):
    """
    Authenticated users get full access; unauthenticated get read-only (GET/HEAD/OPTIONS).
    Write operations additionally require a write role.
    """

    SAFE_METHODS = ('GET', 'HEAD', 'OPTIONS')

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if request.method in self.SAFE_METHODS:
            return True
        return request.user.can_write()


class ReadOnly(BasePermission):
    """Allows only safe (read) methods for authenticated users."""

    SAFE_METHODS = ('GET', 'HEAD', 'OPTIONS')

    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and request.method in self.SAFE_METHODS
        )
