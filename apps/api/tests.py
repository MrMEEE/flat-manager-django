from unittest.mock import patch

from django.test import TestCase
from rest_framework.authtoken.models import Token as AuthToken
from rest_framework.test import APIClient

from apps.flatpak.models import (
    AppMetadata, AppUsageObservation, Build, Client, GPGKey, Package, Repository,
    ExternalRef, ExternalRefVersion, ExternalRefPromotion, FlatpakRemote, Promotion, Organisation,
)
from apps.users.models import (
    PermissionGrant, PermissionGroup, PermissionGroupPermission, User, UserProfile,
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


class TokenWorkflowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(username='workflow', password='test')
        self.client = APIClient()
        self.client.credentials(
            HTTP_AUTHORIZATION='Token ' + AuthToken.objects.create(user=self.user).key)
        self.source = Repository.objects.create(name='source')
        self.target = Repository.objects.create(name='target')
        self.target.parent_repos.add(self.source)
        self.package = Package.objects.create(
            repository=self.source, package_id='org.example.App', package_name='App')
        self.build = Build.objects.create(package=self.package, build_number=1, status='published')
        self.remote = FlatpakRemote.objects.create(name='upstream', url='https://example.com/repo')
        self.external = ExternalRef.objects.create(
            repository=self.source, remote=self.remote,
            ref='app/org.example.External/x86_64/stable', status='published')
        self.version = ExternalRefVersion.objects.create(
            external_ref=self.external, ref=self.external.ref,
            commit_hash='a' * 64, status='published')

    @patch('apps.flatpak.tasks.pull_external_ref_task.delay')
    def test_pull_external_with_token(self, task):
        response = self.client.post(f'/api/external-refs/{self.external.pk}/pull/', {}, format='json')
        self.assertEqual(response.status_code, 202, response.content)
        task.assert_called_once_with(self.external.pk)
        self.external.refresh_from_db()
        self.assertEqual(self.external.status, 'pending')

    @patch('apps.flatpak.tasks.publish_external_ref_task.delay')
    def test_publish_external_with_token(self, task):
        self.external.status = 'pulled'
        self.external.save()
        response = self.client.post(f'/api/external-refs/{self.external.pk}/publish/', {}, format='json')
        self.assertEqual(response.status_code, 202, response.content)
        task.assert_called_once_with(self.external.pk)

    @patch('apps.flatpak.tasks.promote_build_task.delay')
    def test_promote_build_with_token(self, task):
        response = self.client.post(
            f'/api/builds/{self.build.pk}/promote/',
            {'target_repo_id': self.target.pk}, format='json')
        self.assertEqual(response.status_code, 202, response.content)
        promotion = Promotion.objects.get(pk=response.json()['promotion_id'])
        self.assertEqual(promotion.build, self.build)
        self.assertEqual(promotion.target_repo, self.target)
        self.assertEqual(promotion.promoted_by, self.user)
        task.assert_called_once_with(promotion.pk)

    @patch('apps.flatpak.tasks.promote_external_ref_task.delay')
    def test_promote_external_version_with_token(self, task):
        response = self.client.post(
            f'/api/external-refs/{self.external.pk}/promote/',
            {'target_repo_id': self.target.pk, 'external_ref_version_id': self.version.pk},
            format='json')
        self.assertEqual(response.status_code, 202, response.content)
        promotion = ExternalRefPromotion.objects.get(pk=response.json()['promotion_id'])
        self.assertEqual(promotion.external_ref_version, self.version)
        self.assertEqual(promotion.promoted_by, self.user)
        task.assert_called_once_with(promotion.pk)

    def test_anonymous_requests_require_token(self):
        self.client.credentials()
        for path in (
            f'/api/external-refs/{self.external.pk}/pull/',
            f'/api/external-refs/{self.external.pk}/publish/',
            f'/api/external-refs/{self.external.pk}/promote/',
            f'/api/builds/{self.build.pk}/promote/',
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.post(path, {}, format='json').status_code, 401)

    def test_read_only_token_cannot_dispatch_workflows(self):
        user = User.objects.create_user(username='viewer')
        self.client.credentials(
            HTTP_AUTHORIZATION='Token ' + AuthToken.objects.create(user=user).key)
        with patch('apps.flatpak.tasks.pull_external_ref_task.delay') as pull, \
                patch('apps.flatpak.tasks.publish_external_ref_task.delay') as publish, \
                patch('apps.flatpak.tasks.promote_build_task.delay') as promote, \
                patch('apps.flatpak.tasks.promote_external_ref_task.delay') as promote_external:
            for path in (
                f'/api/external-refs/{self.external.pk}/pull/',
                f'/api/external-refs/{self.external.pk}/publish/',
                f'/api/external-refs/{self.external.pk}/promote/',
                f'/api/builds/{self.build.pk}/promote/',
            ):
                with self.subTest(path=path):
                    response = self.client.post(path, {'target_repo_id': self.target.pk}, format='json')
                    self.assertEqual(response.status_code, 403, response.content)
            for task in (pull, publish, promote, promote_external):
                task.assert_not_called()
        self.assertFalse(Promotion.objects.exists())
        self.assertFalse(ExternalRefPromotion.objects.exists())

    @patch('apps.flatpak.tasks.promote_build_task.delay')
    def test_build_promotion_validates_state_target_and_duplicates(self, task):
        path = f'/api/builds/{self.build.pk}/promote/'
        for payload in ({}, {'target_repo_id': 'bad'}, {'target_repo_id': []},
                        {'target_repo_id': self.source.pk}):
            with self.subTest(payload=payload):
                self.assertEqual(self.client.post(path, payload, format='json').status_code, 400)
        self.build.status = 'built'
        self.build.save()
        self.assertEqual(self.client.post(
            path, {'target_repo_id': self.target.pk}, format='json').status_code, 400)
        self.assertFalse(Promotion.objects.exists())
        task.assert_not_called()
        self.build.status = 'published'
        self.build.save()
        self.assertEqual(self.client.post(
            path, {'target_repo_id': self.target.pk}, format='json').status_code, 202)
        self.assertEqual(self.client.post(
            path, {'target_repo_id': self.target.pk}, format='json').status_code, 400)
        self.assertEqual(Promotion.objects.count(), 1)
        self.assertEqual(task.call_count, 1)

    @patch('apps.flatpak.tasks.promote_build_task.delay')
    def test_build_promotion_enforces_parent_chain(self, task):
        downstream = Repository.objects.create(name='downstream')
        downstream.parent_repos.add(self.target)
        path = f'/api/builds/{self.build.pk}/promote/'
        self.assertEqual(self.client.post(
            path, {'target_repo_id': downstream.pk}, format='json').status_code, 400)
        task.assert_not_called()
        Promotion.objects.create(
            build=self.build, package=self.package, target_repo=self.target, status='promoted')
        self.assertEqual(self.client.post(
            path, {'target_repo_id': downstream.pk}, format='json').status_code, 202)

    @patch('apps.flatpak.tasks.promote_build_task.delay')
    def test_failed_build_promotion_can_be_queued_again(self, task):
        promotion = Promotion.objects.create(
            build=self.build, package=self.package, target_repo=self.target,
            status='failed', error_message='Previous attempt failed')
        response = self.client.post(
            f'/api/builds/{self.build.pk}/promote/',
            {'target_repo_id': self.target.pk}, format='json')
        self.assertEqual(response.status_code, 202, response.content)
        self.assertEqual(response.json()['promotion_id'], promotion.pk)
        promotion.refresh_from_db()
        self.assertEqual(promotion.status, 'pending')
        self.assertEqual(promotion.error_message, '')
        task.assert_called_once_with(promotion.pk)

    @patch('apps.flatpak.tasks.promote_external_ref_task.delay')
    def test_external_promotion_requires_published_version_of_same_ref(self, task):
        other = ExternalRef.objects.create(repository=self.source, ref='app/other/x86_64/stable')
        other_version = ExternalRefVersion.objects.create(
            external_ref=other, ref=other.ref, commit_hash='b' * 64, status='published')
        path = f'/api/external-refs/{self.external.pk}/promote/'
        for version_id in ('bad', [], other_version.pk):
            with self.subTest(version_id=version_id):
                response = self.client.post(path, {
                    'target_repo_id': self.target.pk, 'external_ref_version_id': version_id,
                }, format='json')
                self.assertIn(response.status_code, (400, 404), response.content)
        self.version.status = 'pulled'
        self.version.save()
        self.assertEqual(self.client.post(path, {
            'target_repo_id': self.target.pk, 'external_ref_version_id': self.version.pk,
        }, format='json').status_code, 404)
        self.assertEqual(self.client.post(
            path, {'target_repo_id': self.target.pk}, format='json').status_code, 400)
        task.assert_not_called()
        self.assertFalse(ExternalRefPromotion.objects.exists())

    @patch('apps.flatpak.tasks.promote_external_ref_task.delay')
    def test_external_promotion_defaults_to_latest_published_version(self, task):
        latest = ExternalRefVersion.objects.create(
            external_ref=self.external, ref=self.external.ref,
            commit_hash='b' * 64, status='published')
        ExternalRefVersion.objects.create(
            external_ref=self.external, ref=self.external.ref, commit_hash='c' * 64)
        ExternalRefPromotion.objects.create(
            external_ref=self.external, external_ref_version=self.version,
            target_repo=self.target, status='promoted')
        path = f'/api/external-refs/{self.external.pk}/promote/'
        response = self.client.post(path, {'target_repo_id': self.target.pk}, format='json')
        self.assertEqual(response.status_code, 202, response.content)
        promotion = ExternalRefPromotion.objects.get(pk=response.json()['promotion_id'])
        self.assertEqual(promotion.external_ref_version, latest)
        self.assertEqual(self.client.post(
            path, {'target_repo_id': self.target.pk}, format='json').status_code, 400)
        self.assertEqual(task.call_count, 1)

    @patch('apps.flatpak.tasks.pull_external_ref_task.delay')
    @patch('apps.flatpak.tasks.publish_external_ref_task.delay')
    def test_external_pull_publish_reject_invalid_states(self, publish, pull):
        for state in ('pulling', 'publishing'):
            self.external.status = state
            self.external.save()
            for action_name in ('pull', 'publish'):
                with self.subTest(state=state, action=action_name):
                    response = self.client.post(
                        f'/api/external-refs/{self.external.pk}/{action_name}/', {}, format='json')
                    self.assertEqual(response.status_code, 400, response.content)
        self.external.status = 'pending'
        self.external.remote = None
        self.external.save()
        self.assertEqual(self.client.post(
            f'/api/external-refs/{self.external.pk}/pull/', {}, format='json').status_code, 400)
        pull.assert_not_called()
        publish.assert_not_called()

    @patch('apps.flatpak.tasks.promote_build_task.delay')
    @patch('apps.flatpak.tasks.promote_external_ref_task.delay')
    def test_promotion_status_can_be_polled(self, promote_external, promote):
        for path, detail_path in (
            (f'/api/builds/{self.build.pk}/promote/', '/api/promotions/'),
            (f'/api/external-refs/{self.external.pk}/promote/', '/api/external-ref-promotions/'),
        ):
            with self.subTest(path=path):
                response = self.client.post(path, {'target_repo_id': self.target.pk}, format='json')
                self.assertEqual(response.status_code, 202, response.content)
                polled = self.client.get(f"{detail_path}{response.json()['promotion_id']}/")
                self.assertEqual(polled.status_code, 200, polled.content)
                self.assertEqual(polled.json()['status'], 'pending')
        external = self.client.get(f'/api/external-refs/{self.external.pk}/')
        self.assertEqual(external.status_code, 200, external.content)
        self.assertEqual(external.json()['versions'][0]['id'], self.version.pk)

    @patch('apps.flatpak.tasks.promote_build_task.delay')
    @patch('apps.flatpak.tasks.promote_external_ref_task.delay')
    @patch('apps.flatpak.tasks.pull_external_ref_task.delay')
    @patch('apps.flatpak.tasks.publish_external_ref_task.delay')
    def test_organisation_grants_cannot_modify_other_organisations(self, publish, pull, promote_external, promote):
        organisation = Organisation.objects.create(name='Allowed')
        other = Organisation.objects.create(name='Other')
        user = User.objects.create_user(username='scoped')
        for resource, action_name in (
            ('externals', 'sync'), ('published_builds', 'externals'),
            ('published_builds', 'promotion'),
        ):
            PermissionGrant.objects.create(
                user=user, resource=resource, action=action_name,
                granted=True, organisation=organisation)
        self.package.organisations.add(other)
        self.external.organisations.add(other)
        self.client.credentials(
            HTTP_AUTHORIZATION='Token ' + AuthToken.objects.create(user=user).key)
        for path in (
            f'/api/external-refs/{self.external.pk}/pull/',
            f'/api/external-refs/{self.external.pk}/publish/',
            f'/api/external-refs/{self.external.pk}/promote/',
            f'/api/builds/{self.build.pk}/promote/',
        ):
            with self.subTest(path=path):
                response = self.client.post(path, {'target_repo_id': self.target.pk}, format='json')
                self.assertEqual(response.status_code, 404, response.content)
        for task in (publish, pull, promote_external, promote):
            task.assert_not_called()
        self.package.organisations.add(organisation)
        self.external.organisations.add(organisation)
        response = self.client.post(
            f'/api/builds/{self.build.pk}/promote/', {'target_repo_id': self.target.pk}, format='json')
        self.assertEqual(response.status_code, 202, response.content)
        response = self.client.post(
            f'/api/external-refs/{self.external.pk}/pull/', {}, format='json')
        self.assertEqual(response.status_code, 202, response.content)

