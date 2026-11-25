import os
import shutil
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.flatpak.models import GPGKey, Repository

User = get_user_model()


class RepositoryUpdateGPGKeyTests(TestCase):
    """Changing a repository's GPG key in the edit form must update the on-disk
    public key file, the OSTree signing config and re-sign the repository."""

    def setUp(self):
        self.repos_base = tempfile.mkdtemp(prefix='fmd-test-repos-')
        self.addCleanup(shutil.rmtree, self.repos_base, ignore_errors=True)
        settings_override = override_settings(REPOS_BASE_PATH=self.repos_base)
        settings_override.enable()
        self.addCleanup(settings_override.disable)

        self.admin = User.objects.create_superuser(username='repo-admin', password='test')
        self.client.force_login(self.admin)
        self.key_a = GPGKey.objects.create(
            name='Key A', email='a@example.com', key_id='AAAAAAAAAAAAAAAA',
            fingerprint='A' * 40, public_key='PUBLIC KEY A', private_key='secret a')
        self.key_b = GPGKey.objects.create(
            name='Key B', email='b@example.com', key_id='BBBBBBBBBBBBBBBB',
            fingerprint='B' * 40, public_key='PUBLIC KEY B', private_key='secret b')
        self.repo = Repository.objects.create(name='Test Repo')
        os.makedirs(self.repo.repo_path)
        self.key_file = os.path.join(self.repos_base, 'Test-Repo.gpg')

    def _post_edit(self, gpg_key):
        return self.client.post(f'/repos/{self.repo.pk}/edit/', {
            'name': self.repo.name,
            'collection_id': '',
            'description': '',
            'gpg_key': gpg_key.pk if gpg_key else '',
        })

    def _read_key_file(self):
        with open(self.key_file) as fh:
            return fh.read()

    @patch('apps.flatpak.views.update_repo_metadata', return_value={'success': True})
    @patch('apps.flatpak.views.subprocess.run')
    def test_assigning_key_writes_public_key_and_resigns(self, run, update_metadata):
        response = self._post_edit(self.key_a)

        self.assertEqual(response.status_code, 302, response.content)
        self.repo.refresh_from_db()
        self.assertEqual(self.repo.gpg_key, self.key_a)
        self.assertEqual(self._read_key_file(), 'PUBLIC KEY A')
        run.assert_any_call(
            ['ostree', 'config', 'set', '--repo', self.repo.repo_path,
             'core.gpg-sign', self.key_a.key_id],
            capture_output=True, text=True,
        )
        update_metadata.assert_called_once_with(
            self.repo.repo_path, gpg_key=self.key_a, generate_deltas=True)

    @patch('apps.flatpak.views.update_repo_metadata', return_value={'success': True})
    @patch('apps.flatpak.views.subprocess.run')
    def test_switching_key_rewrites_public_key(self, run, update_metadata):
        self.repo.gpg_key = self.key_a
        self.repo.save()
        with open(self.key_file, 'w') as fh:
            fh.write('PUBLIC KEY A')

        self._post_edit(self.key_b)

        self.assertEqual(self._read_key_file(), 'PUBLIC KEY B')
        update_metadata.assert_called_once_with(
            self.repo.repo_path, gpg_key=self.key_b, generate_deltas=True)

    @patch('apps.flatpak.views.update_repo_metadata', return_value={'success': True})
    @patch('apps.flatpak.views.subprocess.run')
    def test_removing_key_deletes_public_key_file(self, run, update_metadata):
        self.repo.gpg_key = self.key_a
        self.repo.save()
        with open(self.key_file, 'w') as fh:
            fh.write('PUBLIC KEY A')

        self._post_edit(None)

        self.assertFalse(os.path.exists(self.key_file))
        run.assert_any_call(
            ['ostree', 'config', 'delete', '--repo', self.repo.repo_path, 'core.gpg-sign'],
            capture_output=True, text=True,
        )
        update_metadata.assert_called_once_with(
            self.repo.repo_path, gpg_key=None, generate_deltas=True)

    @patch('apps.flatpak.views.update_repo_metadata', return_value={'success': True})
    @patch('apps.flatpak.views.subprocess.run')
    def test_unchanged_key_does_not_resign(self, run, update_metadata):
        self.repo.gpg_key = self.key_a
        self.repo.save()

        self._post_edit(self.key_a)

        update_metadata.assert_not_called()
        self.assertFalse(os.path.exists(self.key_file))
