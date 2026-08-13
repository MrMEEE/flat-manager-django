from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, TestCase

from apps.flatpak.models import Organisation, Repository
from apps.flatpak.views import RepositoryUpdateView
from apps.users.models import (
    PermissionGrant,
    PermissionGroup,
    PermissionGroupPermission,
    User,
    UserRole,
    RESOURCE_ACTIONS,
    RESOURCE_CHOICES,
)


class PermissionGrantTests(TestCase):
    def test_permission_grant_allows_org_scoped_access(self):
        org = Organisation.objects.create(
            name='Org A',
            responsible_name='Alice',
            responsible_email='alice@example.com',
        )
        user = User.objects.create_user(username='alice', password='password')

        PermissionGrant.objects.create(
            user=user,
            organisation=org,
            resource='repositories',
            action='read',
            granted=True,
        )

        self.assertTrue(user.has_permission('repositories', 'read', organisation=org))

    def test_permission_grant_denies_cross_org_access(self):
        org_a = Organisation.objects.create(
            name='Org A',
            responsible_name='Alice',
            responsible_email='alice@example.com',
        )
        org_b = Organisation.objects.create(
            name='Org B',
            responsible_name='Bob',
            responsible_email='bob@example.com',
        )
        user = User.objects.create_user(username='bob', password='password')

        PermissionGrant.objects.create(
            user=user,
            organisation=org_a,
            resource='repositories',
            action='read',
            granted=True,
        )

        self.assertFalse(user.has_permission('repositories', 'read', organisation=org_b))


class PermissionGroupTests(TestCase):
    def test_permission_group_grants_access_to_member(self):
        org = Organisation.objects.create(
            name='Org A',
            responsible_name='Alice',
            responsible_email='alice@example.com',
        )
        user = User.objects.create_user(username='group-user', password='password')
        group = PermissionGroup.objects.create(
            name='Repo Readers',
            organisation=org,
        )
        group.users.add(user)
        PermissionGroupPermission.objects.create(
            group=group,
            organisation=org,
            resource='repositories',
            action='read',
            granted=True,
        )

        self.assertTrue(user.has_permission('repositories', 'read', organisation=org))

    def test_permission_group_denies_cross_org_access(self):
        org_a = Organisation.objects.create(
            name='Org A',
            responsible_name='Alice',
            responsible_email='alice@example.com',
        )
        org_b = Organisation.objects.create(
            name='Org B',
            responsible_name='Bob',
            responsible_email='bob@example.com',
        )
        user = User.objects.create_user(username='cross-org-user', password='password')
        group = PermissionGroup.objects.create(
            name='Org A Readers',
            organisation=org_a,
        )
        group.users.add(user)
        PermissionGroupPermission.objects.create(
            group=group,
            organisation=org_a,
            resource='repositories',
            action='read',
            granted=True,
        )

        self.assertFalse(user.has_permission('repositories', 'read', organisation=org_b))

    def test_predefined_groups_are_created_and_locked(self):
        groups = PermissionGroup.ensure_predefined_groups()
        names = {group.name for group in groups}

        self.assertTrue({'Admin', 'Build Admin', 'Repo Admin', 'Superuser', 'View'}.issubset(names))
        for group in groups:
            self.assertTrue(group.is_predefined)
            self.assertFalse(group.is_editable())

    def test_create_superuser_assigns_admin_permission_group(self):
        PermissionGroup.ensure_predefined_groups()

        user = User.objects.create_superuser(
            username='new-superuser',
            email='admin@example.com',
            password='secret123',
        )

        self.assertTrue(user.is_superuser)
        self.assertTrue(user.permission_groups.filter(name='Admin', organisation__isnull=True).exists())

    def test_legacy_roles_map_to_predefined_groups(self):
        PermissionGroup.ensure_predefined_groups()

        user = User.objects.create_user(username='legacy-admin', password='password')
        UserRole.objects.create(user=user, role='admin')

        PermissionGroup.sync_predefined_group_assignments()

        self.assertTrue(user.permission_groups.filter(name='Admin', organisation__isnull=True).exists())

    def test_legacy_roles_do_not_grant_access_without_permission_group(self):
        user = User.objects.create_user(username='legacy-role-only', password='password')
        UserRole.objects.create(user=user, role='admin')

        self.assertFalse(user.has_permission('repositories', 'read'))
        self.assertFalse(user.has_permission('users', 'manage_users'))

    def test_missing_resources_are_available_and_action_maps_are_resource_specific(self):
        available_resources = {value for value, _ in RESOURCE_CHOICES}
        self.assertTrue({'buildstreams', 'externals', 'dependencies'}.issubset(available_resources))

        self.assertIn('read', {value for value, _ in RESOURCE_ACTIONS['buildstreams']})
        self.assertIn('build', {value for value, _ in RESOURCE_ACTIONS['buildstreams']})
        self.assertIn('publish', {value for value, _ in RESOURCE_ACTIONS['externals']})
        self.assertIn('sync', {value for value, _ in RESOURCE_ACTIONS['dependencies']})
        self.assertIn('all', {value for value, _ in RESOURCE_ACTIONS['repositories']})

    def test_all_action_grants_every_permission_for_that_resource(self):
        user = User.objects.create_user(username='all-resource-user', password='password')
        PermissionGrant.objects.create(
            user=user,
            resource='repositories',
            action='all',
            granted=True,
        )

        self.assertTrue(user.has_permission('repositories', 'read'))
        self.assertTrue(user.has_permission('repositories', 'create'))
        self.assertTrue(user.has_permission('repositories', 'update'))
        self.assertTrue(user.has_permission('repositories', 'delete'))
        self.assertTrue(user.has_permission('repositories', 'sync'))

    def test_read_permission_does_not_grant_repository_update_access_in_view(self):
        user = User.objects.create_user(username='repo-reader', password='password')
        org = Organisation.objects.create(
            name='Org A',
            responsible_name='Alice',
            responsible_email='alice@example.com',
        )
        PermissionGrant.objects.create(
            user=user,
            resource='repositories',
            action='read',
            granted=True,
            organisation=org,
        )

        self.assertFalse(user.has_permission('repositories', 'update', organisation=org))

        repo = Repository.objects.create(name='Test Repo', created_by=user)
        request = RequestFactory().get(f'/flatpak/repos/{repo.pk}/edit/')
        request.user = user

        with self.assertRaises(PermissionDenied):
            RepositoryUpdateView.as_view()(request, pk=repo.pk)
