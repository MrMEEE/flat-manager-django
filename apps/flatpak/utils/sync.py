"""
Utilities to reconcile the database's Build/Promotion records against what is
actually present in the OSTree repositories on disk.

This module contains only plain functions (no Django views, no Celery tasks)
so that it can be imported from both without circular-import issues.
"""
import os
import subprocess
import logging

logger = logging.getLogger(__name__)


def ostree_refs(repo_path: str) -> dict[str, str]:
    """Return ``{ref_name: commit_hash}`` for every ``app/*`` ref in *repo_path*.

    Returns an empty dict if the path does not exist or ``ostree refs`` fails.
    """
    if not os.path.isdir(repo_path):
        return {}
    result = subprocess.run(
        ['ostree', 'refs', f'--repo={repo_path}'],
        capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        logger.warning('ostree refs failed for %s: %s', repo_path, result.stderr.strip())
        return {}
    refs = {}
    for line in result.stdout.splitlines():
        ref = line.strip()
        if not ref.startswith('app/'):
            continue
        rev = subprocess.run(
            ['ostree', 'rev-parse', ref, f'--repo={repo_path}'],
            capture_output=True, text=True, timeout=10
        )
        refs[ref] = rev.stdout.strip() if rev.returncode == 0 else ''
    return refs


def run_repo_sync() -> dict:
    """Scan all active OSTree repositories and reconcile Build/Promotion records.

    Returns a stats dict with keys:
    - builds_marked_published
    - builds_marked_unpublished
    - promotions_created
    - promotions_removed
    - warnings  (list of str)
    """
    from django.conf import settings
    from django.utils import timezone
    from apps.flatpak.models import Build, Package, Repository, Promotion

    stats = {
        'builds_marked_published': 0,
        'builds_marked_unpublished': 0,
        'promotions_created': 0,
        'promotions_removed': 0,
        'warnings': [],
    }

    # ── 1. Reconcile build-repo ──────────────────────────────────────────────
    build_repo_path = os.path.join(settings.REPOS_BASE_PATH, 'build-repo')
    build_repo_refs = ostree_refs(build_repo_path)

    for ref, commit_hash in build_repo_refs.items():
        parts = ref.split('/')   # ['app', pkg_id, arch, branch]
        if len(parts) != 4 or not commit_hash:
            continue
        build = Build.objects.filter(commit_hash=commit_hash).first()
        if build and build.status != 'published':
            build.status = 'published'
            if not build.published_at:
                build.published_at = timezone.now()
            if not build.completed_at:
                build.completed_at = timezone.now()
            build.save(update_fields=['status', 'published_at', 'completed_at'])
            pkg = build.package
            latest = pkg.builds.order_by('-build_number').first()
            if latest and latest.pk == build.pk:
                pkg.status = 'published'
                pkg.save(update_fields=['status'])
            stats['builds_marked_published'] += 1

    build_repo_hashes = set(build_repo_refs.values()) - {''}
    for build in Build.objects.filter(status='published').select_related('package'):
        if build.commit_hash and build.commit_hash not in build_repo_hashes:
            build.status = 'committed'
            build.completed_at = None
            build.save(update_fields=['status', 'completed_at'])
            pkg = build.package
            latest = pkg.builds.order_by('-build_number').first()
            if latest and latest.pk == build.pk and pkg.status == 'published':
                pkg.status = 'committed'
                pkg.save(update_fields=['status'])
            stats['builds_marked_unpublished'] += 1

    # ── 2. Reconcile child repos ─────────────────────────────────────────────
    for repo in Repository.objects.filter(is_active=True).prefetch_related('parent_repos'):
        # Only target repos (those that have at least one parent) are tracked as
        # promotion destinations.  Repos without parents are source/build repos.
        if not repo.parent_repos.exists():
            continue

        repo_path = os.path.join(settings.REPOS_BASE_PATH, repo.name)
        repo_refs = ostree_refs(repo_path)

        # ── Create missing Promotion records ──────────────────────────────────
        for ref, commit_hash in repo_refs.items():
            parts = ref.split('/')
            if len(parts) != 4:
                continue
            _, pkg_id, arch, branch = parts

            build = None
            if commit_hash:
                build = Build.objects.filter(commit_hash=commit_hash).select_related('package').first()
            if build is None:
                pkg = Package.objects.filter(package_id=pkg_id, arch=arch, branch=branch).first()
                if pkg:
                    build = (
                        pkg.builds.filter(status='published').order_by('-build_number').first()
                        or (
                            pkg.builds.filter(commit_hash=commit_hash).first()
                            if commit_hash else None
                        )
                    )

            if build is None:
                stats['warnings'].append(
                    f"{repo.name}: ref '{ref}' on disk has no matching Build record "
                    f"(commit={commit_hash[:12] if commit_hash else 'unknown'})"
                )
                continue

            _, created = Promotion.objects.get_or_create(
                build=build,
                target_repo=repo,
                defaults={
                    'package': build.package,
                    'status': 'promoted',
                    'promoted_by': None,
                    'completed_at': timezone.now(),
                }
            )
            if created:
                stats['promotions_created'] += 1
            else:
                # Ensure the status reflects the on-disk reality
                Promotion.objects.filter(build=build, target_repo=repo).exclude(
                    status='promoted'
                ).update(status='promoted')

        # ── Remove stale Promotion records ────────────────────────────────────
        repo_hashes_on_disk = set(repo_refs.values()) - {''}
        ref_names_on_disk = set(repo_refs.keys())
        for promo in (
            Promotion.objects
            .filter(target_repo=repo, status='promoted')
            .select_related('build__package', 'package')
        ):
            pkg = promo.package
            expected_ref = f'app/{pkg.package_id}/{pkg.arch}/{pkg.branch}'
            on_disk = (
                expected_ref in ref_names_on_disk
                or (promo.build.commit_hash and promo.build.commit_hash in repo_hashes_on_disk)
            )
            if not on_disk:
                promo.delete()
                stats['promotions_removed'] += 1

    if any(stats[k] for k in ('builds_marked_published', 'builds_marked_unpublished',
                               'promotions_created', 'promotions_removed')):
        logger.info('repo sync: %s', stats)

    return stats
