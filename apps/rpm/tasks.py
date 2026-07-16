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


def _sanitize_log_message(message):
    message = _ANSI_ESC_RE.sub('', message)
    if '\r' in message:
        message = message.split('\r')[-1]
    message = ''.join(ch for ch in message if ord(ch) <= 0xFFFF)
    return message.strip()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def log_rpm_build(build, level, message):
    """Persist a log line for an RPM build and echo it to the Python logger."""
    from apps.rpm.models import RpmBuildLog
    message = _sanitize_log_message(message)
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

def _extract_dnf_conf_main_section(base_config: str) -> tuple[str, str]:
    """
    Read ``/etc/mock/{base_config}.cfg``, find the ``config_opts['dnf.conf']``
    or ``config_opts['yum.conf']`` assignment, strip all repo stanzas from it
    keeping only ``[main]``, and return ``(key, cleaned_value)``.

    The returned key is whichever of ``'dnf.conf'`` / ``'yum.conf'`` was found
    (``'dnf.conf'`` takes precedence).  Falls back to ``('dnf.conf', '[main]\\n')``
    if the file cannot be read or parsed.
    """
    cfg_file = f'/etc/mock/{base_config}.cfg'
    try:
        with open(cfg_file, encoding='utf-8', errors='replace') as fh:
            content = fh.read()
    except OSError as exc:
        logger.warning("_extract_dnf_conf_main_section: cannot read %s: %s", cfg_file, exc)
        return ('dnf.conf', '[main]\n')

    for key in ('dnf.conf', 'yum.conf'):
        pattern = re.compile(
            r"config_opts\[(['\"])" + re.escape(key) + r"\1\]\s*=\s*[\"']{3}(.*?)[\"']{3}",
            re.DOTALL,
        )
        m = pattern.search(content)
        if not m:
            continue
        raw_conf = m.group(2)
        # Keep only the [main] section — everything before the first non-[main] section
        main_m = re.search(r'\[main\].*?(?=\n\[|\Z)', raw_conf, re.DOTALL)
        if not main_m:
            return (key, '[main]\n')
        return (key, '\n' + main_m.group(0).strip() + '\n')

    logger.warning("_extract_dnf_conf_main_section: no dnf.conf/yum.conf found in %s", cfg_file)
    return ('dnf.conf', '[main]\n')


def _is_inline_gpg_key_content(value: str) -> bool:
    """Return True when *value* looks like inline key material, not a URL/path."""
    if not value:
        return False
    v = value.strip()
    if not v:
        return False
    # URL/path values belong directly in gpgkey=, but multiline key content must
    # be injected through config_opts['files'] and referenced via file://.
    if v.startswith('http://') or v.startswith('https://') or v.startswith('file://'):
        return False
    if v.startswith('/'):
        return False
    if '\n' in v or '\r' in v:
        return True
    if 'BEGIN PGP PUBLIC KEY BLOCK' in v:
        return True
    return False


def _is_inline_ssl_pem_content(value: str) -> bool:
    """Return True when *value* looks like inline PEM/cert/key content."""
    if not value:
        return False
    v = value.strip()
    if not v:
        return False
    # URL/path values belong directly in ssl* fields in repo stanzas.
    if v.startswith('http://') or v.startswith('https://') or v.startswith('file://'):
        return False
    if v.startswith('/'):
        return False
    # Inline PEM/key content is often multiline and may not start directly with
    # "-----BEGIN" (e.g. bag attributes or preamble text).
    if '\n' in v or '\r' in v:
        return True
    if '-----BEGIN ' in v:
        return True
    return False


def _create_mock_config(base_config, build, local_repo_path, allow_internet_access=False, cleanup_on_success=True, cfg_path=None):
    """
    Write a temporary Mock config that inherits from the stock RHEL config and
    adds the repos selected for *build* plus the distribution's local built-RPMs
    repo (when it already has metadata).

    RHSM-managed repos can be provided by mock's subscription-manager plugin,
    while repos with an explicit URL can be emitted directly into yum.conf.

    Repos with an explicit URL (EPEL, third-party, Satellite, or RHSM repos
    that were discovered with a concrete baseurl) get a full stanza including that
    URL so that builds work on machines that are not RHSM-subscribed.
    """
    build_id = build.pk
    cfg = f"include('/etc/mock/{base_config}.cfg')\n\n"
    cfg += f"config_opts['uniqueext'] = 'fmd{build_id}'\n"
    cfg += f"config_opts['rpmbuild_networking'] = {bool(allow_internet_access)}\n"
    cfg += f"config_opts['use_host_resolv'] = {bool(allow_internet_access)}\n"
    cfg += f"config_opts['cleanup_on_success'] = {bool(cleanup_on_success)}\n"

    # Read the base mock config, strip all repo stanzas from its dnf.conf /
    # yum.conf keeping only [main], and assign the cleaned value back.
    # This ensures ONLY repos we explicitly add below are active.
    _conf_key, _main_section = _extract_dnf_conf_main_section(base_config)
    cfg += f"\nconfig_opts[{_conf_key!r}] = {_main_section!r}\n"


    # Resolve cfg_path early so we can write gpgkey files alongside it
    if cfg_path is None:
        cfg_path = os.path.join(tempfile.gettempdir(), f'fmd-mock-{build_id}.cfg')
    os.makedirs(os.path.dirname(cfg_path), exist_ok=True)

    selected_repos = list(build.selected_repos.all())

    # When RHSM or Satellite/Katello repos are selected, enable mock's
    # subscription_manager plugin.  This plugin copies the host's RHSM
    # entitlement certificates (/etc/pki/consumer/ and /etc/pki/entitlement/)
    # into both the bootstrap and the main chroot so that dnf can authenticate
    # against subscription-gated repos (including Satellite/Katello CDN repos
    # that require a valid consumer cert for access).
    has_rhsm_repos = any(r.source in ('rhsm', 'satellite') for r in selected_repos)
    if has_rhsm_repos:
        cfg += "\nconfig_opts['plugin_conf']['subscription_manager_enable'] = True\n"

    # Inject GPG keys and SSL certs/keys into the chroot via config_opts['files'].
    # Mock writes these files into the chroot before dnf runs, so the paths we
    # reference in the repo stanzas exist inside the chroot, not on the host.
    # GPG keys go to /etc/pki/fmd/gpgkeys/, SSL material to /etc/pki/fmd/certs/.
    # Ensure config_opts['files'] exists as a dict (not all base configs define it).
    cfg += "\nconfig_opts['files'] = config_opts.get('files', {})\n"
    cfg += "config_opts['bootstrap_files'] = config_opts.get('bootstrap_files', {})\n"
    injected_host_root = os.path.join(os.path.dirname(cfg_path), 'injected-pki')
    injected_host_pki = os.path.join(injected_host_root, 'fmd')
    os.makedirs(injected_host_pki, exist_ok=True)

    def _write_injected_host_file(chroot_path: str, content: str) -> None:
        """Write injected file content into a host dir that can be bind-mounted."""
        prefix = '/etc/pki/fmd/'
        if not chroot_path.startswith(prefix):
            return
        rel_path = chroot_path[len(prefix):]
        host_path = os.path.join(injected_host_pki, rel_path)
        os.makedirs(os.path.dirname(host_path), exist_ok=True)
        with open(host_path, 'w', encoding='utf-8') as fh:
            fh.write(content)

    injected_files = []
    for repo in selected_repos:
        safe_id = re.sub(r'[^a-zA-Z0-9_.-]', '_', repo.repo_id)
        # Only inject a key file when we have actual armored key content.
        # If gpgkey is a URL/path, it should stay as-is in the repo stanza.
        if repo.gpgcheck and repo.gpgkey and _is_inline_gpg_key_content(repo.gpgkey):
            chroot_gpg = f'/etc/pki/fmd/gpgkeys/{safe_id}.gpg'
            pem_val = repo.gpgkey.rstrip('\n') + '\n'
            cfg += f"\nconfig_opts['files'][{chroot_gpg!r}] = \"\"\"\\\n{pem_val}\"\"\"\n"
            cfg += f"config_opts['bootstrap_files'][{chroot_gpg!r}] = \"\"\"\\\n{pem_val}\"\"\"\n"
            _write_injected_host_file(chroot_gpg, pem_val)
            injected_files.append((repo.repo_id, 'gpgkey', chroot_gpg))
        for ssl_field, suffix in (
            ('sslcacert', 'cacert.pem'),
            ('sslclientcert', 'clientcert.pem'),
            ('sslclientkey', 'clientkey.pem'),
        ):
            val = getattr(repo, ssl_field, '') or ''
            if val and _is_inline_ssl_pem_content(val):
                chroot_path = f'/etc/pki/fmd/certs/{safe_id}-{suffix}'
                pem_val = val.rstrip('\n') + '\n'
                cfg += f"\nconfig_opts['files'][{chroot_path!r}] = \"\"\"\\\n{pem_val}\"\"\"\n"
                cfg += f"config_opts['bootstrap_files'][{chroot_path!r}] = \"\"\"\\\n{pem_val}\"\"\"\n"
                _write_injected_host_file(chroot_path, pem_val)
                injected_files.append((repo.repo_id, ssl_field, chroot_path))

    if injected_files:
        # files[] payload is created only during full chroot initialization in Mock.
        # With root_cache, a reused cached root can skip that step and miss our
        # injected cert/key files. Disable root_cache for this build config.
        cfg += "\nconfig_opts['plugin_conf']['root_cache_enable'] = False\n"
        # Bind-mount injected cert/key files into both main and bootstrap
        # chroots as a robust fallback to files[] injection.
        cfg += "config_opts['plugin_conf']['bind_mount_enable'] = True\n"
        cfg += (
            "config_opts['plugin_conf']['bind_mount_opts']['dirs'].append(" 
            f"({injected_host_pki!r}, '/etc/pki/fmd'))\n"
        )
        cfg += "config_opts['bootstrap_plugin_conf']['bind_mount_enable'] = True\n"
        cfg += (
            "config_opts['bootstrap_plugin_conf']['bind_mount_opts']['dirs'].append(" 
            f"({injected_host_pki!r}, '/etc/pki/fmd'))\n"
        )

    for repo in selected_repos:
        safe_id = re.sub(r'[^a-zA-Z0-9_.-]', '_', repo.repo_id)
        cfg += f"\nconfig_opts[{_conf_key!r}] += \"\"\"\n"
        cfg += f"[{repo.repo_id}]\n"
        cfg += f"name={repo.name}\n"
        if repo.baseurl:
            cfg += f"baseurl={repo.baseurl}\n"
        if repo.metalink:
            cfg += f"metalink={repo.metalink}\n"
        if repo.mirrorlist:
            cfg += f"mirrorlist={repo.mirrorlist}\n"
        cfg += "enabled=1\n"
        cfg += f"gpgcheck={1 if repo.gpgcheck else 0}\n"
        if repo.gpgcheck and repo.gpgkey and _is_inline_gpg_key_content(repo.gpgkey):
            # Reference the chroot-internal path injected via files[] above
            cfg += f"gpgkey=file:///etc/pki/fmd/gpgkeys/{safe_id}.gpg\n"
        elif repo.gpgcheck and repo.gpgkey:
            # Preserve URL/path style gpgkey values from repo discovery.
            cfg += f"gpgkey={repo.gpgkey}\n"
        elif repo.source == 'epel' and repo.gpgcheck:
            rhel_ver = build.distribution.rhel_version
            cfg += f"gpgkey=https://dl.fedoraproject.org/pub/epel/RPM-GPG-KEY-EPEL-{rhel_ver}\n"
        # Reference the chroot-internal cert paths injected via files[] above.
        for ssl_field, suffix in (
            ('sslcacert', 'cacert.pem'),
            ('sslclientcert', 'clientcert.pem'),
            ('sslclientkey', 'clientkey.pem'),
        ):
            val = getattr(repo, ssl_field, '') or ''
            if not val:
                continue
            if _is_inline_ssl_pem_content(val):
                # PEM was injected into chroot at this path
                cfg += f"{ssl_field}=/etc/pki/fmd/certs/{safe_id}-{suffix}\n"
            elif val.startswith('/etc/pki/') or val.startswith('/etc/rhsm/'):
                # Standard system path — subscription_manager plugin copies
                # /etc/pki/entitlement/, /etc/pki/consumer/ and /etc/rhsm/
                # into the chroot at the same paths, so this reference is valid.
                cfg += f"{ssl_field}={val}\n"
            else:
                # Non-system path (e.g. stale host build-dir path from old data)
                # — skip it; trigger a repo re-sync to populate PEM content.
                logger.warning(
                    "_create_mock_config: repo %s %s value %r does not look like a "
                    "system cert path or PEM content — skipping (re-sync to fix)",
                    repo.repo_id, ssl_field, val,
                )
        if any(getattr(repo, f, '') for f in ('sslcacert', 'sslclientcert', 'sslclientkey')):
            cfg += "sslverify=1\n"
        cfg += "\"\"\"\n"

    repodata = os.path.join(local_repo_path, 'repodata', 'repomd.xml')
    if os.path.exists(repodata):
        cfg += (
            f"\nconfig_opts[{_conf_key!r}] += \"\"\"\n"
            f"[flat-manager-built-{build_id}]\n"
            f"name=Flat Manager Built RPMs\n"
            f"baseurl=file://{local_repo_path}\n"
            "enabled=1\n"
            "gpgcheck=0\n"
            "priority=1\n"
            "\"\"\"\n"
        )

    with open(cfg_path, 'w') as fh:
        fh.write(cfg)

    # Log the files[] section of the generated config for diagnostics
    files_lines = [l for l in cfg.splitlines() if "config_opts['files']" in l]
    bootstrap_files_lines = [l for l in cfg.splitlines() if "config_opts['bootstrap_files']" in l]
    files_summary = (
        f"Mock config files[] entries ({len(files_lines)}), "
        f"bootstrap_files[] entries ({len(bootstrap_files_lines)}): "
        + '; '.join(files_lines + bootstrap_files_lines)
    )
    logger.info("_create_mock_config: build %s — %s", build_id, files_summary)
    log_rpm_build(build, 'info', files_summary)
    if injected_files:
        log_rpm_build(build, 'info', "Disabled mock root_cache because files[] injection is used")
        log_rpm_build(build, 'info', f"Bind-mounting injected PKI directory into chroot: {injected_host_pki} -> /etc/pki/fmd")
        log_rpm_build(build, 'info', f"Mock config will inject {len(injected_files)} file(s) into chroot:")
        for repo_id, field, path in injected_files:
            log_rpm_build(build, 'info', f"[files] {repo_id} {field} -> {path}")
    else:
        log_rpm_build(build, 'info', "Mock config has no inline files[] injections for this build")

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


def _fetch_spec_sources(spec_path: str, sources_dir: str, build) -> None:
    """
    Download URL-based Source/Patch entries from a SPEC file into *sources_dir*
    using ``spectool --get-files`` (from the ``rpmdevtools`` package).

    If spectool is not installed the step is skipped with a warning — mock will
    then fail with a clear "source not found" error rather than a silent no-op.
    If spectool is installed but exits non-zero (e.g. a URL is unreachable) a
    RuntimeError is raised so the build fails with a meaningful message.
    """
    spectool = shutil.which('spectool')
    if not spectool:
        log_rpm_build(build, 'warning',
                      'spectool not found (install rpmdevtools) — URL sources will not be fetched automatically')
        return

    cmd = [spectool, '--get-files', '--directory', sources_dir, spec_path]
    log_rpm_build(build, 'info', 'Fetching sources: ' + ' '.join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    for line in (result.stdout + result.stderr).splitlines():
        line = line.strip()
        if line:
            log_rpm_build(build, 'info', line)
    if result.returncode != 0:
        raise RuntimeError(f"spectool failed (exit {result.returncode}) — could not fetch sources")


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
        # Surface the detailed rpmbuild output from mock's build.log, which is
        # written to the resultdir but never streamed to mock's stdout.
        try:
            rd_idx = extra_args.index('--resultdir')
            resultdir = extra_args[rd_idx + 1]
            build_log = os.path.join(resultdir, 'build.log')
            if os.path.exists(build_log):
                log_rpm_build(build, 'info', '--- build.log ---')
                with open(build_log, encoding='utf-8', errors='replace') as fh:
                    for line in fh:
                        line = line.rstrip('\n')
                        if line:
                            log_rpm_build(build, 'info', line)
        except (ValueError, IndexError, OSError):
            pass
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
        build.result_dir = result_dir
        build.save(update_fields=['result_dir'])

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

        # ---- Fetch URL sources ----
        # Download any SourceN / PatchN entries that are http(s):// URLs and
        # are not already present in the cloned repo.  This is a no-op when
        # all sources are bundled in git (e.g. patches + tarball committed).
        _fetch_spec_sources(spec_path, sources_dir, build)

        # ---- Create Mock config ----
        dist = build.distribution
        os.makedirs(dist.repo_path, exist_ok=True)
        mock_cfg_path = _create_mock_config(
            dist.mock_config,
            build,
            dist.repo_path,
            allow_internet_access=build.package.allow_internet_access,
            cleanup_on_success=build.package.cleanup_on_success,
            cfg_path=os.path.join(mock_cfg_dir, f'fmd-mock-{build.pk}.cfg'),
        )
        log_rpm_build(
            build,
            'info',
            f"Internet access {'enabled' if build.package.allow_internet_access else 'disabled'} for this build",
        )
        log_rpm_build(
            build,
            'info',
            f"Chroot cleanup on success: {'enabled' if build.package.cleanup_on_success else 'disabled'}",
        )

        # ---- Verify injected GPG/SSL files exist inside the chroot ----
        _check_paths = []
        for repo in build.selected_repos.all():
            safe_id = re.sub(r'[^a-zA-Z0-9_.-]', '_', repo.repo_id)
            if repo.gpgcheck and repo.gpgkey and _is_inline_gpg_key_content(repo.gpgkey):
                _check_paths.append(f'/etc/pki/fmd/gpgkeys/{safe_id}.gpg')
            for ssl_field, suffix in (
                ('sslcacert', 'cacert.pem'),
                ('sslclientcert', 'clientcert.pem'),
                ('sslclientkey', 'clientkey.pem'),
            ):
                val = getattr(repo, ssl_field, '') or ''
                if val and _is_inline_ssl_pem_content(val):
                    _check_paths.append(f'/etc/pki/fmd/certs/{safe_id}-{suffix}')
        if _check_paths:
            log_rpm_build(build, 'info', "Verifying GPG/SSL files inside mock chroot…")
            try:
                # mock --shell wraps command text; avoid single-quote based
                # escaping here because it can interfere with that wrapper.
                quoted_paths = ' '.join(f'"{p}"' for p in _check_paths)
                check_script = (
                    f'for p in {quoted_paths}; do '
                    'if [ -f "$p" ]; then '
                    'echo "FMDCK_OK: $p"; '
                    'else '
                    'echo "FMDCK_MISSING: $p"; '
                    'fi; '
                    'done'
                )
                check_result = subprocess.run(
                    ['mock', '-r', mock_cfg_path, '--no-bootstrap-chroot', '--shell', check_script],
                    capture_output=True, text=True, timeout=120,
                )
                any_missing = False
                raw_output = check_result.stdout + check_result.stderr
                logger.debug("cert-check raw output: %r", raw_output[:2000])
                ok_paths = re.findall(r'(?m)^FMDCK_OK:\s*([^\r\n]+)$', raw_output)
                missing_paths = re.findall(r'(?m)^FMDCK_MISSING:\s*([^\r\n]+)$', raw_output)
                if not ok_paths and not missing_paths:
                    log_rpm_build(build, 'warning',
                                  "[cert-check] No FMDCK markers found in mock output; "
                                  "capturing first output chunk for debugging")
                    for line in raw_output.splitlines()[:20]:
                        line = line.strip()
                        if line:
                            log_rpm_build(build, 'info', f"[cert-check raw] {line}")
                for path in ok_paths:
                    path = path.strip()
                    if path:
                        log_rpm_build(build, 'info', f"[cert-check] OK: {path}")
                for path in missing_paths:
                    path = path.strip()
                    if path:
                        log_rpm_build(build, 'warning', f"[cert-check] MISSING: {path}")
                        any_missing = True
                if any_missing:
                    log_rpm_build(build, 'warning',
                                  "Some GPG/SSL files are missing in the chroot — "
                                  "try re-syncing repositories to refresh cert content")
            except Exception as _exc:
                log_rpm_build(build, 'warning', f"Could not verify chroot GPG/SSL files: {_exc}")

        # ---- Log active repos inside the chroot ----
        log_rpm_build(build, 'info', "Querying enabled repositories inside mock chroot…")
        try:
            repolist_result = subprocess.run(
                ['mock', '-r', mock_cfg_path, '--no-bootstrap-chroot', '--shell', 'dnf repolist --enabled -v 2>/dev/null || yum repolist enabled -v 2>/dev/null || echo "(repolist unavailable)"'],
                capture_output=True, text=True, timeout=120,
            )
            repolist_output = (repolist_result.stdout + repolist_result.stderr).strip()
            for line in repolist_output.splitlines():
                line = line.strip()
                if line:
                    log_rpm_build(build, 'info', f"[repolist] {line}")
        except Exception as _exc:
            log_rpm_build(build, 'warning', f"Could not query chroot repolist: {_exc}")

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
        pass


# ---------------------------------------------------------------------------
# Repository discovery
# ---------------------------------------------------------------------------

# EPEL metalink templates keyed by RHEL major version.
# EPEL repos are always offered alongside repos discovered from the UBI
# container; they are pre-populated with a public metalink so builds can
# use them on machines without a full Red Hat subscription.
_EPEL_REPOS: dict[str, dict] = {
    '7': {
        'repo_id': 'epel',
        'name': 'Extra Packages for Enterprise Linux 7',
        'metalink': 'https://mirrors.fedoraproject.org/metalink?repo=epel-7&arch=$basearch',
        'gpgcheck': True, 'source': 'epel', 'default_enabled': False,
    },
    '8': {
        'repo_id': 'epel',
        'name': 'Extra Packages for Enterprise Linux 8',
        'metalink': 'https://mirrors.fedoraproject.org/metalink?repo=epel-8&arch=$basearch',
        'gpgcheck': True, 'source': 'epel', 'default_enabled': False,
    },
    '9': {
        'repo_id': 'epel',
        'name': 'Extra Packages for Enterprise Linux 9',
        'metalink': 'https://mirrors.fedoraproject.org/metalink?repo=epel-9&arch=$basearch',
        'gpgcheck': True, 'source': 'epel', 'default_enabled': False,
    },
    '10': {
        'repo_id': 'epel',
        'name': 'Extra Packages for Enterprise Linux 10',
        'metalink': 'https://mirrors.fedoraproject.org/metalink?repo=epel-10&arch=$basearch',
        'gpgcheck': True, 'source': 'epel', 'default_enabled': False,
    },
}

def _classify_rpm_repo_source(repo: dict) -> str:
    """Classify a discovered repo as RHSM, Satellite/Katello, EPEL, or third-party."""
    repo_id = (repo.get('repo_id') or '').lower()
    name = (repo.get('name') or '').lower()
    repo_file = (repo.get('repo_file') or '').lower()
    baseurl = (repo.get('baseurl') or '').lower()
    metalink = (repo.get('metalink') or '').lower()
    mirrorlist = (repo.get('mirrorlist') or '').lower()
    url = ' '.join(v for v in (baseurl, metalink, mirrorlist) if v)

    if repo_id == 'epel' or 'fedoraproject.org/metalink?repo=epel' in url:
        return 'epel'
    if '/pulp/' in url or '/pulp/' in repo_file or '/katello/' in url or '/katello/' in repo_file:
        return 'satellite'
    if 'cdn-ubi.redhat.com/' in url or repo_file.endswith('/ubi.repo'):
        return 'ubi'
    if (
        'cdn.redhat.com/' in url
        or repo_file.endswith('/redhat.repo')
        or name.startswith('red hat')
    ):
        return 'rhsm'
    return 'third_party'


def _parse_dnf_repolist_verbose(output: str) -> list[dict]:
    """Parse ``dnf repolist -v --all`` output into repository metadata dicts."""
    repos: list[dict] = []
    current: dict | None = None

    def _finish_current():
        nonlocal current
        if not current or not current.get('repo_id'):
            current = None
            return
        current['source'] = _classify_rpm_repo_source(current)
        current['default_enabled'] = False
        current['gpgcheck'] = str(current.get('gpgcheck_raw', '1')).strip() not in ('0', 'false', 'False')
        repos.append({
            'repo_id': current.get('repo_id', ''),
            'name': current.get('name') or current.get('repo_id', ''),
            'baseurl': current.get('baseurl', ''),
            'metalink': current.get('metalink', ''),
            'mirrorlist': current.get('mirrorlist', ''),
            'gpgcheck': current['gpgcheck'],
            'gpgkey': current.get('gpgkey', ''),
            'source': current['source'],
            'default_enabled': current['default_enabled'],
        })
        current = None

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith('Last ') or line.startswith('Updating ') or line.startswith('Red Hat Subscription'):
            continue
        if line.startswith('Repo-id'):
            _finish_current()
            current = {'repo_id': line.split(':', 1)[1].strip().split('/')[0]}
            continue
        if current is None or ':' not in line:
            continue
        key, value = line.split(':', 1)
        key = key.strip().lower()
        value = value.strip()
        if key == 'repo-name':
            current['name'] = value
        elif key == 'repo-baseurl':
            current['baseurl'] = value
        elif key == 'repo-metalink':
            current['metalink'] = value
        elif key in ('repo-mirrors', 'repo-mirrorlist'):
            current['mirrorlist'] = value
        elif key == 'repo-filename':
            current['repo_file'] = value
        elif key == 'repo-status':
            current['status_raw'] = value
        elif key == 'repo-gpgcheck':
            current['gpgcheck_raw'] = value
        elif key in ('repo-gpgkey', 'repo-gpg-key'):
            current['gpgkey'] = value

    _finish_current()
    return repos


def _parse_ubi_repo_file(output: str, arch: str) -> list[dict]:
    """Parse ``/etc/yum.repos.d/ubi.repo`` content into repository metadata dicts."""
    repos: list[dict] = []
    current: dict | None = None

    def _expand(value: str) -> str:
        return value.replace('$basearch', arch).replace('${basearch}', arch)

    def _finish_current():
        nonlocal current
        if not current or not current.get('repo_id'):
            current = None
            return
        current['repo_id'] = _expand(current['repo_id'])
        current['baseurl'] = _expand(current.get('baseurl', ''))
        current['metalink'] = _expand(current.get('metalink', ''))
        current['mirrorlist'] = _expand(current.get('mirrorlist', ''))
        current['source'] = _classify_rpm_repo_source(current)
        current['default_enabled'] = False
        current['gpgcheck'] = str(current.get('gpgcheck_raw', '1')).strip() not in ('0', 'false', 'False')
        repos.append({
            'repo_id': current.get('repo_id', ''),
            'name': current.get('name') or current.get('repo_id', ''),
            'baseurl': current.get('baseurl', ''),
            'metalink': current.get('metalink', ''),
            'mirrorlist': current.get('mirrorlist', ''),
            'gpgcheck': current['gpgcheck'],
            'gpgkey': current.get('gpgkey', ''),
            'sslcacert': current.get('sslcacert', ''),
            'sslclientcert': current.get('sslclientcert', ''),
            'sslclientkey': current.get('sslclientkey', ''),
            'source': current['source'],
            'default_enabled': current['default_enabled'],
        })
        current = None

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('[') and line.endswith(']'):
            _finish_current()
            current = {'repo_id': line[1:-1].strip(), 'repo_file': '/etc/yum.repos.d/ubi.repo'}
            continue
        if current is None or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip().lower()
        value = value.strip()
        if key == 'name':
            current['name'] = value
        elif key == 'baseurl':
            current['baseurl'] = value
        elif key == 'metalink':
            current['metalink'] = value
        elif key == 'mirrorlist':
            current['mirrorlist'] = value
        elif key == 'enabled':
            current['enabled_raw'] = value
        elif key == 'gpgcheck':
            current['gpgcheck_raw'] = value
        elif key == 'gpgkey':
            current['gpgkey'] = _expand(value)
        elif key == 'sslcacert':
            current['sslcacert'] = value
        elif key == 'sslclientcert':
            current['sslclientcert'] = value
        elif key == 'sslclientkey':
            current['sslclientkey'] = value

    _finish_current()
    return repos


def _fetch_gpgkey_files_from_container(image: str, file_paths: list) -> dict:
    """
    Retrieve GPG key material for a set of paths/URLs from inside *image*.

    - Absolute paths (``/etc/pki/…``) and ``file://`` paths are cat-ted.
    - ``http://`` / ``https://`` URLs are downloaded with curl (falling back
      to wget) inside the container so the key is fetched in the same network
      context as the container.

    Returns a ``{path_or_url: armored_key_content}`` dict.
    Entries that are unreadable / unreachable are silently omitted.
    """
    if not file_paths:
        return {}
    unique_paths = sorted(set(file_paths))
    logger.info(
        "_fetch_gpgkey_files_from_container: image=%s, fetching %d path(s)/URL(s): %s",
        image, len(unique_paths), unique_paths,
    )
    parts = []
    for p in unique_paths:
        sep = f'===FMDK:{p}==='
        if p.startswith('http://') or p.startswith('https://'):
            # Try curl first, fall back to wget; both write to stdout.
            # SSL verification is disabled (-k / --no-check-certificate) because
            # GPG key files are cryptographically verified by rpm itself and
            # the container may not have the CA bundle for internal servers.
            fetch = (
                f'curl -fsSLk "{p}" 2>/dev/null || wget -qO- --no-check-certificate "{p}" 2>/dev/null || true'
            )
        else:
            # Local path (strip leading file:// if present)
            local = p[7:] if p.startswith('file://') else p
            fetch = f'cat "{local}" 2>/dev/null || true'
        parts.append(f'printf "%s\\n" "{sep}"; {fetch}; printf "%s\\n" "===FMDEND==="')
    script = '; '.join(parts)
    try:
        result = subprocess.run(
            ['podman', 'run', '--rm', '--quiet', image, 'sh', '-c', script],
            capture_output=True, text=True, timeout=60,
        )
    except Exception as exc:
        logger.debug("_fetch_gpgkey_files_from_container failed: %s", exc)
        return {}

    logger.debug(
        "_fetch_gpgkey_files_from_container: raw output (%d bytes): %s",
        len(result.stdout), result.stdout[:1000],   
    )
    contents: dict = {}
    current_path: str | None = None
    current_lines: list = []
    for line in result.stdout.splitlines():
        if line.startswith('===FMDK:') and line.endswith('==='):
            if current_path is not None:
                contents[current_path] = '\n'.join(current_lines).strip()
            current_path = line[8:-3]
            current_lines = []
        elif line == '===FMDEND===':
            if current_path is not None:
                contents[current_path] = '\n'.join(current_lines).strip()
            current_path = None
            current_lines = []
        elif current_path is not None:
            current_lines.append(line)

    result = {k: v for k, v in contents.items() if v}
    logger.info(
        "_fetch_gpgkey_files_from_container: retrieved key content for %d/%d path(s): %s",
        len(result), len(unique_paths),
        {k: f'{len(v)} chars' for k, v in result.items()},
    )
    return result


def _enrich_repos_with_gpgkey_content(repos: list, image: str, arch: str) -> None:
    """
    Replace gpgkey values in *repos* with the actual armored GPG key content
    fetched from *image*.  Handles:

    - ``file:///path`` and absolute paths  → cat inside the container
    - ``http://`` / ``https://`` URLs      → downloaded inside the container

    Already-armored content (starts with ``-----BEGIN``) is left unchanged.
    Modifies *repos* in-place.
    """
    keys_to_fetch: list = []
    for repo in repos:
        gpgkey_val = (repo.get('gpgkey') or '').strip()
        if not gpgkey_val or gpgkey_val.startswith('-----BEGIN'):
            # Already armored key material (or empty) should be left as-is.
            continue
        for token in gpgkey_val.split():
            keys_to_fetch.append(token)

    if not keys_to_fetch:
        logger.info("_enrich_repos_with_gpgkey_content: no gpgkey paths/URLs to fetch")
        return

    logger.info(
        "_enrich_repos_with_gpgkey_content: will fetch %d gpgkey path(s)/URL(s): %s",
        len(keys_to_fetch), keys_to_fetch,
    )

    key_contents = _fetch_gpgkey_files_from_container(image, keys_to_fetch)
    if not key_contents:
        return

    for repo in repos:
        gpgkey_val = repo.get('gpgkey', '')
        if not gpgkey_val:
            continue
        if gpgkey_val.lstrip().startswith('-----BEGIN'):
            continue
        key_parts: list = []
        already_armored: list = []
        for token in gpgkey_val.split():
            if token.startswith('-----BEGIN'):
                already_armored.append(token)
            elif token in key_contents:
                key_parts.append(key_contents[token])
        combined = already_armored + key_parts
        if combined:
            repo['gpgkey'] = '\n'.join(combined)


def _enrich_repos_with_ssl_cert_content(repos: list, image: str) -> None:
    """
    Replace ``sslcacert`` / ``sslclientcert`` / ``sslclientkey`` path values
    in *repos* with the actual PEM content fetched from *image*.

    The paths come from the ``.repo`` files inside the container
    (e.g. ``/etc/pki/entitlement/12345.pem``).  We cat them from inside the
    same container so the correct entitlement certs are used.

    Already-PEM content (starts with ``-----BEGIN``) is left unchanged.
    Modifies *repos* in-place.
    """
    paths_to_fetch: list = []
    for repo in repos:
        for field in ('sslcacert', 'sslclientcert', 'sslclientkey'):
            val = repo.get(field, '')
            if val and not val.startswith('-----BEGIN'):
                paths_to_fetch.append(val)

    if not paths_to_fetch:
        logger.info("_enrich_repos_with_ssl_cert_content: no SSL cert paths to fetch")
        return

    logger.info(
        "_enrich_repos_with_ssl_cert_content: will fetch %d SSL cert path(s) from %s: %s",
        len(paths_to_fetch), image, paths_to_fetch,
    )

    cert_contents = _fetch_gpgkey_files_from_container(image, paths_to_fetch)
    if not cert_contents:
        logger.warning("_enrich_repos_with_ssl_cert_content: could not fetch any SSL cert content from container")
        return

    for repo in repos:
        for field in ('sslcacert', 'sslclientcert', 'sslclientkey'):
            val = repo.get(field, '')
            if not val or val.startswith('-----BEGIN'):
                continue
            if val in cert_contents:
                repo[field] = cert_contents[val]
                logger.info(
                    "_enrich_repos_with_ssl_cert_content: repo %s %s — fetched %d chars of PEM content",
                    repo['repo_id'], field, len(cert_contents[val]),
                )
            else:
                logger.warning(
                    "_enrich_repos_with_ssl_cert_content: repo %s %s — could not fetch content for path %r",
                    repo['repo_id'], field, val,
                )


def _discover_repos_via_container(rhel_version: str, arch: str) -> list[dict] | None:
    """
    Start a plain UBI container matching *rhel_version*, run
    ``dnf repolist --all -v`` inside it, and return the detected repos as a
    list of dicts suitable for upsert into ``RpmRepository``.

    Returns ``None`` when podman is unavailable, the container image cannot
    be pulled, or no repos are found.
    """
    image = f'registry.access.redhat.com/ubi{rhel_version}/ubi:latest'
    # --disablerepo='*' prevents dnf from fetching any package metadata while
    # still listing all configured repos — makes discovery much faster.
    repo_cmd = ['dnf', '--disablerepo=*', 'repolist', '--all', '-v']
    if str(rhel_version) == '7':
        repo_cmd = ['yum', '--disablerepo=*', 'repolist', 'all', '-v']
    cmd = ['podman', 'run', '--rm', '--quiet', image, *repo_cmd]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("Container repo discovery failed for RHEL %s: %s", rhel_version, exc)
        return None

    # returncode 1 can mean "no repos found" — still try to parse
    if result.returncode not in (0, 1):
        logger.warning(
            "Container repo discovery: podman exited %d for RHEL %s\nstdout: %s\nstderr: %s",
            result.returncode, rhel_version,
            result.stdout[:500], result.stderr[:500],
        )
        return None

    parsed = _parse_dnf_repolist_verbose(result.stdout)
    if parsed:
        logger.info(
            "Container repo discovery: RHEL %s — dnf repolist found %d repo(s): %s",
            rhel_version, len(parsed),
            ', '.join(r['repo_id'] for r in parsed),
        )
        # dnf repolist -v does not include gpgkey.  Get the gpgkey= values from
        # the .repo files inside the container, then fetch the actual key files
        # they reference and store the armored key content in the parsed dicts.
        try:
            gk_result = subprocess.run(
                ['podman', 'run', '--rm', '--quiet', image,
                 'sh', '-lc', 'dnf --disablerepo="*" repolist --all 2>/dev/null; cat /etc/yum.repos.d/*.repo 2>/dev/null || true'],
                capture_output=True, text=True, timeout=60,
            )
            logger.debug(
                "Container repo discovery: RHEL %s — raw .repo files content (%d bytes):\n%s",
                rhel_version, len(gk_result.stdout), gk_result.stdout[:2000],
            )
            gk_repos = _parse_ubi_repo_file(gk_result.stdout, arch)
            logger.info(
                "Container repo discovery: RHEL %s — parsed %d repo stanza(s) from .repo files: %s",
                rhel_version, len(gk_repos),
                ', '.join(f"{r['repo_id']}(gpgkey={r.get('gpgkey','')!r})" for r in gk_repos),
            )
            gk_map = {r['repo_id']: r for r in gk_repos}
            for repo in parsed:
                gk = gk_map.get(repo['repo_id'])
                if gk:
                    if not repo.get('gpgkey') and gk.get('gpgkey'):
                        repo['gpgkey'] = gk['gpgkey']
                        logger.info(
                            "Container repo discovery: RHEL %s — repo %s gpgkey set from .repo file: %r",
                            rhel_version, repo['repo_id'], repo['gpgkey'],
                        )
                    # SSL client cert fields only exist in .repo files, not in
                    # dnf repolist -v output, so always copy them if present.
                    for ssl_field in ('sslcacert', 'sslclientcert', 'sslclientkey'):
                        if gk.get(ssl_field):
                            repo[ssl_field] = gk[ssl_field]
                            logger.info(
                                "Container repo discovery: RHEL %s — repo %s %s set from .repo file: %r",
                                rhel_version, repo['repo_id'], ssl_field, gk[ssl_field],
                            )
                        else:
                            logger.info(
                                "Container repo discovery: RHEL %s — repo %s has no %s in .repo files",
                                rhel_version, repo['repo_id'], ssl_field,
                            )
                elif not repo.get('gpgkey'):
                    logger.info(
                        "Container repo discovery: RHEL %s — repo %s has no gpgkey in .repo files",
                        rhel_version, repo['repo_id'],
                    )
            _enrich_repos_with_gpgkey_content(parsed, image, arch)
            _enrich_repos_with_ssl_cert_content(parsed, image)
            for repo in parsed:
                logger.info(
                    "Container repo discovery: RHEL %s — repo %s final gpgkey: %s | sslcacert: %s | sslclientcert: %s | sslclientkey: %s",
                    rhel_version, repo['repo_id'],
                    'armored key (%d chars)' % len(repo['gpgkey']) if repo.get('gpgkey') else 'NONE',
                    'PEM (%d chars)' % len(repo['sslcacert']) if repo.get('sslcacert', '').startswith('-----BEGIN') else (repo.get('sslcacert') or 'NONE'),
                    'PEM (%d chars)' % len(repo['sslclientcert']) if repo.get('sslclientcert', '').startswith('-----BEGIN') else (repo.get('sslclientcert') or 'NONE'),
                    'PEM (%d chars)' % len(repo['sslclientkey']) if repo.get('sslclientkey', '').startswith('-----BEGIN') else (repo.get('sslclientkey') or 'NONE'),
                )
        except Exception as exc:
            logger.warning("Could not enrich gpgkeys for RHEL %s: %s", rhel_version, exc)
        return parsed

    fallback_cmd = [
        'podman', 'run', '--rm', '--quiet', image,
        'sh', '-lc', 'cat /etc/yum.repos.d/ubi.repo',
    ]
    try:
        fallback_result = subprocess.run(fallback_cmd, capture_output=True, text=True, timeout=60)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("Container repo discovery fallback failed for RHEL %s: %s", rhel_version, exc)
        return None

    fallback_parsed = _parse_ubi_repo_file(fallback_result.stdout, arch)
    if fallback_parsed:
        _enrich_repos_with_gpgkey_content(fallback_parsed, image, arch)
        logger.info(
            "Container repo discovery: using ubi.repo fallback for RHEL %s after unusable repolist output",
            rhel_version,
        )
        return fallback_parsed

    logger.warning(
        "Container repo discovery: no usable repo data for RHEL %s "
        "(repolist_stdout=%r, repolist_stderr=%r, ubi_repo_stdout=%r, ubi_repo_stderr=%r)",
        rhel_version,
        result.stdout[:300], result.stderr[:300],
        fallback_result.stdout[:300], fallback_result.stderr[:300],
    )
    return None


def _discover_repos_from_host(rhel_version: str, arch: str) -> list[dict]:
    """
    Parse the host's ``/etc/yum.repos.d/*.repo`` files and return repo dicts
    for entries that look like they belong to *rhel_version*.

    This catches RHSM-managed repos from ``redhat.repo`` (generated by
    subscription-manager) which are available on the build host but never
    appear inside a plain UBI container.  GPG key files referenced by the
    repo stanzas are read directly from the host filesystem.
    """
    repos_dir = '/etc/yum.repos.d'
    if not os.path.isdir(repos_dir):
        return []

    combined = []
    for repo_file in sorted(glob.glob(os.path.join(repos_dir, '*.repo'))):
        try:
            with open(repo_file, encoding='utf-8', errors='replace') as fh:
                content = fh.read()
        except OSError as exc:
            logger.debug("_discover_repos_from_host: cannot read %s: %s", repo_file, exc)
            continue
        parsed = _parse_ubi_repo_file(content, arch)
        for repo in parsed:
            # Only keep repos that plausibly belong to this RHEL version
            repo_id_lower = repo['repo_id'].lower()
            if (
                f'rhel-{rhel_version}' in repo_id_lower
                or f'rhel{rhel_version}' in repo_id_lower
                or f'el{rhel_version}' in repo_id_lower
                or repo.get('source') in ('ubi', 'epel')  # always relevant
            ):
                # Resolve file:// gpgkey paths from the host filesystem directly
                gpgkey_val = repo.get('gpgkey', '')
                if gpgkey_val and not gpgkey_val.startswith('-----BEGIN'):
                    key_parts = []
                    for token in gpgkey_val.split():
                        path = token[7:] if token.startswith('file://') else (
                            token if token.startswith('/') else None
                        )
                        if path and os.path.isfile(path):
                            try:
                                with open(path, encoding='utf-8', errors='replace') as kf:
                                    content_key = kf.read().strip()
                                if content_key:
                                    key_parts.append(content_key)
                            except OSError:
                                pass
                        elif token.startswith('http://') or token.startswith('https://'):
                            key_parts.append(token)  # keep URL, fetched later if needed
                    if key_parts:
                        repo['gpgkey'] = '\n'.join(key_parts)
                combined.append(repo)

    logger.info(
        "_discover_repos_from_host: RHEL %s — found %d repo(s) from host /etc/yum.repos.d: %s",
        rhel_version, len(combined),
        ', '.join(r['repo_id'] for r in combined),
    )
    return combined


def sync_rpm_repositories_for_distribution(dist) -> tuple[int, int]:
    """
    Discover and upsert RpmRepository records for *dist*.

    Repo sources (merged, repos_data de-duplicated by repo_id):
    1. UBI container: ``dnf repolist --all -v`` — discovers UBI + RHSM repos
       visible inside a plain UBI image.
    2. Host yum.repos.d: ``/etc/yum.repos.d/*.repo`` files on the build host —
       picks up subscription-manager-generated ``redhat.repo`` and any other
       locally configured repos that the mock base config can inherit.
    3. Built-in EPEL definitions.

    The user's ``enabled`` flag is preserved on existing records;
    only metadata (name, URLs, gpgkey) is updated on re-sync.

    Returns ``(created_count, updated_count)``.
    """
    from apps.rpm.models import RpmRepository

    repos_data: list[dict] = (
        _discover_repos_via_container(dist.rhel_version, dist.arch)
        or []
    )

    # Merge in repos from the host's own yum.repos.d so that RHSM-managed
    # repos (which only appear on a subscribed host, not inside a plain UBI
    # container) are visible and selectable in the UI.
    host_repos = _discover_repos_from_host(dist.rhel_version, dist.arch)
    existing_ids = {r['repo_id'] for r in repos_data}
    for hr in host_repos:
        if hr['repo_id'] not in existing_ids:
            repos_data.append(hr)
            existing_ids.add(hr['repo_id'])
        else:
            # Prefer host data for gpgkey since the host has the actual key files
            for rd in repos_data:
                if rd['repo_id'] == hr['repo_id']:
                    if hr.get('gpgkey') and not rd.get('gpgkey'):
                        rd['gpgkey'] = hr['gpgkey']
                    break

    epel = _EPEL_REPOS.get(dist.rhel_version)
    if epel:
        repos_data.append(epel)

    now = timezone.now()
    created_count = 0
    updated_count = 0

    for data in repos_data:
        repo, created = RpmRepository.objects.get_or_create(
            distribution=dist,
            repo_id=data['repo_id'],
            defaults={
                'name': data.get('name', data['repo_id']),
                'baseurl': data.get('baseurl', ''),
                'mirrorlist': data.get('mirrorlist', ''),
                'metalink': data.get('metalink', ''),
                'gpgcheck': data.get('gpgcheck', True),
                'gpgkey': data.get('gpgkey', ''),
                'sslcacert': data.get('sslcacert', ''),
                'sslclientcert': data.get('sslclientcert', ''),
                'sslclientkey': data.get('sslclientkey', ''),
                'enabled': data.get('default_enabled', False),
                'source': data.get('source', 'rhsm'),
                'last_synced': now,
            },
        )
        if created:
            created_count += 1
        else:
            # Preserve user's enabled choice; update only metadata.
            meta_fields = ['name', 'baseurl', 'mirrorlist', 'metalink', 'gpgcheck', 'gpgkey',
                           'sslcacert', 'sslclientcert', 'sslclientkey', 'source']
            changed = False
            for field in meta_fields:
                new_val = data.get(field)
                if new_val is not None and getattr(repo, field) != new_val:
                    setattr(repo, field, new_val)
                    changed = True
            repo.last_synced = now
            save_fields = ['last_synced'] + (meta_fields if changed else [])
            repo.save(update_fields=save_fields)
            if changed:
                updated_count += 1

    dist.repos_synced_at = now
    dist.save(update_fields=['repos_synced_at'])
    return created_count, updated_count


@shared_task(name='rpm.sync_distribution_repos', queue='ops')
def sync_distribution_repos_task(dist_pk: int):
    """
    Queue-able task: sync repos for a single distribution by PK.
    Called from the UI when the user clicks 'Sync repos'.
    """
    from apps.rpm.models import RpmDistribution
    try:
        dist = RpmDistribution.objects.get(pk=dist_pk)
    except RpmDistribution.DoesNotExist:
        logger.warning("sync_distribution_repos_task: dist %s not found", dist_pk)
        return
    created, updated = sync_rpm_repositories_for_distribution(dist)
    logger.info("Synced repos for %s: +%d created, %d updated", dist.name, created, updated)
    return {'created': created, 'updated': updated}


@shared_task(name='rpm.sync_rpm_repositories', queue='ops')
def sync_rpm_repositories_task():
    """
    Periodic task: sync available yum/dnf repositories for all active RPM
    distributions.  Re-registers its own beat schedule on each run.
    """
    from apps.rpm.models import RpmDistribution

    total_created = total_updated = 0
    for dist in RpmDistribution.objects.filter(is_active=True):
        try:
            created, updated = sync_rpm_repositories_for_distribution(dist)
            total_created += created
            total_updated += updated
            logger.info("Synced repos for %s: +%d created, %d updated", dist.name, created, updated)
        except Exception:
            logger.exception("Failed to sync repos for distribution %s", dist.name)

    _sync_rpm_repo_periodic_task()
    return {'created': total_created, 'updated': total_updated}


def _sync_rpm_repo_periodic_task():
    """Ensure the repo-sync celery beat task is scheduled at the configured interval."""
    interval_hours = getattr(settings, 'RPM_REPO_SYNC_INTERVAL_HOURS', 24)
    _sync_rpm_periodic_task(
        'rpm.sync_rpm_repositories',
        'sync-rpm-repositories',
        interval_hours,
    )


# ---------------------------------------------------------------------------
# Distribution sync helper (called from views)
# ---------------------------------------------------------------------------

def sync_distributions_from_mock():
    """
    Scan /etc/mock/ for rhel-*.cfg files and upsert RpmDistribution records.
    Newly created distributions have their repositories seeded automatically.
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
        if created:
            try:
                sync_rpm_repositories_for_distribution(dist)
            except Exception:
                logger.exception("Could not seed repos for new distribution %s", dist.name)
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
        version, _release_date, error = _run_version_script(package.upstream_version_script, package.name)

    if not version and package.upstream_url:
        version, _raw_tag, error = _fetch_latest_upstream_tag(package.upstream_url)

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


# ---------------------------------------------------------------------------
# Failed-build cleanup
# ---------------------------------------------------------------------------

@shared_task(name='rpm.cleanup_failed_rpm_builds', queue='ops')
def cleanup_failed_rpm_builds():
    """
    Periodic task: for every RPM package keep only the N most recent failed
    builds, deleting older records and their on-disk build artifacts.

    N is taken from SiteConfig.failed_builds_to_keep (the same setting used
    for flatpak builds).  A value of 0 means keep all.
    """
    from apps.rpm.models import RpmPackage, RpmBuild
    from apps.flatpak.models import SiteConfig

    config = SiteConfig.get_solo()
    keep = config.failed_builds_to_keep

    if keep == 0:
        return "Cleanup skipped: keeping all failed RPM builds"

    rpm_build_base = (
        getattr(settings, 'RPM_BUILD_PATH', '')
        or os.path.join(settings.FLATPAK_BUILD_PATH, 'rpms')
    )

    total_deleted = 0
    for package in RpmPackage.objects.all():
        failed_ids = list(
            RpmBuild.objects.filter(package=package, status='failed')
            .order_by('-build_number')
            .values_list('id', flat=True)
        )
        to_delete = failed_ids[keep:]
        if not to_delete:
            continue

        # Clean up on-disk build artifacts before removing DB records.
        for build_id in to_delete:
            pattern = os.path.join(rpm_build_base, f'fmd-rpm-{build_id}-*')
            for build_dir in glob.glob(pattern):
                try:
                    shutil.rmtree(build_dir)
                    logger.info("Removed RPM build artifacts: %s", build_dir)
                except OSError as exc:
                    logger.warning("Could not remove %s: %s", build_dir, exc)

        deleted, _ = RpmBuild.objects.filter(id__in=to_delete).delete()
        total_deleted += deleted
        logger.info(
            "Deleted %d old failed build(s) for RPM package %s",
            deleted, package.name,
        )

    if total_deleted:
        logger.info("cleanup_failed_rpm_builds: removed %d build record(s)", total_deleted)

    return f"Cleaned up {total_deleted} old failed RPM build(s)"
