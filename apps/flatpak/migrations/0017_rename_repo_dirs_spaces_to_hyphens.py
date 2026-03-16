"""
Data migration: rename on-disk repository folders and GPG key files for any
repository whose name contains spaces.

Old layout:  <REPOS_BASE_PATH>/My Repo/          + My Repo.gpg
New layout:  <REPOS_BASE_PATH>/My-Repo/          + My-Repo.gpg

The migration is idempotent – if the old path no longer exists (already
renamed or never created) it is silently skipped.
"""

import logging
import os

from django.conf import settings
from django.db import migrations

logger = logging.getLogger(__name__)


def rename_repo_paths(apps, schema_editor):
    Repository = apps.get_model('flatpak', 'Repository')
    base = getattr(settings, 'REPOS_BASE_PATH', None)
    if not base:
        logger.warning('REPOS_BASE_PATH not set – skipping repo dir rename migration')
        return

    for repo in Repository.objects.all():
        name = repo.name
        if ' ' not in name:
            continue

        folder_name = name.replace(' ', '-')

        # ── Rename the OSTree repo directory ─────────────────────────────────
        old_dir = os.path.join(base, name)
        new_dir = os.path.join(base, folder_name)

        if os.path.exists(old_dir) and not os.path.exists(new_dir):
            os.rename(old_dir, new_dir)
            logger.info('Renamed repo dir: %r → %r', old_dir, new_dir)
        elif os.path.exists(old_dir) and os.path.exists(new_dir):
            logger.warning(
                'Both %r and %r exist – skipping dir rename for repo %r',
                old_dir, new_dir, name,
            )

        # ── Rename the exported GPG public-key file ───────────────────────────
        old_gpg = os.path.join(base, f'{name}.gpg')
        new_gpg = os.path.join(base, f'{folder_name}.gpg')

        if os.path.exists(old_gpg) and not os.path.exists(new_gpg):
            os.rename(old_gpg, new_gpg)
            logger.info('Renamed GPG key file: %r → %r', old_gpg, new_gpg)
        elif os.path.exists(old_gpg) and os.path.exists(new_gpg):
            logger.warning(
                'Both %r and %r exist – skipping GPG rename for repo %r',
                old_gpg, new_gpg, name,
            )


def reverse_rename_repo_paths(apps, schema_editor):
    """Reverse: put spaces back."""
    Repository = apps.get_model('flatpak', 'Repository')
    base = getattr(settings, 'REPOS_BASE_PATH', None)
    if not base:
        return

    for repo in Repository.objects.all():
        name = repo.name
        if ' ' not in name:
            continue

        folder_name = name.replace(' ', '-')

        new_dir = os.path.join(base, folder_name)
        old_dir = os.path.join(base, name)
        if os.path.exists(new_dir) and not os.path.exists(old_dir):
            os.rename(new_dir, old_dir)

        new_gpg = os.path.join(base, f'{folder_name}.gpg')
        old_gpg = os.path.join(base, f'{name}.gpg')
        if os.path.exists(new_gpg) and not os.path.exists(old_gpg):
            os.rename(new_gpg, old_gpg)


class Migration(migrations.Migration):

    dependencies = [
        ('flatpak', '0016_package_upstream_version_script'),
    ]

    operations = [
        migrations.RunPython(
            rename_repo_paths,
            reverse_code=reverse_rename_repo_paths,
        ),
    ]
