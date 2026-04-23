import os
import re
import glob
import shlex
import shutil
import tempfile
import subprocess
import logging

from celery import shared_task
from django.utils import timezone
from django.conf import settings
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

logger = logging.getLogger(__name__)

_ANSI_ESC_RE = re.compile(r'\x1b\[[0-9;]*[mKGHJAB]')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def log_rpm_build(build, level, message):
    """Persist a log line for an RPM build and echo it to the Python logger."""
    from apps.rpm.models import RpmBuildLog
    message = _ANSI_ESC_RE.sub('', message)
    if '\r' in message:
        message = message.split('\r')[-1]
    message = message.strip()
    if not message:
        return
    RpmBuildLog.objects.create(build=build, message=message, level=level)
    logger.log(
        getattr(logging, level.upper(), logging.INFO),
        "[RPM Build #%s] %s", build.pk, message,
    )


def send_rpm_build_status_update(build_id, status, message=''):
    channel_layer = get_channel_layer()
    event = {
        'type': 'rpm_build_status_update',
        'build_id': build_id,
        'status': status,
        'message': message,
        'timestamp': timezone.now().isoformat(),
    }
    try:
        async_to_sync(channel_layer.group_send)('notifications', event)
        async_to_sync(channel_layer.group_send)(f'rpm_build_{build_id}', event)
    except Exception as exc:
        logger.warning("Could not send WebSocket update for RPM build %s: %s", build_id, exc)


def _update_package_status(package):
    """Recompute the aggregate status on the parent RpmPackage."""
    from apps.rpm.models import RpmBuild
    builds = RpmBuild.objects.filter(package=package)
    if not builds.exists():
        package.status = 'idle'
    elif builds.filter(status__in=['building', 'pending']).exists():
        package.status = 'building'
    elif builds.filter(status='failed').exists() and not builds.filter(status='built').exists():
        package.status = 'failed'
    elif builds.filter(status='built').exists():
        package.status = 'built'
    else:
        package.status = 'idle'

    update_fields = ['status']
    latest_built = (
        builds.filter(status='built', version__gt='')
        .order_by('-started_at')
        .values_list('version', flat=True)
        .first()
    )
    if latest_built and package.last_build_version != latest_built:
        package.last_build_version = latest_built
        update_fields.append('last_build_version')

    package.save(update_fields=update_fields)


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

def _create_mock_config(base_config, build_id, local_repo_path, cfg_path=None):
    """
    Write a temporary Mock config that inherits from the stock RHEL config and
    adds our local built-RPMs repo as an extra yum source (if the repo metadata
    already exists so that the createrepo has been run at least once before).
    """
    cfg = f"include('/etc/mock/{base_config}.cfg')\n\n"
    cfg += f"config_opts['uniqueext'] = 'fmd{build_id}'\n"

    repodata = os.path.join(local_repo_path, 'repodata', 'repomd.xml')
    if os.path.exists(repodata):
        cfg += (
            "\nconfig_opts['yum.conf'] += \"\"\"\n"
            f"[flat-manager-built-{build_id}]\n"
            f"name=Flat Manager Built RPMs\n"
            f"baseurl=file://{local_repo_path}\n"
            "enabled=1\n"
            "gpgcheck=0\n"
            "priority=1\n"
            "\"\"\"\n"
        )

    if cfg_path is None:
        cfg_root = tempfile.gettempdir()
        cfg_path = os.path.join(cfg_root, f'fmd-mock-{build_id}.cfg')
    os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
    with open(cfg_path, 'w') as fh:
        fh.write(cfg)
    return cfg_path


def _ensure_spec_release_matches_build_number(spec_path: str, build_number: int) -> bool:
    """Set SPEC Release field to '<build_number>%{?dist}'."""
    try:
        with open(spec_path, 'r', encoding='utf-8', errors='replace') as fh:
            lines = fh.readlines()

        replaced = False
        release_line = f"Release:        {build_number}%{{?dist}}\n"
        for idx, line in enumerate(lines):
            if re.match(r'^\s*Release\s*:', line, re.IGNORECASE):
                lines[idx] = release_line
                replaced = True
                break

        if not replaced:
            insert_at = 0
            for idx, line in enumerate(lines):
                if re.match(r'^\s*Version\s*:', line, re.IGNORECASE):
                    insert_at = idx + 1
                    break
            lines.insert(insert_at, release_line)

        with open(spec_path, 'w', encoding='utf-8') as fh:
            fh.writelines(lines)
        return True
    except Exception:
        return False


def _run_mock(cfg_path, extra_args, build):
    """Run mock, streaming output line-by-line into build logs."""
    cmd = ['mock', '-r', cfg_path] + extra_args
    log_rpm_build(build, 'info', 'Running: ' + ' '.join(cmd))

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding='utf-8',
        errors='replace',
    )

    from apps.rpm.models import RpmBuild as _RpmBuild
    for line in proc.stdout:
        line = line.rstrip('\n')
        if line:
            log_rpm_build(build, 'info', line)

        # Honour cancellation requests between lines
        current_status = (
            _RpmBuild.objects.filter(pk=build.pk)
            .values_list('status', flat=True)
            .first()
        )
        if current_status == 'cancelled':
            proc.kill()
            proc.wait()
            raise RuntimeError("Build was cancelled")

    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"mock exited with code {proc.returncode}")


def _run_createrepo(repo_path, build):
    """Update repo metadata with createrepo_c (preferred) or createrepo."""
    for cmd in [
        ['createrepo_c', '--update', repo_path],
        ['createrepo', '--update', repo_path],
    ]:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            log_rpm_build(build, 'info', f"Repository metadata updated: {repo_path}")
            return
        # If the binary doesn't exist, try the next one
        if result.returncode == 127 or 'not found' in result.stderr.lower():
            continue
        raise RuntimeError(f"{cmd[0]} failed: {result.stderr.strip()}")
    raise RuntimeError("Neither createrepo_c nor createrepo is available on this system")


def _sign_rpms(rpm_paths: list, gpg_key, build) -> bool:
    """
    Sign binary RPM files in-place using a temporary GNUPGHOME so the
    system keyring is never touched.  Returns True when all RPMs were signed.

    The private key stored in GPGKey.private_key must be passphrase-free
    (generated with %no-protection — the default for keys created in flat-manager).
    """
    import stat

    if not gpg_key or not getattr(gpg_key, 'private_key', ''):
        log_rpm_build(build, 'warning', 'No private key material available — skipping RPM signing')
        return False

    tmpdir = tempfile.mkdtemp(prefix='fmd-gpg-')
    try:
        os.chmod(tmpdir, stat.S_IRWXU)  # 0700 – gpg requires this
        env = {**os.environ, 'GNUPGHOME': tmpdir}

        # Import the private key into the temporary keyring
        proc = subprocess.run(
            ['gpg', '--batch', '--import'],
            input=gpg_key.private_key,
            text=True, capture_output=True, env=env,
        )
        if proc.returncode != 0:
            log_rpm_build(build, 'error',
                          f'GPG key import failed — RPM signing skipped: {proc.stderr.strip()}')
            return False

        # rpm --addsign uses the _gpg_path macro to locate the keyring and
        # _gpg_name to identify the key.  Use the full fingerprint for accuracy.
        all_signed = True
        for rpm_path in rpm_paths:
            proc = subprocess.run(
                [
                    'rpm',
                    '--define', f'_gpg_path {tmpdir}',
                    '--define', f'_gpg_name {gpg_key.fingerprint}',
                    '--addsign', rpm_path,
                ],
                capture_output=True, text=True, env=env,
            )
            if proc.returncode != 0:
                log_rpm_build(build, 'error',
                              f'Signing {os.path.basename(rpm_path)} failed: {proc.stderr.strip()}')
                all_signed = False
            else:
                log_rpm_build(build, 'info',
                              f'Signed {os.path.basename(rpm_path)} with key {gpg_key.key_id}')

        return all_signed
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _sign_repomd(repo_path: str, gpg_key, build) -> None:
    """
    Create a detached ASCII-armored GPG signature for repodata/repomd.xml
    and write the public key as RPM-GPG-KEY in the repo root.
    """
    import stat

    repomd_path = os.path.join(repo_path, 'repodata', 'repomd.xml')
    if not os.path.exists(repomd_path):
        log_rpm_build(build, 'warning', 'repomd.xml not found — skipping repomd signing')
        return

    tmpdir = tempfile.mkdtemp(prefix='fmd-gpg-')
    try:
        os.chmod(tmpdir, stat.S_IRWXU)
        env = {**os.environ, 'GNUPGHOME': tmpdir}

        proc = subprocess.run(
            ['gpg', '--batch', '--import'],
            input=gpg_key.private_key,
            text=True, capture_output=True, env=env,
        )
        if proc.returncode != 0:
            log_rpm_build(build, 'warning',
                          f'GPG key import for repomd signing failed: {proc.stderr.strip()}')
            return

        sig_path = repomd_path + '.asc'
        if os.path.exists(sig_path):
            os.unlink(sig_path)

        proc = subprocess.run(
            [
                'gpg', '--batch', '--armor', '--detach-sign',
                '--pinentry-mode', 'loopback', '--passphrase', '',
                '-u', gpg_key.fingerprint,
                repomd_path,
            ],
            capture_output=True, text=True, env=env,
        )
        if proc.returncode != 0:
            log_rpm_build(build, 'warning',
                          f'repomd.xml signing failed: {proc.stderr.strip()}')
            return

        log_rpm_build(build, 'info', 'Signed repodata/repomd.xml')

        # Publish the public key so clients can configure gpgcheck=1
        pub_key_path = os.path.join(repo_path, 'RPM-GPG-KEY')
        with open(pub_key_path, 'w') as fh:
            fh.write(gpg_key.public_key)
        log_rpm_build(build, 'info', f'Wrote {pub_key_path}')

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _extract_spec_version(spec_path):
    try:
        with open(spec_path) as fh:
            for line in fh:
                m = re.match(r'^\s*Version\s*:\s*(.+)', line, re.IGNORECASE)
                if m:
                    return m.group(1).strip()
    except Exception:
        pass
    return ''


# ---------------------------------------------------------------------------
# Main build task
# ---------------------------------------------------------------------------

@shared_task(bind=True, queue='build')
def rpm_build_task(self, build_id):
    """Clone the git repo and build the RPM using Mock."""
    from apps.rpm.models import RpmBuild

    try:
        build = RpmBuild.objects.select_related('package', 'distribution').get(pk=build_id)
    except RpmBuild.DoesNotExist:
        logger.error("RpmBuild %s not found", build_id)
        return

    build.status = 'building'
    build.celery_task_id = self.request.id
    build.save(update_fields=['status', 'celery_task_id'])
    _update_package_status(build.package)
    send_rpm_build_status_update(build.pk, 'building', 'Build started')

    work_dir = None
    result_dir = None
    mock_cfg_path = None
    build_root = None

    try:
        rpm_build_base = getattr(settings, 'RPM_BUILD_PATH', '') or os.path.join(settings.FLATPAK_BUILD_PATH, 'rpms')
        os.makedirs(rpm_build_base, exist_ok=True)
        build_root = tempfile.mkdtemp(prefix=f'fmd-rpm-{build.pk}-', dir=rpm_build_base)
        os.chmod(build_root, 0o700)

        work_dir = os.path.join(build_root, 'sources')
        result_dir = os.path.join(build_root, 'result')
        mock_cfg_dir = os.path.join(build_root, 'mock')
        os.makedirs(work_dir, exist_ok=True)
        os.makedirs(result_dir, exist_ok=True)
        os.makedirs(mock_cfg_dir, exist_ok=True)

        # ---- Clone ----
        log_rpm_build(
            build, 'info',
            f"Cloning {build.package.git_repo_url} @ {build.package.git_branch}",
        )
        git_cmd = (
            f"umask 0022 && git clone --depth=1 --branch {shlex.quote(build.package.git_branch)} "
            f"{shlex.quote(build.package.git_repo_url)} {shlex.quote(work_dir)}"
        )
        r = subprocess.run(['bash', '-c', git_cmd], capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            raise RuntimeError(f"git clone failed: {r.stderr.strip()}")

        commit_r = subprocess.run(
            ['git', 'rev-parse', 'HEAD'],
            cwd=work_dir, capture_output=True, text=True, timeout=10,
        )
        if commit_r.returncode == 0:
            build.source_commit = commit_r.stdout.strip()
            build.save(update_fields=['source_commit'])
        log_rpm_build(build, 'info', f"Cloned at {build.source_commit or 'unknown commit'}")

        # ---- Locate SPEC ----
        spec_path = os.path.join(work_dir, build.package.spec_file)
        if not os.path.exists(spec_path):
            raise RuntimeError(
                f"SPEC file not found in repo: {build.package.spec_file}"
            )
        if _ensure_spec_release_matches_build_number(spec_path, build.build_number):
            log_rpm_build(build, 'info', f"Adjusted SPEC Release to {build.build_number}%{{?dist}}")
        else:
            log_rpm_build(build, 'warning', 'Could not adjust SPEC Release field automatically')
        sources_dir = os.path.dirname(spec_path)

        # ---- Create Mock config ----
        dist = build.distribution
        os.makedirs(dist.repo_path, exist_ok=True)
        mock_cfg_path = _create_mock_config(
            dist.mock_config,
            build.pk,
            dist.repo_path,
            cfg_path=os.path.join(mock_cfg_dir, f'fmd-mock-{build.pk}.cfg'),
        )

        # ---- Build SRPM ----
        log_rpm_build(build, 'info', f"Building SRPM ({dist.display_name})")
        srpm_dir = os.path.join(result_dir, 'srpm')
        os.makedirs(srpm_dir)
        _run_mock(
            mock_cfg_path,
            ['--buildsrpm', '--spec', spec_path, '--sources', sources_dir,
             '--resultdir', srpm_dir, '--no-cleanup-after'],
            build,
        )

        srpms = glob.glob(os.path.join(srpm_dir, '*.src.rpm'))
        if not srpms:
            raise RuntimeError("Mock produced no SRPM — check logs above")
        srpm_path = srpms[0]
        log_rpm_build(build, 'info', f"SRPM: {os.path.basename(srpm_path)}")

        # ---- Build RPMs ----
        log_rpm_build(build, 'info', "Building RPMs from SRPM")
        rpm_dir = os.path.join(result_dir, 'rpms')
        os.makedirs(rpm_dir)
        _run_mock(
            mock_cfg_path,
            ['--rebuild', srpm_path, '--resultdir', rpm_dir],
            build,
        )

        # ---- Collect output ----
        built_rpms = [
            r for r in glob.glob(os.path.join(rpm_dir, '*.rpm'))
            if not r.endswith('.src.rpm')
        ]
        if not built_rpms:
            raise RuntimeError("Mock produced no binary RPMs")

        # ---- Copy to repo ----
        copied = []
        for rpm in built_rpms:
            dest = os.path.join(dist.repo_path, os.path.basename(rpm))
            shutil.copy2(rpm, dest)
            copied.append(os.path.basename(rpm))
            log_rpm_build(build, 'info', f"Stored {os.path.basename(rpm)}")

        # ---- Sign RPMs (if a key is configured for this package+distribution) ----
        from apps.rpm.models import RpmPackageSigningKey
        try:
            _pkg_signing = RpmPackageSigningKey.objects.select_related('signing_key').get(
                package=build.package, distribution=dist)
            signing_key = _pkg_signing.signing_key
        except RpmPackageSigningKey.DoesNotExist:
            signing_key = None
        if signing_key and signing_key.is_active:
            log_rpm_build(build, 'info',
                          f"Signing RPMs with GPG key {signing_key.key_id} ({signing_key.name})")
            full_paths = [os.path.join(dist.repo_path, fn) for fn in copied]
            _sign_rpms(full_paths, signing_key, build)
        else:
            log_rpm_build(build, 'info', "No signing key configured — RPMs will be unsigned")

        # ---- Update repo metadata ----
        log_rpm_build(build, 'info', "Updating RPM repository metadata")
        _run_createrepo(dist.repo_path, build)

        # ---- Sign repomd.xml ----
        if signing_key and signing_key.is_active:
            _sign_repomd(dist.repo_path, signing_key, build)

        # ---- Finish ----
        build.status = 'built'
        build.version = _extract_spec_version(spec_path)
        build.rpm_files = copied
        build.completed_at = timezone.now()
        build.save(update_fields=['status', 'version', 'rpm_files', 'completed_at'])
        log_rpm_build(build, 'info', f"Done — {len(copied)} RPM(s) produced.")
        log_rpm_build(build, 'info', f"Build artifacts stored in: {build_root}")

        # ---- Push to Satellite/Katello destinations ----
        _push_to_satellite_destinations(build, dist, copied)

        _update_package_status(build.package)
        send_rpm_build_status_update(build.pk, 'built', 'Build complete')

    except Exception as exc:
        logger.exception("RPM build %s failed: %s", build.pk, exc)
        build.status = 'failed'
        build.error_message = str(exc)
        build.completed_at = timezone.now()
        build.save(update_fields=['status', 'error_message', 'completed_at'])
        log_rpm_build(build, 'error', f"Build failed: {exc}")
        _update_package_status(build.package)
        send_rpm_build_status_update(build.pk, 'failed', str(exc))

    finally:
        # Keep build_root artifacts for debugging/auditability.
        # They live under RPM_BUILD_PATH and are unique per build.


# ---------------------------------------------------------------------------
# Distribution sync helper (called from views)
# ---------------------------------------------------------------------------

def sync_distributions_from_mock():
    """
    Scan /etc/mock/ for rhel-*.cfg files and upsert RpmDistribution records.
    Returns a list of (RpmDistribution, created) tuples.
    """
    from apps.rpm.models import RpmDistribution

    pattern = re.compile(r'^rhel-(\d+)-(x86_64)$')
    results = []
    mock_dir = '/etc/mock'

    if not os.path.isdir(mock_dir):
        return results

    for cfg_file in sorted(glob.glob(os.path.join(mock_dir, 'rhel-*-x86_64.cfg'))):
        stem = os.path.basename(cfg_file)[:-4]  # strip .cfg
        m = pattern.match(stem)
        if not m:
            continue
        rhel_version, arch = m.group(1), m.group(2)
        dist, created = RpmDistribution.objects.get_or_create(
            name=stem,
            defaults={
                'display_name': f"RHEL {rhel_version} ({arch})",
                'mock_config': stem,
                'arch': arch,
                'rhel_version': rhel_version,
                'is_active': True,
            },
        )
        results.append((dist, created))

    return results


# ---------------------------------------------------------------------------
# Satellite / Katello push (called after successful builds)
# ---------------------------------------------------------------------------

def _push_to_satellite_destinations(build, dist, copied_filenames):
    """
    Push each built RPM file to every Satellite/Katello destination configured
    for this (package, distribution) pair.
    """
    from apps.rpm.models import RpmPackageDistributionDestination
    from apps.rpm.satellite import push_rpm

    destinations = (
        RpmPackageDistributionDestination.objects
        .filter(package=build.package, distribution=dist)
        .select_related('repository__server')
    )
    if not destinations.exists():
        return

    for dest in destinations:
        server = dest.repository.server
        try:
            token = server.token
        except Exception as exc:
            log_rpm_build(build, 'error', f"Katello: could not decrypt token for {server.name}: {exc}")
            continue

        for filename in copied_filenames:
            rpm_path = os.path.join(dist.repo_path, filename)
            log_rpm_build(build, 'info',
                          f"Katello: pushing {filename} to {dest.repository} …")
            err = push_rpm(
                rpm_path,
                server.url,
                server.login,
                token,
                dest.repository.repository_id,
                server.ssl_verify,
            )
            if err:
                log_rpm_build(build, 'error', f"Katello push failed: {err}")
            else:
                log_rpm_build(build, 'info',
                              f"Katello: {filename} pushed successfully to {dest.repository}")


# ---------------------------------------------------------------------------
# Periodic version-check tasks (mirroring apps/flatpak/tasks.py pattern)
# ---------------------------------------------------------------------------

@shared_task(name='rpm.check_rpm_available_version')
def check_rpm_available_version_task(package_id: int):
    """
    Re-check the available (spec-file) version for a single RPM package.
    """
    import re
    from apps.rpm.models import RpmPackage

    try:
        package = RpmPackage.objects.get(pk=package_id)
    except RpmPackage.DoesNotExist:
        return

    if not package.git_repo_url or not package.spec_file:
        return

    branch = (package.git_branch or 'main').strip()

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = subprocess.run(
                ['git', 'clone', '--depth', '1', '--branch', branch,
                 '--', package.git_repo_url, tmpdir],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                logger.warning("rpm available check: git clone failed for %s", package.name)
                return

            spec_path = os.path.join(tmpdir, package.spec_file)
            if not os.path.isfile(spec_path):
                return

            with open(spec_path, 'r', errors='replace') as fh:
                spec_text = fh.read()

        version = None
        requires = []
        build_requires = []

        for line in spec_text.splitlines():
            stripped = line.strip()
            if re.match(r'^Version\s*:', stripped, re.IGNORECASE):
                raw = stripped.split(':', 1)[1].strip()
                if raw and not raw.startswith('%{'):
                    version = raw
            elif re.match(r'^Requires\s*:', stripped, re.IGNORECASE):
                req = stripped.split(':', 1)[1].strip()
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
        logger.info("rpm available check: %s → %s", package.name, version)

    except Exception:
        logger.exception("rpm available check failed for package %s", package_id)


@shared_task(name='rpm.check_all_rpm_available_versions')
def check_all_rpm_available_versions():
    """
    Periodic task: queue check_rpm_available_version_task for every active RPM package.
    Interval is re-synced from SiteConfig.available_version_check_interval_hours.
    """
    from apps.rpm.models import RpmPackage
    from apps.flatpak.models import SiteConfig

    config = SiteConfig.get()
    interval_hours = config.available_version_check_interval_hours
    _sync_rpm_periodic_task(
        'rpm.check_all_rpm_available_versions',
        'check_all_rpm_available_versions',
        interval_hours,
    )

    for pkg in RpmPackage.objects.filter(git_repo_url__isnull=False).exclude(git_repo_url=''):
        check_rpm_available_version_task.delay(pkg.pk)


@shared_task(name='rpm.check_rpm_upstream_version')
def check_rpm_upstream_version_task(package_id: int):
    """Re-check the upstream version for a single RPM package."""
    from apps.rpm.models import RpmPackage
    from apps.flatpak.tasks import _fetch_latest_upstream_tag, _run_version_script, _normalise_version

    try:
        package = RpmPackage.objects.get(pk=package_id)
    except RpmPackage.DoesNotExist:
        return

    if not package.upstream_url and not package.upstream_version_script.strip():
        return

    version = None
    error = None

    if package.upstream_version_script.strip():
        version, error = _run_version_script(package.upstream_version_script, package.name)

    if not version and package.upstream_url:
        version, error = _fetch_latest_upstream_tag(package.upstream_url)

    if not version:
        logger.warning("rpm upstream check: no version for %s: %s", package.name, error)
        return

    version = _normalise_version(version)
    package.upstream_version = version
    package.upstream_checked_at = timezone.now()
    package.save(update_fields=['upstream_version', 'upstream_checked_at'])
    logger.info("rpm upstream check: %s → %s", package.name, version)


@shared_task(name='rpm.check_all_rpm_upstream_versions')
def check_all_rpm_upstream_versions():
    """
    Periodic task: queue check_rpm_upstream_version_task for every RPM package
    that has an upstream URL or script.  Re-syncs the interval from SiteConfig.
    """
    from apps.rpm.models import RpmPackage
    from apps.flatpak.models import SiteConfig

    config = SiteConfig.get()
    interval_hours = config.upstream_version_check_interval_hours
    _sync_rpm_periodic_task(
        'rpm.check_all_rpm_upstream_versions',
        'check_all_rpm_upstream_versions',
        interval_hours,
    )

    for pkg in RpmPackage.objects.filter(
        git_repo_url__isnull=False
    ).exclude(git_repo_url=''):
        check_rpm_upstream_version_task.delay(pkg.pk)


def _sync_rpm_periodic_task(task_name: str, schedule_name: str, interval_hours: int):
    """
    Ensure a django-celery-beat IntervalSchedule+PeriodicTask exists and matches
    the requested interval.  Creates or updates as needed.
    """
    try:
        from django_celery_beat.models import PeriodicTask, IntervalSchedule
        import json as _json

        schedule, _ = IntervalSchedule.objects.get_or_create(
            every=interval_hours,
            period=IntervalSchedule.HOURS,
        )
        task_obj, created = PeriodicTask.objects.get_or_create(
            name=schedule_name,
            defaults={
                'task': task_name,
                'interval': schedule,
                'args': _json.dumps([]),
            },
        )
        if not created and task_obj.interval != schedule:
            task_obj.interval = schedule
            task_obj.save(update_fields=['interval'])
    except Exception:
        logger.exception("Could not sync periodic task %s", task_name)
