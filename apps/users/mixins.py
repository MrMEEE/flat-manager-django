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
from django.core.exceptions import PermissionDenied, ValidationError


def scope_queryset_for_user(queryset, user, resource, action='read', relation='organisations'):
    """Limit an organisation-tagged queryset to the user's RBAC scope."""
    organisation_ids = user.permission_organisation_ids(resource, action)
    if organisation_ids is None:
        return queryset
    if not organisation_ids:
        return queryset.none()
    return queryset.filter(**{f'{relation}__in': organisation_ids}).distinct()


def apply_default_organisation(instance, user):
    """Assign the user's default organisation when the new item has none."""
    organisation = getattr(user, 'default_organisation', None)
    organisations = getattr(instance, 'organisations', None)
    if organisation is not None and organisations is not None and not organisations.exists():
        organisations.add(organisation)


def configure_create_organisations(form, user, resource, action='create'):
    if 'organisations' not in form.fields:
        return
    allowed = user.permission_organisation_ids(resource, action)
    if allowed is not None:
        field = form.fields['organisations']
        field.queryset = field.queryset.filter(pk__in=allowed)
        if user.default_organisation_id in allowed:
            field.initial = [user.default_organisation_id]
        else:
            field.required = True


def resolve_create_organisation(user, resource, organisation_id=None):
    from apps.flatpak.models import Organisation

    allowed = user.permission_organisation_ids(resource, 'create')
    if allowed is not None and not allowed:
        raise PermissionDenied('You do not have permission to create this resource.')
    if organisation_id:
        try:
            organisation_id = int(organisation_id)
        except (TypeError, ValueError):
            raise ValidationError('Invalid organisation.')
        if allowed is not None and organisation_id not in allowed:
            raise PermissionDenied('You cannot create resources for this organisation.')
        organisation = Organisation.objects.filter(pk=organisation_id).first()
        if organisation is None:
            raise ValidationError('Invalid organisation.')
        return organisation
    organisation = getattr(user, 'default_organisation', None)
    if allowed is not None and (organisation is None or organisation.pk not in allowed):
        raise ValidationError('Select an organisation where you can create this resource.')
    return organisation


class ResourceActionRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Require an explicit resource/action permission for a protected view."""
    resource = None
    action = None

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        if self.action == 'create' and 'organisations' in form.fields:
            configure_create_organisations(form, self.request.user, self.resource)
        return form

    def form_valid(self, form):
        creating = getattr(self, 'object', None) is None
        if creating and self.action == 'create' and 'organisations' in form.fields:
            allowed = self.request.user.permission_organisation_ids(self.resource, 'create')
            selected = form.cleaned_data.get('organisations')
            default_id = self.request.user.default_organisation_id
            if allowed is not None and not selected and default_id not in allowed:
                form.add_error('organisations', 'Select an organisation where you can create this resource.')
                return self.form_invalid(form)
        if creating and hasattr(form.instance, 'created_by_id'):
            form.instance.created_by = self.request.user
        response = super().form_valid(form)
        if creating:
            apply_default_organisation(self.object, self.request.user)
        return response

    def test_func(self):
        if not self.request.user.is_authenticated:
            return False
        if not self.resource or not self.action:
            return False
        return self.request.user.has_permission(self.resource, self.action) or bool(
            self.request.user.permission_organisation_ids(self.resource, self.action)
        )


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
        return self.request.user.is_authenticated and (
            self.request.user.has_permission('builds', 'build')
            or bool(self.request.user.permission_organisation_ids('builds', 'build'))
        )


class WriteRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Requires any write-level permission for general edit actions."""

    def test_func(self):
        return self.request.user.is_authenticated and (
            self.request.user.has_permission('repositories', 'update')
            or self.request.user.has_permission('flatpaks', 'build')
            or self.request.user.has_permission('config', 'admin')
        )
