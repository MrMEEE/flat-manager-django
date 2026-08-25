from django.test import TestCase
from rest_framework.test import APIClient

from apps.flatpak.models import AppMetadata, AppUsageObservation, Package, Repository
from apps.users.models import User


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
        self.assertEqual(
            AppUsageObservation.objects.filter(app_id=self.published.package_id).count(),
            1,
        )
        usage = self.client.get('/api/v2/usage/popular')
        self.assertEqual(usage.json()['apps'][0]['app_id'], self.published.package_id)

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