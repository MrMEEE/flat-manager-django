from unittest.mock import patch

from django.test import TestCase
from rest_framework.authtoken.models import Token as AuthToken
from rest_framework.test import APIClient

from apps.flatpak.models import (
    AppMetadata, AppUsageObservation, Build, Client, GPGKey, Package, Repository,
)
from apps.users.models import (
    PermissionGroup, PermissionGroupPermission, User, UserProfile,
)


class FlathubPublicAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.repository = Repository.objects.create(name='Public')
        self.published = Package.objects.create(
            repository=self.repository,
            package_id='org.example.App',
            package_name='Example App',
            status='published',
            version='1.2.3',
        )
        Package.objects.create(
            repository=self.repository,
            package_id='org.example.Draft',
            package_name='Draft App',
            status='pending',
        )

    def test_status_is_public(self):
        response = self.client.get('/api/v2/status')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok'})

    def test_appstream_only_returns_published_packages(self):
        response = self.client.get('/api/v2/appstream')

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['id'] for item in response.json()], ['org.example.App'])

    def test_appstream_detail_and_summary(self):
        appstream = self.client.get('/api/v2/appstream/org.example.App')
        summary = self.client.get('/api/v2/summary/org.example.App')

        self.assertEqual(appstream.status_code, 200)
        self.assertEqual(appstream.json()['version'], '1.2.3')
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.json()['id'], 'org.example.App')

    def test_appstream_uses_persisted_metadata(self):
        AppMetadata.objects.create(
            package=self.published,
            summary='A useful example',
            developer_name='Example Team',
            categories=['Utility'],
            keywords=['example'],
        )

        response = self.client.get('/api/v2/appstream/org.example.App')
        developers = self.client.get('/api/v2/collection/developer')
        category = self.client.get('/api/v2/collection/category/Utility')

        self.assertEqual(response.json()['summary'], 'A useful example')
        self.assertEqual(developers.json(), ['Example Team'])
        self.assertEqual(category.json()[0]['id'], 'org.example.App')

    def test_search_requires_a_query_and_finds_apps(self):
        missing = self.client.post('/api/v2/search', {}, format='json')
        response = self.client.post('/api/v2/search', {'query': 'Example'}, format='json')

        self.assertEqual(missing.status_code, 400)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]['id'], 'org.example.App')

    def test_app_supporting_endpoints(self):
        stats = self.client.get('/api/v2/stats/org.example.App')
        platforms = self.client.get('/api/v2/platforms')
        runtimes = self.client.get('/api/v2/runtimes')
        fullscreen = self.client.get('/api/v2/is-fullscreen-app/org.example.App')
        addons = self.client.get('/api/v2/addon/org.example.App')

        self.assertEqual(stats.status_code, 200)
        self.assertEqual(stats.json()['app_id'], 'org.example.App')
        self.assertEqual(platforms.json(), ['x86_64'])
        self.assertEqual(runtimes.json(), [])
        self.assertEqual(fullscreen.json(), False)
        self.assertEqual(addons.json(), [])

    def test_client_checkin_records_installation_usage(self):
        response = self.client.post(
            '/api/client-checkin/',
            {
                'hostname': 'usage-client',
                'installed': [
                    {'app_id': self.published.package_id, 'version': '1.2.3'},
                    {'app_id': self.published.package_id, 'version': '1.2.3'},
                ],
            },
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['lockdown'], False)
        self.assertEqual(
            AppUsageObservation.objects.filter(app_id=self.published.package_id).count(),
            1,
        )
        usage = self.client.get('/api/v2/usage/popular')
        self.assertEqual(usage.json()['apps'][0]['app_id'], self.published.package_id)

    def test_client_checkin_returns_lockdown_directive(self):
        Client.objects.create(hostname='locked-client', lockdown=True)

        response = self.client.post(
            '/api/client-checkin/',
            {'hostname': 'locked-client', 'installed': []},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['lockdown'], True)

    def test_logged_in_user_can_toggle_client_lockdown(self):
        user = User.objects.create_user(username='operator', password='password')
        group = PermissionGroup.objects.create(name='Client Operators')
        PermissionGroupPermission.objects.create(group=group, resource='clients', action='update')
        group.users.add(user)
        client = Client.objects.create(hostname='toggle-client')
        self.client.force_login(user)

        locked = self.client.post(
            f'/clients/{client.pk}/lockdown/',
            {'lockdown': True},
            format='json',
        )
        client.refresh_from_db()

        self.assertEqual(locked.status_code, 200)
        self.assertEqual(locked.json()['lockdown'], True)
        self.assertEqual(client.lockdown, True)

        unlocked = self.client.post(
            f'/clients/{client.pk}/lockdown/',
            {'lockdown': False},
            format='json',
        )
        client.refresh_from_db()

        self.assertEqual(unlocked.status_code, 200)
        self.assertEqual(unlocked.json()['lockdown'], False)
        self.assertEqual(client.lockdown, False)

    def test_missing_app_returns_not_found(self):
        response = self.client.get('/api/v2/appstream/org.example.Missing')

        self.assertEqual(response.status_code, 404)


class FlathubWorkflowAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username='admin', password='password')
        self.user.is_staff = True
        self.user.save(update_fields=['is_staff'])
        repository = Repository.objects.create(name='Workflow')
        self.package = Package.objects.create(
            repository=repository,
            package_id='org.example.Workflow',
            package_name='Workflow App',
            status='published',
        )

    def test_moderation_submission_requires_admin(self):
        user = User.objects.create_user(username='member', password='password')
        self.client.force_authenticate(user)
        response = self.client.post(
            '/api/v2/moderation/submit_review_request',
            {'app_id': self.package.package_id},
            format='json',
        )
        self.assertEqual(response.status_code, 403)

    def test_admin_can_submit_and_review_moderation_request(self):
        self.client.force_authenticate(self.user)
        submitted = self.client.post(
            '/api/v2/moderation/submit_review_request',
            {'app_id': self.package.package_id, 'reason': 'Ready for review'},
            format='json',
        )
        self.assertEqual(submitted.status_code, 201)

        reviewed = self.client.post(
            f"/api/v2/moderation/requests/{submitted.json()['id']}/review",
            {'status': 'approved', 'comment': 'Looks good'},
            format='json',
        )
        self.assertEqual(reviewed.status_code, 200)
        self.assertEqual(reviewed.json()['status'], 'approved')

    def test_admin_can_create_and_read_app_pick(self):
        self.client.force_authenticate(self.user)
        created = self.client.post(
            '/api/v2/app-picks/admin/curated-app-selections',
            {
                'app_id': self.package.package_id,
                'kind': 'day',
                'date': '2026-08-25',
                'title': "Today's pick",
            },
            format='json',
        )
        self.assertEqual(created.status_code, 201)

        public = self.client.get('/api/v2/app-picks/app-of-the-day/2026-08-25')
        self.assertEqual(public.status_code, 200)
        self.assertEqual(public.json()[0]['app_id'], self.package.package_id)

        admin_list = self.client.get('/api/v2/app-picks/admin/curated-app-selections')
        self.assertEqual(admin_list.status_code, 200)
        self.assertEqual(admin_list.json()[0]['app_id'], self.package.package_id)

        current = self.client.post(
            '/api/v2/app-picks/app-of-the-week',
            {'app_id': self.package.package_id, 'date': '2026-08-25'},
            format='json',
        )
        self.assertEqual(current.status_code, 201)


class APIRegressionTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username='admin', email='a@example.com', password='x')
        self.client = APIClient()
        self.client.credentials(
            HTTP_AUTHORIZATION='Token ' + AuthToken.objects.create(user=self.admin).key)
        self.repo = Repository.objects.create(name='stable', collection_id='org.example.Stable')
        self.package = Package.objects.create(
            repository=self.repo, package_id='org.example.App', package_name='App',
            git_repo_url='https://example.com/app.git', status='pending')

    def test_subset_create(self):
        r = self.client.post('/api/repository-subsets/', {
            'name': 'apps', 'collection_id': 'org.example.Stable.Apps',
            'base_url': 'https://cdn.example.com', 'repository_id': self.repo.id}, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(self.repo.subsets.count(), 1)

    def test_artifact_create(self):
        build = Build.objects.create(package=self.package, build_number=1, status='built')
        r = self.client.post('/api/artifacts/', {
            'filename': 'app.flatpak', 'file_path': '/tmp/app.flatpak',
            'file_size': 1, 'checksum': 'abc', 'build_id': build.id}, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(build.artifacts.count(), 1)

    def test_token_create_generates_value(self):
        r = self.client.post('/api/tokens/', {
            'name': 'ci-upload', 'token_type': 'upload', 'repository_id': self.repo.id},
            format='json')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(len(r.json()['token']), 64)
        r2 = self.client.post('/api/tokens/', {
            'name': 'ci-two', 'token_type': 'upload', 'repository_id': self.repo.id},
            format='json')
        self.assertEqual(r2.status_code, 201, r2.content)
        self.assertNotEqual(r.json()['token'], r2.json()['token'])

    def test_profile_exposes_id_and_user_id(self):
        r = self.client.get('/api/profiles/')
        self.assertEqual(r.status_code, 200, r.content)
        results = r.json().get('results', r.json())
        self.assertTrue(results, 'signal should have created a profile')
        self.assertIn('id', results[0])
        pk = results[0]['id']
        r2 = self.client.patch('/api/profiles/%s/' % pk, {'bio': 'hello'}, format='json')
        self.assertEqual(r2.status_code, 200, r2.content)
        self.assertEqual(UserProfile.objects.get(pk=pk).bio, 'hello')

    def test_profile_create_for_user_without_one(self):
        user = User.objects.create(username='noprofile')
        UserProfile.objects.filter(user=user).delete()
        r = self.client.post('/api/profiles/', {'user_id': user.id, 'bio': 'new'}, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        # Duplicate must be a 400, not a 500.
        r2 = self.client.post('/api/profiles/', {'user_id': user.id}, format='json')
        self.assertEqual(r2.status_code, 400, r2.content)

    def test_package_logs_returns_latest_build(self):
        build = Build.objects.create(package=self.package, build_number=1, status='built')
        build.logs.create(message='hello', level='info')
        anon = APIClient()
        r = anon.get('/api/packages/%s/logs/' % self.package.id)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['build_id'], build.id)
        self.assertEqual(r.json()['total_logs'], 1)

    def test_package_logs_without_builds(self):
        anon = APIClient()
        r = anon.get('/api/packages/%s/logs/' % self.package.id)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['total_logs'], 0)

    def test_commit_and_publish_do_not_raise(self):
        with patch('apps.flatpak.tasks.commit_package_task.delay') as commit, \
                patch('apps.flatpak.tasks.publish_package_task.delay') as publish:
            r = self.client.post('/api/packages/%s/commit/' % self.package.id, format='json')
            self.assertEqual(r.status_code, 200, r.content)
            commit.assert_called_once_with(self.package.id)
            self.package.status = 'committed'
            self.package.save()
            r2 = self.client.post('/api/packages/%s/publish/' % self.package.id, format='json')
            self.assertEqual(r2.status_code, 200, r2.content)
            publish.assert_called_once_with(self.package.id)

    def test_repository_build_count_and_builds_action(self):
        Build.objects.create(package=self.package, build_number=1, status='built')
        r = self.client.get('/api/repositories/%s/' % self.repo.id)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['build_count'], 1)
        r2 = self.client.get('/api/repositories/%s/builds/' % self.repo.id)
        self.assertEqual(r2.status_code, 200, r2.content)
        self.assertEqual(len(r2.json()), 1)

    def test_cancel_rejects_published(self):
        self.package.status = 'published'
        self.package.save()
        r = self.client.post('/api/packages/%s/cancel/' % self.package.id, format='json')
        self.assertEqual(r.status_code, 400, r.content)

    def test_git_branches_rejects_injection(self):
        r = self.client.get('/api/git-branches/', {'repo_url': "'; touch /tmp/pwned_flatman; '"})
        self.assertEqual(r.status_code, 200)
        import os
        self.assertFalse(os.path.exists('/tmp/pwned_flatman'))

    def test_gpg_key_unused_import_still_fine(self):
        GPGKey.objects.create(name='k', email='k@example.com', key_id='1', fingerprint='f',
                              public_key='p', private_key='s')
        r = self.client.get('/api/gpg-keys/')
        self.assertEqual(r.status_code, 200, r.content)

