import json

from django.contrib import admin
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.backends.db import SessionStore
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import RequestFactory, TestCase
from unittest.mock import patch
from rest_framework.authtoken.models import Token as AuthToken
from rest_framework.test import APIClient
from rest_framework.authtoken.models import Token as AuthToken
from rest_framework.test import APIClient

from apps.flatpak.models import (
    ExternalRef,
    ExternalRefVersion,
    FlatpakRemote,
    Organisation,
    Promotion,
    Repository,
)
from apps.flatpak.models import Package
from apps.flatpak.views import (
    ExternalRefBulkActionView,
    ExternalRefDeleteView,
    ExternalRefCreateView,
    ExternalRefPullView,
    ExternalRefPublishView,
    ExternalRefPromoteView,
    ExternalRefUpdateView,
    OrganisationDeleteView,
    OrganisationUpdateView,
    PromotionListView,
    RepositoryCreateView,
    RepositoryUpdateView,
    dependencies_list,
)
from apps.users.views import ProfileView
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
    def test_global_organisation_is_seeded_and_immutable(self):
        global_org = Organisation.objects.get(name='Global')

        global_org.description = 'Changed by user'
        with self.assertRaises(ValidationError):
            global_org.save()
        with self.assertRaises(ValidationError):
            global_org.delete()
        with self.assertRaises(ValidationError):
            Organisation.objects.create(
                name='Global', responsible_name='Other', responsible_email='other@example.com',
            )

        user = User.objects.create_superuser(username='global-org-admin', password='password')
        for view_class, path in (
            (OrganisationUpdateView, f'/organisations/{global_org.pk}/edit/'),
            (OrganisationDeleteView, f'/organisations/{global_org.pk}/delete/'),
        ):
            request = RequestFactory().get(path)
            request.user = user
            view = view_class()
            view.setup(request, pk=global_org.pk)
            self.assertFalse(view.get_queryset().filter(pk=global_org.pk).exists())

    def test_bulk_delete_removes_only_selected_accessible_ownership(self):
        allowed_org = Organisation.objects.create(name='Bulk Delete Allowed')
        hidden_org = Organisation.objects.create(name='Bulk Delete Hidden')
        user = User.objects.create_user(username='bulk-external-deleter', password='password')
        PermissionGrant.objects.create(
            user=user, organisation=allowed_org, resource='externals',
            action='delete', granted=True,
        )
        repository = Repository.objects.create(name='Bulk Delete Repo')
        external = ExternalRef.objects.create(
            repository=repository, ref='app/org.example.BulkDelete/x86_64/stable',
        )
        external.organisations.add(allowed_org, hidden_org)
        request = RequestFactory().post(
            '/externals/bulk-action/',
            data=json.dumps({
                'action': 'delete', 'ids': [external.pk],
                'org_pks': [allowed_org.pk], 'delete_all': False,
            }),
            content_type='application/json',
        )
        request.user = user

        response = ExternalRefBulkActionView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content), {
            'status': 'success', 'deleted': 0, 'updated': 1,
        })
        external.refresh_from_db()
        self.assertEqual(list(external.organisations.all()), [hidden_org])

    def test_external_form_duplicate_adds_creator_default_organisation(self):
        existing_org = Organisation.objects.create(name='Existing Owner Org')
        creator_org = Organisation.objects.create(name='Form Creator Org')
        user = User.objects.create_user(
            username='external-form-creator', password='password', default_organisation=creator_org,
        )
        PermissionGrant.objects.create(
            user=user, organisation=creator_org, resource='externals',
            action='create', granted=True,
        )
        repository = Repository.objects.create(name='Form Duplicate Repo')
        remote = FlatpakRemote.objects.create(name='form-duplicate-remote', url='https://example.com/repo')
        external = ExternalRef.objects.create(
            repository=repository, remote=remote, ref='app/org.example.FormDuplicate/x86_64/stable',
        )
        external.organisations.add(existing_org)
        request = RequestFactory().post('/externals/create/', {
            'repository': repository.pk,
            'remote': remote.pk,
            'ref': external.ref,
        })
        request.user = user
        request.session = SessionStore()
        request._messages = FallbackStorage(request)

        with patch('apps.flatpak.tasks.pull_external_ref_task.delay') as task:
            response = ExternalRefCreateView.as_view()(request)

        self.assertEqual(response.status_code, 302)
        external.refresh_from_db()
        self.assertCountEqual(external.organisations.all(), [existing_org, creator_org])
        task.assert_not_called()

    def test_scoped_api_delete_only_removes_accessible_ownership(self):
        allowed_org = Organisation.objects.create(name='Delete Allowed Org')
        hidden_org = Organisation.objects.create(name='Delete Hidden Org')
        user = User.objects.create_user(username='scoped-external-deleter', password='password')
        PermissionGrant.objects.create(
            user=user, organisation=allowed_org, resource='externals',
            action='delete', granted=True,
        )
        repository = Repository.objects.create(name='Scoped Delete Repo')
        external = ExternalRef.objects.create(
            repository=repository, ref='app/org.example.Shared/x86_64/stable',
        )
        external.organisations.add(allowed_org, hidden_org)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Token ' + AuthToken.objects.create(user=user).key)

        with patch('apps.flatpak.tasks.remove_external_ref_from_repos') as remove_ref:
            response = client.delete(f'/api/external-refs/{external.pk}/')

        self.assertEqual(response.status_code, 204, response.content)
        external.refresh_from_db()
        self.assertEqual(list(external.organisations.all()), [hidden_org])
        remove_ref.assert_not_called()

    def test_detail_delete_can_remove_selected_orgs_or_delete_all(self):
        first_org = Organisation.objects.create(name='Detail Delete One')
        second_org = Organisation.objects.create(name='Detail Delete Two')
        user = User.objects.create_user(username='detail-external-deleter', password='password')
        for org in (first_org, second_org):
            PermissionGrant.objects.create(
                user=user, organisation=org, resource='externals',
                action='delete', granted=True,
            )
        repository = Repository.objects.create(name='Detail Delete Repo')
        external = ExternalRef.objects.create(
            repository=repository, ref='app/org.example.DetailDelete/x86_64/stable',
        )
        external.organisations.add(first_org, second_org)

        request = RequestFactory().post(
            f'/externals/{external.pk}/delete/',
            {'organisation_ids': [str(first_org.pk)]},
        )
        request.user = user
        request.session = SessionStore()
        request._messages = FallbackStorage(request)
        response = ExternalRefDeleteView.as_view()(request, pk=external.pk)
        self.assertEqual(response.status_code, 302)
        external.refresh_from_db()
        self.assertEqual(list(external.organisations.all()), [second_org])

        request = RequestFactory().post(
            f'/externals/{external.pk}/delete/',
            {'delete_all': 'true'},
        )
        request.user = user
        request.session = SessionStore()
        request._messages = FallbackStorage(request)
        with patch('apps.flatpak.tasks.remove_external_ref_from_repos') as remove_ref:
            response = ExternalRefDeleteView.as_view()(request, pk=external.pk)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ExternalRef.objects.filter(pk=external.pk).exists())
        remove_ref.assert_called_once()

    def test_external_promotion_targets_follow_user_organisation_scope(self):
        devops_org = Organisation.objects.create(name='Devops Promotion Org')
        infrastructure_org = Organisation.objects.create(name='Infrastructure Promotion Org')
        user = User.objects.create_user(username='scoped-promoter', password='password')
        PermissionGrant.objects.create(
            user=user, organisation=devops_org, resource='externals',
            action='read', granted=True,
        )
        PermissionGrant.objects.create(
            user=user, organisation=devops_org, resource='published_builds',
            action='promotion', granted=True,
        )

        source = Repository.objects.create(name='External Source')
        devops_repo = Repository.objects.create(name='Devops Target')
        devops_repo.organisations.add(devops_org)
        devops_repo.parent_repos.add(source)
        infrastructure_repo = Repository.objects.create(name='Infrastructure Target')
        infrastructure_repo.organisations.add(infrastructure_org)
        infrastructure_repo.parent_repos.add(source)
        source.organisations.add(devops_org)
        remote = FlatpakRemote.objects.create(name='promotion-remote', url='https://example.com/repo')
        external = ExternalRef.objects.create(
            repository=source, remote=remote, ref='app/org.example.Promote/x86_64/stable',
            status='published',
        )
        external.organisations.add(devops_org)
        version = ExternalRefVersion.objects.create(
            external_ref=external, ref=external.ref, commit_hash='a' * 64, status='published',
        )

        request = RequestFactory().get('/promotions/')
        request.user = user
        list_view = PromotionListView()
        list_view.setup(request)
        list_view.object_list = Promotion.objects.none()
        context = list_view.get_context_data()
        self.assertEqual(
            context['ready_to_promote_externals'][0]['targets'], [devops_repo],
        )

        request = RequestFactory().post(
            f'/externals/{external.pk}/promote/',
            data=f'{{"target_repo_id": {infrastructure_repo.pk}, "external_ref_version_id": {version.pk}}}',
            content_type='application/json',
        )
        request.user = user
        with patch('apps.flatpak.tasks.promote_external_ref_task.delay') as task:
            response = ExternalRefPromoteView.as_view()(request, pk=external.pk)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(task.called)

    def test_repository_create_and_edit_forms_include_organisation_choices(self):
        organisation = Organisation.objects.create(name='Repository Form Org')
        user = User.objects.create_superuser(username='repository-form-admin', password='password')

        request = RequestFactory().get('/repos/create/')
        request.user = user
        create_view = RepositoryCreateView()
        create_view.setup(request)
        create_form = create_view.get_form()
        self.assertIn('organisations', create_form.fields)
        self.assertIn(organisation, create_form.fields['organisations'].queryset)

        repository = Repository.objects.create(name='Existing Form Repo')
        request = RequestFactory().get(f'/repos/{repository.pk}/edit/')
        request.user = user
        edit_view = RepositoryUpdateView()
        edit_view.setup(request, pk=repository.pk)
        edit_view.object = repository
        edit_form = edit_view.get_form()
        self.assertIn('organisations', edit_form.fields)
        self.assertIn(organisation, edit_form.fields['organisations'].queryset)

    def test_scoped_user_can_repull_owned_external_and_denials_are_json(self):
        organisation = Organisation.objects.create(name='Pull Permission Org')
        other_organisation = Organisation.objects.create(name='Other Pull Org')
        user = User.objects.create_user(username='external-sync-user', password='password')
        PermissionGrant.objects.create(
            user=user, organisation=organisation, resource='externals',
            action='sync', granted=True,
        )
        repository = Repository.objects.create(name='Pull Permission Repo')
        remote = FlatpakRemote.objects.create(name='pull-remote', url='https://example.com/repo')
        visible_external = ExternalRef.objects.create(
            repository=repository, remote=remote, ref='app/org.example.Visible/x86_64/stable',
        )
        visible_external.organisations.add(organisation)
        hidden_external = ExternalRef.objects.create(
            repository=repository, remote=remote, ref='app/org.example.Hidden/x86_64/stable',
        )
        hidden_external.organisations.add(other_organisation)

        request = RequestFactory().post(f'/externals/{visible_external.pk}/pull/')
        request.user = user
        with patch('apps.flatpak.tasks.pull_external_ref_task.delay') as task:
            response = ExternalRefPullView.as_view()(request, pk=visible_external.pk)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/json')
        task.assert_called_once_with(visible_external.pk)

        request = RequestFactory().post(f'/externals/{hidden_external.pk}/pull/')
        request.user = user
        response = ExternalRefPullView.as_view()(request, pk=hidden_external.pk)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response['Content-Type'], 'application/json')

        request = RequestFactory().post(f'/externals/{visible_external.pk}/pull/')
        request.user = User.objects.create_user(username='no-sync-user', password='password')
        response = ExternalRefPullView.as_view()(request, pk=visible_external.pk)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response['Content-Type'], 'application/json')

    def test_scoped_user_can_publish_owned_external(self):
        organisation = Organisation.objects.create(name='Publish Permission Org')
        user = User.objects.create_user(username='external-publish-user', password='password')
        PermissionGrant.objects.create(
            user=user, organisation=organisation, resource='externals',
            action='publish', granted=True,
        )
        repository = Repository.objects.create(name='Publish Permission Repo')
        external = ExternalRef.objects.create(
            repository=repository, ref='app/org.example.Publish/x86_64/stable', status='pulled',
        )
        external.organisations.add(organisation)

        request = RequestFactory().post(f'/externals/{external.pk}/publish/')
        request.user = user
        with patch('apps.flatpak.tasks.publish_external_ref_task.delay') as task:
            response = ExternalRefPublishView.as_view()(request, pk=external.pk)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/json')
        task.assert_called_once_with(external.pk)

    def test_dependencies_page_only_uses_visible_packages_and_externals(self):
        visible_org = Organisation.objects.create(name='Visible Dependency Org')
        hidden_org = Organisation.objects.create(name='Hidden Dependency Org')
        user = User.objects.create_user(username='dependency-reader', password='password')
        for resource in ('flatpaks', 'externals'):
            PermissionGrant.objects.create(
                user=user, organisation=visible_org, resource=resource,
                action='read', granted=True,
            )

        repository = Repository.objects.create(name='Dependency Scope Repo')
        visible_package = Package.objects.create(
            repository=repository, package_id='org.example.Visible', package_name='Visible App',
            dependencies={'runtime_full': 'runtime/org.example.VisibleRuntime/x86_64/stable'},
        )
        visible_package.organisations.add(visible_org)
        hidden_package = Package.objects.create(
            repository=repository, package_id='org.example.Hidden', package_name='Hidden App',
            dependencies={'runtime_full': 'runtime/org.example.HiddenRuntime/x86_64/stable'},
        )
        hidden_package.organisations.add(hidden_org)

        visible_external = ExternalRef.objects.create(
            repository=repository, ref='runtime/org.example.VisibleExternal/x86_64/stable',
            dependencies={'all': ['runtime/org.example.ExternalDependency/x86_64/stable']},
        )
        visible_external.organisations.add(visible_org)
        hidden_external = ExternalRef.objects.create(
            repository=repository, ref='runtime/org.example.HiddenExternal/x86_64/stable',
            dependencies={'all': ['runtime/org.example.HiddenExternalDependency/x86_64/stable']},
        )
        hidden_external.organisations.add(hidden_org)

        request = RequestFactory().get('/dependencies/')
        request.user = user
        with patch('apps.flatpak.views.render', side_effect=lambda _request, _template, context: context), \
                patch('subprocess.run', return_value=type('Result', (), {'returncode': 1, 'stdout': ''})()):
            context = dependencies_list(request)

        missing_refs = {item['full_ref'] for item in context['missing_deps']}
        self.assertIn('runtime/org.example.VisibleRuntime/x86_64/stable', missing_refs)
        self.assertIn('runtime/org.example.ExternalDependency/x86_64/stable', missing_refs)
        self.assertNotIn('runtime/org.example.HiddenRuntime/x86_64/stable', missing_refs)
        self.assertNotIn('runtime/org.example.HiddenExternalDependency/x86_64/stable', missing_refs)

    def test_external_ownership_editor_respects_update_organisation_scope(self):
        allowed_org = Organisation.objects.create(name='Allowed External Org')
        other_org = Organisation.objects.create(name='Other External Org')
        user = User.objects.create_user(username='external-owner', password='password')
        PermissionGrant.objects.create(
            user=user, organisation=allowed_org, resource='externals',
            action='update', granted=True,
        )
        repository = Repository.objects.create(name='External Ownership Repo')
        remote = FlatpakRemote.objects.create(name='ownership-remote', url='https://example.com/repo')
        allowed_external = ExternalRef.objects.create(
            repository=repository, remote=remote, ref='app/org.example.Allowed/x86_64/stable',
        )
        allowed_external.organisations.add(allowed_org)
        other_external = ExternalRef.objects.create(
            repository=repository, remote=remote, ref='app/org.example.Other/x86_64/stable',
        )
        other_external.organisations.add(other_org)

        request = RequestFactory().get(f'/externals/{allowed_external.pk}/edit/')
        request.user = user
        view = ExternalRefUpdateView()
        view.setup(request, pk=allowed_external.pk)
        self.assertQuerySetEqual(view.get_queryset(), [allowed_external])

        view.object = allowed_external
        context = view.get_context_data()
        self.assertEqual(list(context['all_organisations']), [allowed_org])

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

    def test_permission_scope_reports_allowed_organisations(self):
        org = Organisation.objects.create(
            name='Scoped Org',
            responsible_name='Alice',
            responsible_email='alice@example.com',
        )
        user = User.objects.create_user(username='scoped-user', password='password')
        PermissionGrant.objects.create(
            user=user,
            organisation=org,
            resource='flatpaks',
            action='read',
            granted=True,
        )

        self.assertEqual(user.permission_organisation_ids('flatpaks', 'read'), {org.pk})
        self.assertTrue(user.can_view_flatpaks())

    def test_global_permission_scope_is_unrestricted(self):
        user = User.objects.create_user(username='global-user', password='password')
        PermissionGrant.objects.create(
            user=user,
            resource='flatpaks',
            action='read',
            granted=True,
        )

        self.assertIsNone(user.permission_organisation_ids('flatpaks', 'read'))

    def test_default_organisation_is_available_for_new_items(self):
        org = Organisation.objects.create(
            name='Default Org',
            responsible_name='Alice',
            responsible_email='alice@example.com',
        )
        user = User.objects.create_user(
            username='default-org-user', password='password', default_organisation=org,
        )
        repository = Repository.objects.create(name='Default Repo', created_by=user)

        package = Package.objects.create(
            repository=repository,
            package_id='org.example.App',
            package_name='Example App',
            created_by=user,
        )
        from apps.users.mixins import apply_default_organisation
        apply_default_organisation(package, user)

        self.assertIn(org, package.organisations.all())

    def test_admin_created_repository_gets_creator_default_organisation(self):
        org = Organisation.objects.create(name='Admin Default Org')
        user = User.objects.create_superuser(
            username='admin-default-org', password='password', default_organisation=org,
        )
        request = RequestFactory().post('/admin/flatpak/repository/add/')
        request.user = user
        model_admin = admin.site._registry[Repository]
        form_class = model_admin.get_form(request)
        form = form_class(data={
            'name': 'Admin Default Repo',
            'collection_id': '',
            'description': '',
            'gpg_key': '',
            'parent_repos': [],
            'created_by': user.pk,
        })
        self.assertTrue(form.is_valid(), form.errors)
        repository = form.save(commit=False)
        model_admin.save_model(request, repository, form, change=False)
        model_admin.save_related(request, form, [], change=False)

        repository = Repository.objects.get(name='Admin Default Repo')
        self.assertEqual(repository.created_by, user)
        self.assertEqual(list(repository.organisations.all()), [org])


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
        self.assertIn('promote', {value for value, _ in RESOURCE_ACTIONS['builds']})
        self.assertIn('commit', {value for value, _ in RESOURCE_ACTIONS['flatpaks']})
        self.assertIn('publish', {value for value, _ in RESOURCE_ACTIONS['externals']})
        self.assertIn('promote', {value for value, _ in RESOURCE_ACTIONS['externals']})
        self.assertEqual(
            {'commit', 'externals', 'promotion', 'publish', 'all'},
            {value for value, _ in RESOURCE_ACTIONS['published_builds']},
        )
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


class ProfileApiTokenTests(TestCase):
    """The Django test client cannot render templates on Python 3.14, so these
    tests drive ProfileView through RequestFactory instead."""

    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(
            username='tokenuser', email='t@example.com', password='pw12345678')

    def _request(self, method, data=None):
        request = getattr(self.factory, method)('/profile/', data or {})
        request.user = self.user
        request.session = SessionStore()
        request._messages = FallbackStorage(request)
        return ProfileView.as_view()(request)

    def test_profile_offers_creation_when_no_token(self):
        response = self._request('get')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Create API Token', response.content.decode())

    def test_create_then_shows_token_and_curl_example(self):
        self._request('post', {'action': 'create_api_token'})
        token = AuthToken.objects.get(user=self.user)
        content = self._request('get').content.decode()
        self.assertIn(token.key, content)
        self.assertIn('Authorization: Token $FLATMAN_TOKEN', content)

    def test_create_is_idempotent(self):
        self._request('post', {'action': 'create_api_token'})
        first = AuthToken.objects.get(user=self.user).key
        self._request('post', {'action': 'create_api_token'})
        self.assertEqual(AuthToken.objects.filter(user=self.user).count(), 1)
        self.assertEqual(AuthToken.objects.get(user=self.user).key, first)

    def test_regenerate_replaces_the_key(self):
        self._request('post', {'action': 'create_api_token'})
        first = AuthToken.objects.get(user=self.user).key
        self._request('post', {'action': 'regenerate_api_token'})
        self.assertEqual(AuthToken.objects.filter(user=self.user).count(), 1)
        self.assertNotEqual(AuthToken.objects.get(user=self.user).key, first)

    def test_revoke_deletes_the_token(self):
        self._request('post', {'action': 'create_api_token'})
        self._request('post', {'action': 'revoke_api_token'})
        self.assertFalse(AuthToken.objects.filter(user=self.user).exists())

    def test_token_authenticates_against_the_api(self):
        self._request('post', {'action': 'create_api_token'})
        key = AuthToken.objects.get(user=self.user).key
        api = APIClient()
        self.assertEqual(api.get('/api/repositories/').status_code, 401)
        api.credentials(HTTP_AUTHORIZATION='Token ' + key)
        self.assertEqual(api.get('/api/repositories/').status_code, 200)

    def test_profile_update_still_works(self):
        self._request('post', {'bio': 'hello', 'phone': '', 'organization': ''})
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.bio, 'hello')
