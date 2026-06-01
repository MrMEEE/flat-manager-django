import logging
import os
import shutil
import shlex
import subprocess
import tempfile

from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from apps.users.mixins import (
    AdminRequiredMixin, RepoAdminRequiredMixin,
    BuildAdminRequiredMixin,
)
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.views import View
from django.http import JsonResponse
from django.urls import reverse_lazy, reverse
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .models import (
    RpmDistribution, RpmPackage, RpmBuild, RpmBuildLog,
    SatelliteServer, SatelliteRepository, RpmPackageDistributionDestination,
)
from .forms import RpmPackageForm

logger = logging.getLogger(__name__)


def _get_writable_temp_base() -> str:
    """Return a temp base directory that is writable by the current process."""
    candidates = []
    configured = (getattr(settings, 'TEMP_DIR', '') or '').strip()
    if configured:
        candidates.append(configured)
    candidates.append(os.path.join(tempfile.gettempdir(), 'flat-manager'))

    errors = []
    for base in candidates:
        try:
            os.makedirs(base, exist_ok=True)
            probe = tempfile.mkdtemp(prefix='probe-', dir=base)
            shutil.rmtree(probe, ignore_errors=True)
            return base
        except OSError as exc:
            errors.append(f"{base}: {exc}")

    details = '; '.join(errors) if errors else 'no candidate directories available'
    raise OSError(f"No writable temp directory for RPM git operations ({details})")


def _get_rpm_build_base_path() -> str:
    return (getattr(settings, 'RPM_BUILD_PATH', '') or os.path.join(settings.FLATPAK_BUILD_PATH, 'rpms'))


def _decode_mountinfo_path(value: str) -> str:
    return (
        value.replace('\\040', ' ')
        .replace('\\011', '\t')
        .replace('\\012', '\n')
        .replace('\\134', '\\')
    )


def _get_mount_entry(path: str):
    """Return the longest matching mount entry from /proc/self/mountinfo for path."""
    target = os.path.abspath(path)
    best = None
    best_len = -1

    try:
        with open('/proc/self/mountinfo', 'r', encoding='utf-8', errors='replace') as fh:
            for line in fh:
                left, _, _right = line.partition(' - ')
                parts = left.split()
                if len(parts) < 6:
                    continue
                mount_point = _decode_mountinfo_path(parts[4])
                mount_opts = parts[5].split(',')
                prefix = mount_point.rstrip('/') + '/'
                if target == mount_point or target.startswith(prefix):
                    if len(mount_point) > best_len:
                        best = {
                            'mount_point': mount_point,
                            'options': mount_opts,
                        }
                        best_len = len(mount_point)
    except OSError:
        return None

    return best


def _get_rpm_build_path_warning():
    build_path = _get_rpm_build_base_path()
    mount_entry = _get_mount_entry(build_path)
    if not mount_entry:
        return None
    if 'nosuid' not in mount_entry['options']:
        return None
    return {
        'build_path': build_path,
        'mount_point': mount_entry['mount_point'],
    }


# ---------------------------------------------------------------------------
# Package views
# ---------------------------------------------------------------------------

class RpmPackageListView(LoginRequiredMixin, ListView):
    model = RpmPackage
    template_name = 'rpm/package_list.html'
    context_object_name = 'packages'
    paginate_by = 30

    def get_queryset(self):
        from django.db.models import Count
        return (
            RpmPackage.objects
            .prefetch_related('distributions')
            .annotate(dest_count=Count('distribution_destinations', distinct=True))
            .order_by('-created_at')
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['mock_installed'] = bool(shutil.which('mock'))
        ctx['rpm_build_path_warning'] = _get_rpm_build_path_warning()
        return ctx


class RpmPackageDetailView(LoginRequiredMixin, DetailView):
    model = RpmPackage
    template_name = 'rpm/package_detail.html'
    context_object_name = 'package'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['builds'] = (
            self.object.builds
            .select_related('distribution')
            .order_by('-started_at')
        )
        # Destinations section: one row per (distribution, destinations) pair
        distributions = list(self.object.distributions.filter(is_active=True).order_by('rhel_version', 'arch'))
        existing = (
            RpmPackageDistributionDestination.objects
            .filter(package=self.object, distribution__in=distributions)
            .select_related('distribution', 'repository__server')
        )
        dest_by_dist: dict = {}
        for d in distributions:
            dest_by_dist[d.pk] = {'distribution': d, 'destinations': []}
        for e in existing:
            if e.distribution_id in dest_by_dist:
                dest_by_dist[e.distribution_id]['destinations'].append(e)
        from apps.flatpak.models import GPGKey
        from apps.rpm.models import RpmPackageSigningKey
        ctx['gpg_keys'] = GPGKey.objects.filter(is_active=True).order_by('name')
        signing_key_records = RpmPackageSigningKey.objects.filter(
            package=self.object, distribution__in=distributions
        ).select_related('signing_key')
        signing_key_by_dist_pk = {r.distribution_id: r.signing_key for r in signing_key_records}
        for entry in dest_by_dist.values():
            entry['signing_key'] = signing_key_by_dist_pk.get(entry['distribution'].pk)
        ctx['dist_destinations'] = list(dest_by_dist.values())
        ctx['available_repos'] = SatelliteRepository.objects.select_related('server').order_by('server__name', 'organization', 'name')
        return ctx


class RpmPackageCreateView(BuildAdminRequiredMixin, CreateView):
    model = RpmPackage
    form_class = RpmPackageForm
    template_name = 'rpm/package_form.html'

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        response = super().form_valid(form)
        # Queue one build per selected distribution
        self._trigger_builds(self.object)
        messages.success(
            self.request,
            f"Package '{self.object.name}' created and builds queued.",
        )
        return response

    def _trigger_builds(self, package):
        from apps.rpm.tasks import rpm_build_task
        for dist in package.distributions.filter(is_active=True):
            build = RpmBuild.objects.create(
                package=package,
                distribution=dist,
                build_number=1,
                status='pending',
            )
            rpm_build_task.delay(build.pk)

    def get_success_url(self):
        return reverse('rpm:package_detail', args=[self.object.pk])


class RpmPackageUpdateView(BuildAdminRequiredMixin, UpdateView):
    model = RpmPackage
    form_class = RpmPackageForm
    template_name = 'rpm/package_form.html'

    def get_success_url(self):
        messages.success(self.request, "Package updated.")
        return reverse('rpm:package_detail', args=[self.object.pk])


class RpmPackageDeleteView(BuildAdminRequiredMixin, DeleteView):
    model = RpmPackage
    template_name = 'rpm/package_confirm_delete.html'
    success_url = reverse_lazy('rpm:package_list')

    def form_valid(self, form):
        name = self.object.name
        response = super().form_valid(form)
        messages.success(self.request, f"Package '{name}' deleted.")
        return response


def _next_build_number(package, distribution):
    """Return the next build_number for the given package+distribution combo.

    Resets to 1 when the version in the most recent build differs from the
    package's current available_version (i.e. the version has changed since
    the last build).  Falls back to incrementing when either version is not
    yet known.
    """
    last = (
        RpmBuild.objects
        .filter(package=package, distribution=distribution)
        .order_by('-build_number')
        .first()
    )
    if last is None:
        return 1
    if last.version and package.available_version and last.version != package.available_version:
        return 1
    return last.build_number + 1


class RpmPackageBuildView(BuildAdminRequiredMixin, View):
    """POST — trigger builds immediately using each distribution's default-enabled repos."""

    def post(self, request, pk):
        package = get_object_or_404(RpmPackage, pk=pk)
        from apps.rpm.tasks import rpm_build_task

        queued = 0
        for dist in package.distributions.filter(is_active=True):
            next_number = _next_build_number(package, dist)
            build = RpmBuild.objects.create(
                package=package,
                distribution=dist,
                build_number=next_number,
                status='pending',
            )
            build.selected_repos.set(package.default_repos.filter(distribution=dist))
            rpm_build_task.delay(build.pk)
            queued += 1

        if queued:
            messages.success(request, f"Queued {queued} build(s).")
        else:
            messages.warning(request, "No active distributions configured for this package.")

        return redirect('rpm:package_detail', pk=pk)


class RpmPackageBuildWithNumberView(BuildAdminRequiredMixin, View):
    """POST — trigger builds with a user-specified build number (JSON response)."""

    def post(self, request, pk):
        package = get_object_or_404(RpmPackage, pk=pk)
        from apps.rpm.tasks import rpm_build_task

        try:
            build_number = int(request.POST.get('build_number', ''))
        except (TypeError, ValueError):
            return JsonResponse({'error': 'Build number must be an integer.'}, status=400)

        if build_number < 1:
            return JsonResponse({'error': 'Build number must be at least 1.'}, status=400)

        # Determine the current maximum build number across all distributions.
        from django.db.models import Max
        max_existing = (
            RpmBuild.objects
            .filter(package=package)
            .aggregate(m=Max('build_number'))['m']
        ) or 0

        if build_number <= max_existing:
            return JsonResponse(
                {'error': f'Build number must be greater than the current highest build number ({max_existing}).'},
                status=400,
            )

        dists = list(package.distributions.filter(is_active=True))
        if not dists:
            return JsonResponse(
                {'error': 'No active distributions configured for this package.'},
                status=400,
            )

        for dist in dists:
            build = RpmBuild.objects.create(
                package=package,
                distribution=dist,
                build_number=build_number,
                status='pending',
            )
            build.selected_repos.set(package.default_repos.filter(distribution=dist))
            rpm_build_task.delay(build.pk)

        return JsonResponse({'queued': len(dists), 'build_number': build_number})


# ---------------------------------------------------------------------------
# Build views
# ---------------------------------------------------------------------------

class RpmBuildDetailView(LoginRequiredMixin, DetailView):
    model = RpmBuild
    template_name = 'rpm/build_detail.html'
    context_object_name = 'build'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['logs'] = self.object.logs.order_by('timestamp')
        return ctx


class RpmBuildLogsApiView(LoginRequiredMixin, View):
    """AJAX — return all log lines plus current status for polling."""

    def get(self, request, pk):
        build = get_object_or_404(RpmBuild, pk=pk)
        logs = build.logs.order_by('timestamp').values('id', 'message', 'level', 'timestamp')
        log_data = [
            {
                'id': l['id'],
                'message': l['message'],
                'level': l['level'],
                'timestamp': l['timestamp'].strftime('%H:%M:%S'),
            }
            for l in logs
        ]
        return JsonResponse({'status': build.status, 'logs': log_data})


class RpmBuildMockLogView(LoginRequiredMixin, View):
    """AJAX — return the content of a mock log file from the build result directory."""
    ALLOWED_FILES = frozenset({
        'build.log', 'hw_info.log', 'installed_pkgs.log', 'root.log', 'state.log',
    })

    def get(self, request, pk):
        build = get_object_or_404(RpmBuild, pk=pk)
        filename = request.GET.get('file', '')
        if filename not in self.ALLOWED_FILES:
            return JsonResponse({'error': 'Invalid log file name.'}, status=400)

        if not build.result_dir:
            return JsonResponse({'available': False, 'content': None})

        for subdir in ('rpms', 'srpm'):
            candidate = os.path.join(build.result_dir, subdir, filename)
            if os.path.isfile(candidate):
                try:
                    with open(candidate, encoding='utf-8', errors='replace') as fh:
                        content = fh.read()
                    return JsonResponse({'available': True, 'content': content})
                except OSError:
                    break

        return JsonResponse({'available': False, 'content': None})


class RpmBuildRetryView(BuildAdminRequiredMixin, View):
    """POST — create a new build record (copying repo selection) and queue it."""

    def post(self, request, pk):
        old_build = get_object_or_404(RpmBuild, pk=pk)
        from apps.rpm.tasks import rpm_build_task

        new_build = RpmBuild.objects.create(
            package=old_build.package,
            distribution=old_build.distribution,
            build_number=_next_build_number(old_build.package, old_build.distribution),
            status='pending',
        )
        # Copy the repo selection from the original build
        new_build.selected_repos.set(old_build.selected_repos.all())
        rpm_build_task.delay(new_build.pk)
        messages.success(request, "Retry queued.")
        return redirect('rpm:build_detail', pk=new_build.pk)


class RpmBuildCancelView(BuildAdminRequiredMixin, View):
    """POST — mark a build as cancelled; the running task will notice and abort."""

    def post(self, request, pk):
        build = get_object_or_404(RpmBuild, pk=pk)
        if build.status in ('pending', 'building'):
            build.status = 'cancelled'
            build.completed_at = timezone.now()
            build.save(update_fields=['status', 'completed_at'])
            from apps.rpm.tasks import _update_package_status
            _update_package_status(build.package)
            messages.success(request, "Build cancelled.")
        else:
            messages.warning(request, "Build is not in a cancellable state.")
        return redirect('rpm:build_detail', pk=pk)


class RpmBuildDeleteView(BuildAdminRequiredMixin, View):
    """POST — delete a completed/failed/cancelled build record."""

    def post(self, request, pk):
        build = get_object_or_404(RpmBuild, pk=pk)
        if build.status in ('building', 'pending'):
            messages.error(request, "Cannot delete an active build.")
            return redirect('rpm:build_detail', pk=pk)
        package_pk = build.package_id
        build.delete()
        messages.success(request, "Build deleted.")
        return redirect('rpm:package_detail', pk=package_pk)


# ---------------------------------------------------------------------------
# Distribution management
# ---------------------------------------------------------------------------

class RpmDistributionListView(RepoAdminRequiredMixin, ListView):
    model = RpmDistribution
    template_name = 'rpm/distribution_list.html'
    context_object_name = 'distributions'
    ordering = ['rhel_version', 'arch']

    def get_queryset(self):
        from django.db.models import Count, Q
        return (
            RpmDistribution.objects
            .annotate(
                repo_count=Count('repositories'),
                enabled_repo_count=Count('repositories', filter=Q(repositories__enabled=True)),
            )
            .order_by('rhel_version', 'arch')
        )


class RpmPackageRepositoriesView(RepoAdminRequiredMixin, View):
    """GET — per-package repository configuration page.

    Shows only the distributions assigned to this package (and active).
    Toggle state reflects package.default_repos, not the global repo.enabled flag.
    """

    def get(self, request, pk):
        package = get_object_or_404(RpmPackage, pk=pk)
        from apps.rpm.models import RpmRepository
        active_dists = list(
            package.distributions
            .filter(is_active=True)
            .prefetch_related('repositories')
            .order_by('rhel_version', 'arch')
        )
        default_repo_pks = set(package.default_repos.values_list('pk', flat=True))
        dist_repos = [
            {
                'dist': dist,
                'repos': list(dist.repositories.order_by('-source', 'name')),
            }
            for dist in active_dists
        ]
        return render(request, 'rpm/package_repositories.html', {
            'package': package,
            'dist_repos': dist_repos,
            'default_repo_pks': default_repo_pks,
        })


class RpmPackageRepositoryToggleView(RepoAdminRequiredMixin, View):
    """POST (AJAX) — toggle a repo in/out of a package's default_repos."""

    def post(self, request, pk, repo_pk):
        from apps.rpm.models import RpmRepository
        package = get_object_or_404(RpmPackage, pk=pk)
        repo = get_object_or_404(RpmRepository, pk=repo_pk)
        if package.default_repos.filter(pk=repo_pk).exists():
            package.default_repos.remove(repo)
            enabled = False
        else:
            package.default_repos.add(repo)
            enabled = True
        return JsonResponse({'ok': True, 'enabled': enabled})


class RpmPackageSigningKeyView(RepoAdminRequiredMixin, View):
    """POST — assign or clear the GPG signing key for a (package, distribution) pair."""

    def post(self, request, pkg_pk, dist_pk):
        from apps.flatpak.models import GPGKey
        from apps.rpm.models import RpmPackageSigningKey
        package = get_object_or_404(RpmPackage, pk=pkg_pk)
        dist = get_object_or_404(RpmDistribution, pk=dist_pk)
        key_id = request.POST.get('signing_key_id', '').strip()
        obj, _ = RpmPackageSigningKey.objects.get_or_create(
            package=package, distribution=dist,
            defaults={'signing_key': None},
        )
        if key_id:
            signing_key = get_object_or_404(GPGKey, pk=key_id)
            obj.signing_key = signing_key
            obj.save(update_fields=['signing_key'])
            messages.success(
                request,
                f"Signing key '{signing_key.name}' assigned to "
                f"{package.name} / {dist.display_name}.",
            )
        else:
            obj.signing_key = None
            obj.save(update_fields=['signing_key'])
            messages.success(
                request,
                f"Signing key removed from {package.name} / {dist.display_name} — RPMs will be unsigned.",
            )
        return redirect('rpm:package_detail', pk=pkg_pk)


class RpmScanSpecFilesView(BuildAdminRequiredMixin, View):
    """GET — shallow-clone a git repo and return a list of .spec files found in it."""

    def get(self, request):
        import glob as _glob

        url = request.GET.get('url', '').strip()
        branch = (request.GET.get('branch', '') or '').strip() or 'main'

        if not url:
            return JsonResponse({'error': 'url is required'}, status=400)

        temp_dir = None
        try:
            tmp_base = _get_writable_temp_base()
            temp_dir = tempfile.mkdtemp(dir=tmp_base, prefix='rpm-scan-')
            os.chmod(temp_dir, 0o700)
            source_dir = os.path.join(temp_dir, 'source')

            git_cmd = (
                f"umask 0022 && git clone --depth 1 --branch {shlex.quote(branch)} "
                f"-- {shlex.quote(url)} {shlex.quote(source_dir)}"
            )
            result = subprocess.run(
                ['bash', '-c', git_cmd],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                msg = (result.stderr or result.stdout or 'Clone failed').strip()
                return JsonResponse({'error': msg}, status=400)

            spec_files = _glob.glob(os.path.join(source_dir, '**', '*.spec'), recursive=True)
            rel_paths = sorted(os.path.relpath(f, source_dir) for f in spec_files)
        except subprocess.TimeoutExpired:
            return JsonResponse({'error': 'Git clone timed out after 120 seconds.'}, status=504)
        except FileNotFoundError:
            return JsonResponse({'error': 'git executable not found on this server.'}, status=500)
        except OSError as exc:
            return JsonResponse({'error': f'Server filesystem error: {exc}'}, status=500)
        finally:
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)

        return JsonResponse({'spec_files': rel_paths})


class RpmScanBranchesView(BuildAdminRequiredMixin, View):
    """GET — list all remote branches of a git repo without cloning it."""

    def get(self, request):
        url = request.GET.get('url', '').strip()
        if not url:
            return JsonResponse({'error': 'url is required'}, status=400)

        try:
            result = subprocess.run(
                ['git', 'ls-remote', '--heads', '--', url],
                capture_output=True, text=True, timeout=30,
            )
        except subprocess.TimeoutExpired:
            return JsonResponse({'error': 'Git timed out listing remote branches.'}, status=504)
        except FileNotFoundError:
            return JsonResponse({'error': 'git executable not found on this server.'}, status=500)

        if result.returncode != 0:
            msg = (result.stderr or result.stdout or 'Failed to list branches').strip()
            return JsonResponse({'error': msg}, status=400)

        branches = []
        for line in result.stdout.splitlines():
            # lines look like: <sha>\trefs/heads/<branch>
            parts = line.strip().split('\t', 1)
            if len(parts) == 2 and parts[1].startswith('refs/heads/'):
                branches.append(parts[1][len('refs/heads/'):])

        return JsonResponse({'branches': sorted(branches)})


class RpmPackageCheckUpstreamView(BuildAdminRequiredMixin, View):
    """POST — AJAX: fetch the latest upstream version tag for a package."""

    def post(self, request, pk):
        package = get_object_or_404(RpmPackage, pk=pk)
        if not package.upstream_url and not package.upstream_version_script.strip():
            return JsonResponse(
                {'error': 'No upstream URL or version script configured for this package'},
                status=400,
            )

        from apps.flatpak.tasks import _fetch_latest_upstream_tag, _run_version_script, _normalise_version

        version = None
        error = None

        if package.upstream_version_script.strip():
            version, _release_date, error = _run_version_script(package.upstream_version_script, package.name)

        if not version and package.upstream_url:
            version, _raw_tag, error = _fetch_latest_upstream_tag(package.upstream_url)

        if not version:
            return JsonResponse({'error': error or 'Could not determine upstream version'}, status=502)

        version = _normalise_version(version)
        package.upstream_version = version
        package.upstream_checked_at = timezone.now()
        package.save(update_fields=['upstream_version', 'upstream_checked_at'])

        return JsonResponse({
            'version': version,
            'has_update': bool(
                package.last_build_version and version and version != package.last_build_version
            ),
        })


class RpmPackageCheckAvailableView(BuildAdminRequiredMixin, View):
    """POST — AJAX: shallow-clone the spec repo, extract version and Requires."""

    def post(self, request, pk):
        import glob as _glob
        import re

        package = get_object_or_404(RpmPackage, pk=pk)
        if not package.git_repo_url:
            return JsonResponse({'error': 'No git repository URL configured'}, status=400)

        branch = (package.git_branch or 'main').strip()

        temp_dir = None
        try:
            tmp_base = _get_writable_temp_base()
            temp_dir = tempfile.mkdtemp(dir=tmp_base, prefix='rpm-avail-')
            os.chmod(temp_dir, 0o700)
            source_dir = os.path.join(temp_dir, 'source')

            git_cmd = (
                f"umask 0022 && git clone --depth 1 --branch {shlex.quote(branch)} "
                f"-- {shlex.quote(package.git_repo_url)} {shlex.quote(source_dir)}"
            )
            result = subprocess.run(
                ['bash', '-c', git_cmd],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                msg = (result.stderr or result.stdout or 'Clone failed').strip()
                return JsonResponse({'error': msg}, status=502)

            spec_path = os.path.join(source_dir, package.spec_file)
            if not os.path.isfile(spec_path):
                return JsonResponse(
                    {'error': f'Spec file not found: {package.spec_file}'}, status=400
                )

            with open(spec_path, 'r', errors='replace') as fh:
                spec_text = fh.read()

            version = None
            requires = []
            build_requires = []

            for line in spec_text.splitlines():
                stripped = line.strip()
                if re.match(r'^Version\s*:', stripped, re.IGNORECASE):
                    raw = stripped.split(':', 1)[1].strip()
                    # Skip macro-only expressions like %{version}
                    if raw and not raw.startswith('%{'):
                        version = raw
                elif re.match(r'^Requires\s*:', stripped, re.IGNORECASE):
                    req = stripped.split(':', 1)[1].strip()
                    # Skip self-references like "%{name} = %{version}"
                    if req and '%{name}' not in req:
                        requires.append(req)
                elif re.match(r'^BuildRequires\s*:', stripped, re.IGNORECASE):
                    req = stripped.split(':', 1)[1].strip()
                    if req and '%{name}' not in req:
                        build_requires.append(req)

            now = timezone.now()
            update_fields = ['available_version_checked_at', 'spec_requires', 'spec_requires_checked_at']
            package.available_version_checked_at = now
            package.spec_requires = {'requires': requires, 'build_requires': build_requires}
            package.spec_requires_checked_at = now
            if version:
                package.available_version = version
                update_fields.append('available_version')
            package.save(update_fields=update_fields)

            return JsonResponse({
                'available_version': version or '',
                'requires': requires,
                'build_requires': build_requires,
                'has_update': bool(
                    package.last_build_version and version and version != package.last_build_version
                ),
            })

        except Exception as exc:
            logger.exception("RpmPackageCheckAvailableView error for package %s", pk)
            return JsonResponse({'error': str(exc)}, status=500)
        finally:
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)


class RpmDistributionSyncView(RepoAdminRequiredMixin, View):

    def post(self, request):
        from apps.rpm.tasks import sync_distributions_from_mock
        results = sync_distributions_from_mock()
        created = sum(1 for _, c in results if c)
        found = len(results)
        if found == 0:
            messages.warning(request, "No RHEL Mock configs found in /etc/mock/.")
        else:
            messages.success(
                request,
                f"Sync complete: {found} distribution(s) found, {created} newly added.",
            )
        return redirect('rpm:distribution_list')


class RpmDistributionToggleView(RepoAdminRequiredMixin, View):
    """POST — toggle the is_active flag on a distribution."""

    def post(self, request, pk):
        dist = get_object_or_404(RpmDistribution, pk=pk)
        dist.is_active = not dist.is_active
        dist.save(update_fields=['is_active'])
        state = "enabled" if dist.is_active else "disabled"
        messages.success(request, f"Distribution '{dist.display_name}' {state}.")
        return redirect('rpm:distribution_list')


class RpmDistributionSyncReposView(RepoAdminRequiredMixin, View):
    """POST — queue an async repo sync for a single distribution."""

    def post(self, request, pk):
        from apps.rpm.tasks import sync_distribution_repos_task
        dist = get_object_or_404(RpmDistribution, pk=pk)
        sync_distribution_repos_task.delay(dist.pk)
        messages.info(
            request,
            f"Repo sync queued for '{dist.display_name}'. "
            "Results will be available after the task completes.",
        )
        return redirect('rpm:distribution_list')


class RpmRepositoryToggleView(RepoAdminRequiredMixin, View):
    """POST — toggle the enabled flag on a single RpmRepository."""

    def post(self, request, repo_pk):
        from apps.rpm.models import RpmRepository
        repo = get_object_or_404(RpmRepository, pk=repo_pk)
        repo.enabled = not repo.enabled
        repo.save(update_fields=['enabled'])
        state = "enabled" if repo.enabled else "disabled"
        return JsonResponse({'ok': True, 'enabled': repo.enabled, 'state': state})


# ---------------------------------------------------------------------------
# Satellite / Katello destinations
# ---------------------------------------------------------------------------

class RpmDestinationListView(RepoAdminRequiredMixin, ListView):
    """List all Satellite servers and their registered repositories."""
    model = SatelliteServer
    template_name = 'rpm/destination_list.html'
    context_object_name = 'servers'

    def get_queryset(self):
        return SatelliteServer.objects.prefetch_related('repositories').order_by('name')


class RpmSatelliteServerCreateView(AdminRequiredMixin, View):
    """
    GET  — render the Add Server form.
    POST — call the Satellite API with admin credentials to provision the
           service account + encrypted PAT, then save the SatelliteServer.
    """

    def get(self, request):
        from django.shortcuts import render
        return render(request, 'rpm/server_form.html', {})

    def post(self, request):
        from django.shortcuts import render
        from apps.rpm.satellite import (
            _basic_auth, test_connection, provision_service_account, encrypt_token,
        )

        name       = request.POST.get('name', '').strip()
        url        = request.POST.get('url', '').rstrip('/')
        ssl_verify = request.POST.get('ssl_verify', '') == 'on'
        admin_user = request.POST.get('admin_user', '').strip()
        admin_pass = request.POST.get('admin_pass', '').strip()
        svc_login  = request.POST.get('svc_login', '').strip() or 'flat-manager'

        errors = {}
        if not name:
            errors['name'] = 'Required.'
        if not url:
            errors['url'] = 'Required.'
        if not admin_user:
            errors['admin_user'] = 'Required.'
        if not admin_pass:
            errors['admin_pass'] = 'Required.'
        if SatelliteServer.objects.filter(name=name).exists():
            errors['name'] = 'A server with this name already exists.'

        if errors:
            return render(request, 'rpm/server_form.html', {
                'errors': errors,
                'form_data': request.POST,
            })

        admin_auth = _basic_auth(admin_user, admin_pass)

        # Verify connectivity
        err = test_connection(url, admin_auth, ssl_verify)
        if err:
            errors['non_field'] = f"Could not contact Satellite: {err}"
            return render(request, 'rpm/server_form.html', {
                'errors': errors,
                'form_data': request.POST,
            })

        # Provision service account and get PAT
        token, err = provision_service_account(url, admin_auth, svc_login, ssl_verify)
        if err:
            errors['non_field'] = f"Service account setup failed: {err}"
            return render(request, 'rpm/server_form.html', {
                'errors': errors,
                'form_data': request.POST,
            })

        server = SatelliteServer.objects.create(
            name=name,
            url=url,
            login=svc_login,
            token_encrypted=encrypt_token(token),
            ssl_verify=ssl_verify,
            created_by=request.user,
        )
        messages.success(request, f"Satellite server '{server.name}' added successfully.")
        return redirect('rpm:destination_list')


class RpmSatelliteServerDeleteView(AdminRequiredMixin, View):
    def post(self, request, pk):
        server = get_object_or_404(SatelliteServer, pk=pk)
        name = server.name
        server.delete()
        messages.success(request, f"Satellite server '{name}' removed.")
        return redirect('rpm:destination_list')


class RpmSatelliteRepositoryAddView(AdminRequiredMixin, View):
    """POST — add a SatelliteRepository to a known server's saved repo list."""

    def post(self, request, server_pk):
        server = get_object_or_404(SatelliteServer, pk=server_pk)
        org     = request.POST.get('organization', '').strip()
        product = request.POST.get('product', '').strip()
        name    = request.POST.get('name', '').strip()
        try:
            repo_id = int(request.POST.get('repository_id', 0))
        except (ValueError, TypeError):
            repo_id = 0

        if not all([org, product, name, repo_id]):
            messages.error(request, "All repository fields are required.")
            return redirect('rpm:destination_list')

        SatelliteRepository.objects.get_or_create(
            server=server,
            repository_id=repo_id,
            defaults=dict(organization=org, product=product, name=name),
        )
        messages.success(request, f"Repository '{name}' added to {server.name}.")
        return redirect('rpm:destination_list')


class RpmSatelliteRepositoryDeleteView(AdminRequiredMixin, View):
    def post(self, request, pk):
        repo = get_object_or_404(SatelliteRepository, pk=pk)
        name = repo.name
        repo.delete()
        messages.success(request, f"Repository '{name}' removed.")
        return redirect('rpm:destination_list')


# -- AJAX discovery endpoints -------------------------------------------------

class RpmSatelliteFetchOrgsView(AdminRequiredMixin, View):
    """GET ?server=<pk>  — return org list as JSON."""

    def get(self, request):
        from apps.rpm.satellite import fetch_organizations
        server_pk = request.GET.get('server', '')
        server = get_object_or_404(SatelliteServer, pk=server_pk)
        orgs = fetch_organizations(server.url, server.login, server.token, server.ssl_verify)
        return JsonResponse({'orgs': [{'id': o['id'], 'name': o['name']} for o in orgs]})


class RpmSatelliteFetchProductsView(AdminRequiredMixin, View):
    """GET ?server=<pk>&org_id=<id>  — return product list as JSON."""

    def get(self, request):
        from apps.rpm.satellite import fetch_products
        server = get_object_or_404(SatelliteServer, pk=request.GET.get('server', ''))
        try:
            org_id = int(request.GET.get('org_id', 0))
        except ValueError:
            return JsonResponse({'error': 'Invalid org_id'}, status=400)
        products = fetch_products(server.url, server.login, server.token, org_id, server.ssl_verify)
        return JsonResponse({'products': [{'id': p['id'], 'name': p['name']} for p in products]})


class RpmSatelliteFetchReposView(AdminRequiredMixin, View):
    """GET ?server=<pk>&product_id=<id>  — return repo list as JSON."""

    def get(self, request):
        from apps.rpm.satellite import fetch_repositories
        server = get_object_or_404(SatelliteServer, pk=request.GET.get('server', ''))
        try:
            product_id = int(request.GET.get('product_id', 0))
        except ValueError:
            return JsonResponse({'error': 'Invalid product_id'}, status=400)
        repos = fetch_repositories(server.url, server.login, server.token, product_id, server.ssl_verify)
        return JsonResponse({'repos': [{'id': r['id'], 'name': r['name']} for r in repos]})


# -- Package destination assignment ------------------------------------------

class RpmPackageAssignDestinationView(RepoAdminRequiredMixin, View):
    """POST — link a (package, distribution) pair to a SatelliteRepository."""

    def post(self, request, pk):
        package = get_object_or_404(RpmPackage, pk=pk)
        try:
            dist_id = int(request.POST.get('distribution_id', 0))
            repo_id_field = int(request.POST.get('repository_id', 0))
        except (ValueError, TypeError):
            messages.error(request, "Invalid distribution or repository.")
            return redirect('rpm:package_detail', pk=pk)

        dist = get_object_or_404(RpmDistribution, pk=dist_id)
        repo = get_object_or_404(SatelliteRepository, pk=repo_id_field)

        _, created = RpmPackageDistributionDestination.objects.get_or_create(
            package=package, distribution=dist, repository=repo,
        )
        if created:
            messages.success(request, f"Destination '{repo}' assigned for {dist.display_name}.")
        else:
            messages.info(request, "That destination is already assigned.")
        return redirect('rpm:package_detail', pk=pk)


class RpmPackageRemoveDestinationView(RepoAdminRequiredMixin, View):
    """POST — remove a package distribution destination."""

    def post(self, request, pk):
        dest = get_object_or_404(RpmPackageDistributionDestination, pk=pk)
        package_pk = dest.package_id
        dest.delete()
        messages.success(request, "Destination removed.")
        return redirect('rpm:package_detail', pk=package_pk)
