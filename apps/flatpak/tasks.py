from celery import shared_task
from django.utils import timezone
from django.conf import settings
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
import json
import logging
import os
import re
import subprocess
import shutil
import tempfile
import time
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def _host_uses_lib64():
    """
    Return True when the host OS uses ``lib64`` as its native multilib
    library directory (RHEL/Fedora/CentOS), False on distros that use
    ``lib`` (Debian/Ubuntu/Arch).

    Detection heuristic: ``/usr/lib64`` exists as a *real* directory
    (not a symlink).  On Debian-family systems the path either does not
    exist or is a compatibility symlink pointing to ``lib``.
    """
    return os.path.isdir('/usr/lib64') and not os.path.islink('/usr/lib64')


def normalize_manifest_libdirs(manifest_file, build=None):
    """
    On hosts where CMake/Meson default to ``lib64`` (RHEL9/x86_64 etc.),
    inject ``-DCMAKE_INSTALL_LIBDIR=lib`` / ``--libdir=lib`` into every
    cmake-ninja / meson module in the manifest.

    flatpak-builder only puts ``/app/lib/pkgconfig`` on ``PKG_CONFIG_PATH``,
    so ``.pc`` files that land in ``lib64/pkgconfig`` are invisible to
    subsequent modules — causing ``configure: error: Unable to locate …``
    failures on RHEL9 that do not reproduce on Debian/Ubuntu.

    The function is a no-op on distros that already default to ``lib``.
    Idempotent: skips modules that already carry the correct option.
    Writes the manifest back in-place only when changes are needed.
    """
    try:
        import platform

        machine = platform.machine()
        uses_lib64 = _host_uses_lib64()

        if not uses_lib64:
            if build:
                log_build(build, 'info',
                    f"[libdir normalisation] host uses 'lib' by default "
                    f"(arch={machine}) — no manifest patching needed")
            return

        # Identify the distro for the log message
        try:
            import distro
            distro_desc = f"{distro.name()} {distro.version()}"
        except Exception:
            distro_desc = 'unknown distro'

        if build:
            log_build(build, 'info',
                f"[libdir normalisation] detected lib64 host "
                f"({distro_desc}, arch={machine}) — patching cmake-ninja "
                f"and meson modules to install into lib/")

        try:
            import yaml
            _has_yaml = True
        except ImportError:
            _has_yaml = False

        with open(manifest_file, 'r') as fh:
            content = fh.read()

        is_yaml = manifest_file.endswith(('.yml', '.yaml'))
        if is_yaml:
            if not _has_yaml:
                if build:
                    log_build(build, 'warning',
                        '[libdir normalisation] PyYAML not available; skipping')
                return
            manifest = yaml.safe_load(content)
        else:
            manifest = json.loads(content)

        if not isinstance(manifest, dict):
            return

        changed = []

        def _fix_modules(modules):
            if not isinstance(modules, list):
                return
            for mod in modules:
                if not isinstance(mod, dict):
                    continue
                buildsystem = mod.get('buildsystem', '')
                config_opts = mod.get('config-opts')
                if config_opts is None:
                    config_opts = []
                    mod['config-opts'] = config_opts
                name = mod.get('name', '<unnamed>')
                if buildsystem == 'cmake-ninja':
                    key = '-DCMAKE_INSTALL_LIBDIR=lib'
                    if not any('CMAKE_INSTALL_LIBDIR' in str(o) for o in config_opts):
                        config_opts.append(key)
                        changed.append(f"{name} (cmake-ninja): added {key}")
                elif buildsystem == 'meson':
                    key = '--libdir=lib'
                    if not any('libdir' in str(o) for o in config_opts):
                        config_opts.append(key)
                        changed.append(f"{name} (meson): added {key}")
                # Recurse into nested modules
                _fix_modules(mod.get('modules', []))

        _fix_modules(manifest.get('modules', []))

        if not changed:
            if build:
                log_build(build, 'info',
                    '[libdir normalisation] all modules already have correct libdir — nothing to patch')
            return

        with open(manifest_file, 'w') as fh:
            if is_yaml:
                yaml.dump(manifest, fh, default_flow_style=False, allow_unicode=True, sort_keys=False)
            else:
                json.dump(manifest, fh, indent=2, ensure_ascii=False)

        if build:
            for msg in changed:
                log_build(build, 'info', f"[libdir normalisation] patched: {msg}")

    except Exception as exc:
        if build:
            log_build(build, 'warning', f"normalize_manifest_libdirs failed (non-fatal): {exc}")


def ensure_appstream_compose_shims(build=None):
    """
    Ensure an ``appstream-compose`` wrapper exists inside every installed
    Freedesktop SDK's ``files/bin/`` directory.

    flatpak-builder calls ``appstream-compose`` from inside the bwrap sandbox
    where only the SDK's own ``files/bin`` is on PATH.  SDK 23.08+ dropped the
    standalone binary in favour of ``appstreamcli compose``.  Writing a tiny
    wrapper script directly into the SDK's bin dir makes it visible inside the
    sandbox without any PATH tricks or sandbox flag gymnastics.

    Safe to call on every build — skips SDKs that already have the wrapper.
    """
    import glob as _glob
    home = str(Path.home())
    # Cover both user (~/.local/share/flatpak) and system (/var/lib/flatpak)
    search_roots = [
        os.path.join(home, '.local', 'share', 'flatpak'),
        '/var/lib/flatpak',
    ]
    # Translate appstream-compose args to appstreamcli compose args.
    # flatpak-builder 1.2.3 calls (from inside bwrap via flatpak build):
    #   appstream-compose --prefix=PREFIX --origin=O --basename=ID ID
    # where PREFIX is /app (apps) or /usr (runtimes), ID is the component ID.
    #
    # Two key bugs in the old translation:
    # 1. --prefix=/ is WRONG: appstreamcli looks for .desktop files at
    #    ${prefix}/share/applications/, so using / instead of /app means it
    #    looks at /share/applications/ (doesn't exist) → file-read-error.
    # 2. --basename was dropped and --origin kept as "flatpak", so the output
    #    file was named "flatpak.xml.gz" instead of "<app-id>.xml.gz".
    #    flatpak build-update-repo looks for "<app-id>.xml.gz" specifically.
    #
    # Fix: pass --prefix=${PREFIX} and use --basename value as --origin.
    shim_content = (
        '#!/bin/sh\n'
        '# appstream-compose shim installed by flat-manager-django\n'
        '# Translates flatpak-builder 1.2.3 appstream-compose call to appstreamcli compose.\n'
        '#\n'
        '# Old call: appstream-compose --prefix=PREFIX --origin=O --basename=ID ID\n'
        '# New call: appstreamcli compose --prefix=PREFIX --origin=ID\n'
        '#             --result-root=PREFIX\n'
        '#             --data-dir=PREFIX/share/app-info/xmls\n'
        '#             --icons-dir=PREFIX/share/app-info/icons/flatpak /\n'
        '# SOURCE_DIR is / (sandbox root); --prefix tells appstreamcli where within\n'
        '# that root the app data lives.  Passing PREFIX here causes /PREFIX/PREFIX/share.\n'
        '# --origin is set to the --basename value so the output file is named\n'
        '# <app-id>.xml.gz, which is what flatpak build-update-repo expects.\n'
        'PREFIX=/app\n'
        'BASENAME=\n'
        'OTHER_ARGS=\n'
        'for arg; do\n'
        '    case "$arg" in\n'
        '        --prefix=*)   PREFIX="${arg#--prefix=}" ;;\n'
        '        --origin=*)   : ;;  # Dropped — we derive origin from --basename\n'
        '        --basename=*) BASENAME="${arg#--basename=}" ;;\n'
        '        --*)          OTHER_ARGS="${OTHER_ARGS} ${arg}" ;;\n'
        '        *)            : ;;  # Drop old positional SRCDIR (app ID); replaced by PREFIX\n'
        '    esac\n'
        'done\n'
        '# Use --basename as --origin so the output XML is named <app-id>.xml.gz\n'
        'ORIGIN="${BASENAME:-flatpak}"\n'
        '# flatpak-builder renames the metainfo file to share/appdata/ just before\n'
        '# calling appstream-compose (logged as "Renaming … to share/appdata/…").\n'
        '# We copy it back — AND rename *.appdata.xml → *.metainfo.xml, because\n'
        '# appstreamcli 0.15+ on RHEL 9 silently ignores *.appdata.xml files in\n'
        '# share/metainfo/ and only processes *.metainfo.xml, so the old extension\n'
        '# produced an empty compose result even when the file was present.\n'
        'if [ -d "${PREFIX}/share/appdata" ]; then\n'
        '    mkdir -p "${PREFIX}/share/metainfo"\n'
        '    for _f in "${PREFIX}/share/appdata/"*.xml; do\n'
        '        [ -f "$_f" ] || continue\n'
        '        _name="$(basename "$_f")"\n'
        '        case "$_name" in\n'
        '            *.appdata.xml) _dest="${PREFIX}/share/metainfo/${_name%.appdata.xml}.metainfo.xml" ;;\n'
        '            *)             _dest="${PREFIX}/share/metainfo/${_name}" ;;\n'
        '        esac\n'
        '        [ -e "$_dest" ] || cp "$_f" "$_dest"\n'
        '    done\n'
        'fi\n'
        '# Also normalise *.appdata.xml already sitting in share/metainfo/ (apps that\n'
        '# install the file directly, bypassing the flatpak-builder rename pass).\n'
        'for _f in "${PREFIX}/share/metainfo/"*.appdata.xml; do\n'
        '    [ -f "$_f" ] || continue\n'
        '    _dest="${_f%.appdata.xml}.metainfo.xml"\n'
        '    [ -e "$_dest" ] || cp "$_f" "$_dest"\n'
        'done\n'
        '# Ensure output directories exist before compose tries to write to them.\n'
        'mkdir -p "${PREFIX}/share/app-info/xmls"\n'
        'mkdir -p "${PREFIX}/share/app-info/icons/flatpak"\n'
        '# shellcheck disable=SC2086\n'
        '_asc_rc=0\n'
        'appstreamcli compose \\\n'
        '    --prefix="${PREFIX}" \\\n'
        '    --origin="${ORIGIN}" \\\n'
        '    --result-root="${PREFIX}" \\\n'
        '    --data-dir="${PREFIX}/share/app-info/xmls" \\\n'
        '    --icons-dir="${PREFIX}/share/app-info/icons/flatpak" \\\n'
        '    --no-net \\\n'
        '    ${OTHER_ARGS} \\\n'
        '    / || _asc_rc=$?\n'
        '# If appstreamcli failed (non-zero exit) OR produced no gz, synthesise a minimal\n'
        '# one from the metainfo so flatpak build-update-repo can populate the appstream\n'
        '# branch with the app version.  appstreamcli exits non-zero even when it creates\n'
        '# a partial gz (e.g. "some data was ignored"), so we must check the exit code —\n'
        '# not just the file\'s existence — to detect a failed compose run.\n'
        '# This fallback runs INSIDE the bwrap sandbox so the gz flows through the normal\n'
        '# flatpak-builder finish-stage export path into the OSTree commit.\n'
        '_gz="${PREFIX}/share/app-info/xmls/${ORIGIN}.xml.gz"\n'
        'if [ "${_asc_rc}" != "0" ] || [ ! -s "${_gz}" ]; then\n'
        '    _meta=""\n'
        '    for _p in \\\n'
        '        "${PREFIX}/share/metainfo/${ORIGIN}.metainfo.xml" \\\n'
        '        "${PREFIX}/share/appdata/${ORIGIN}.appdata.xml" \\\n'
        '        "${PREFIX}/share/metainfo/${ORIGIN}.appdata.xml" \\\n'
        '        "${PREFIX}/share/appdata/${ORIGIN}.metainfo.xml"; do\n'
        '        [ -f "$_p" ] && { _meta="$_p"; break; }; done\n'
        '    if [ -n "$_meta" ]; then\n'
        '        # Pipe directly into gzip; avoids /tmp which may be absent or\n'
        '        # read-only inside the flatpak-builder bwrap cleanup sandbox.\n'
        '        { printf '"'"'<?xml version="1.0" encoding="UTF-8"?>\\n<components version="0.8" origin="%s">\\n'"'"' "${ORIGIN}"; \\\n'
        '          cat "${_meta}"; \\\n'
        '          printf '"'"'\\n</components>\\n'"'"'; } | gzip -9 -c > "${_gz}"\n'
        '        printf "appstream-compose shim: synthesised fallback xml.gz from %s\\n" "${_meta}" >&2\n'
        '    fi\n'
        'fi\n'
    )
    patched = []

    for root in search_roots:
        # Match all SDK runtimes (org.freedesktop.Sdk, org.kde.Sdk, org.gnome.Sdk, …).
        # Flatpak may store deploys as 'active' symlinks OR raw commit-hash directories;
        # use a wildcard for the 5th component to cover both cases.  Deduplicate by
        # realpath so that if 'active' IS a symlink we don't write the shim twice.
        pattern = os.path.join(
            root, 'runtime', '*.Sdk',
            '*', '*', '*', 'files', 'bin'
        )
        _seen_real_bin_dirs = set()
        for bin_dir in _glob.glob(pattern):
            real_bin = os.path.realpath(bin_dir)
            if real_bin in _seen_real_bin_dirs:
                continue
            _seen_real_bin_dirs.add(real_bin)
            compose_path = os.path.join(bin_dir, 'appstream-compose')
            appstreamcli_path = os.path.join(bin_dir, 'appstreamcli')
            # Write shim if missing or outdated (idempotent)
            if os.path.exists(appstreamcli_path):
                needs_write = True
                if os.path.exists(compose_path):
                    try:
                        with open(compose_path) as _f:
                            needs_write = _f.read() != shim_content
                    except OSError:
                        pass
                    except UnicodeDecodeError:
                        # Existing file is a real binary (SDK ships its own
                        # appstream-compose). Replace it with our shim so that
                        # appstreamcli compose is called with the correct args.
                        needs_write = True
                if needs_write:
                    try:
                        with open(compose_path, 'w') as _f:
                            _f.write(shim_content)
                        os.chmod(compose_path, 0o755)
                        patched.append(compose_path)
                    except OSError as e:
                        if build:
                            log_build(build, 'warning',
                                      f"Could not write appstream-compose shim to {compose_path}: {e}")

    if patched and build:
        log_build(build, 'info',
                  f"Installed appstream-compose shim in {len(patched)} SDK(s): "
                  + ', '.join(os.path.dirname(p).replace('/files/bin', '') for p in patched))



class BuildCancelledError(Exception):
    """Raised when the DB build record is set to 'cancelled' during a build."""


def run_cancellable(cmd, cwd, build, timeout_seconds):
    """
    Run *cmd* as a subprocess, streaming every output line to the build log
    in real-time, while periodically checking whether *build* has been
    cancelled in the database.  Kills the process and raises
    :exc:`BuildCancelledError` if cancellation is detected.

    Returns a :class:`subprocess.CompletedProcess` on success (or non-zero exit).
    stdout is empty (lines were already logged); stderr contains the collected
    stderr text for use as an error message on failure.
    """
    import threading
    import queue as _queue

    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding='utf-8',
        errors='replace',  # Replace un-decodable bytes (e.g. binary download progress) with ?
        bufsize=1,  # line-buffered
    )

    # Each reader thread feeds (stream_name, line) into the queue.
    # A sentinel (stream_name, None) signals EOF for that stream.
    line_queue = _queue.Queue()

    def _reader(pipe, name):
        try:
            for raw_line in pipe:
                line_queue.put((name, raw_line))
        finally:
            line_queue.put((name, None))

    t_out = threading.Thread(target=_reader, args=(proc.stdout, 'stdout'), daemon=True)
    t_err = threading.Thread(target=_reader, args=(proc.stderr, 'stderr'), daemon=True)
    t_out.start()
    t_err.start()

    poll_interval = 5  # seconds between cancellation checks
    elapsed = 0
    stderr_lines = []
    streams_done = set()

    try:
        while len(streams_done) < 2 or proc.poll() is None:
            # Drain all lines currently available without blocking.
            drained = 0
            while True:
                try:
                    name, raw = line_queue.get(timeout=0.1)
                except _queue.Empty:
                    break
                drained += 1
                if raw is None:
                    streams_done.add(name)
                    continue
                line = raw.rstrip('\n')
                if not line:
                    continue
                if name == 'stderr':
                    stderr_lines.append(line)
                    log_build(build, 'info', line)
                else:
                    log_build(build, 'info', line)

            if len(streams_done) >= 2:
                # Both pipes closed — process is definitely done.
                break

            # After draining, sleep briefly then check cancellation/timeout.
            if drained == 0:
                time.sleep(poll_interval)
                elapsed += poll_interval

                if elapsed >= timeout_seconds:
                    proc.kill()
                    proc.wait()
                    raise RuntimeError(
                        f"Build timed out after {timeout_seconds // 60} minutes"
                    )

                from apps.flatpak.models import Build as _Build
                current_status = _Build.objects.filter(
                    pk=build.pk
                ).values_list('status', flat=True).first()
                if current_status == 'cancelled':
                    proc.kill()
                    proc.wait()
                    raise BuildCancelledError("Build was cancelled by user")

    except (BuildCancelledError, RuntimeError):
        raise
    except Exception:
        proc.kill()
        proc.wait()
        raise

    t_out.join(timeout=5)
    t_err.join(timeout=5)
    proc.wait()

    return subprocess.CompletedProcess(
        args=cmd,
        returncode=proc.returncode,
        stdout='',  # already streamed line-by-line above
        stderr='\n'.join(stderr_lines),
    )


def _patch_build_dir_versions(build_dir, package_id, version, log_fn=None):
    """Ensure the built artifact has the correct version in its appstream metadata.

    flatpak-builder already committed the build to the OSTree repo, but many
    apps ship no <releases> element (or omit it altogether), so 'flatpak list'
    shows a blank version column.

    This function patches TWO things inside *build_dir* (which still exists
    on-disk after flatpak-builder returns):

    1. ``files/share/metainfo/{package_id}.metainfo.xml``
       (or the fallback appdata variants) — the canonical metainfo source.
       Created from scratch if absent.

    2. ``export/share/app-info/xmls/{package_id}.xml.gz``
       The gzip-compressed AppStream XML that flatpak build-export commits
       into the OSTree ref and that flatpak build-update-repo later reads to
       build the repository's appstream summary.  Patching here is what
       actually makes 'flatpak list' show the version.

    After calling this function the caller MUST re-export the build_dir with
    ``flatpak build-export`` so the patched files replace the earlier commit.

    Returns True if any file was modified.
    """
    import gzip
    import datetime
    import xml.etree.ElementTree as ET

    def _log(msg):
        if log_fn:
            log_fn(msg)

    if not version:
        return False

    today = datetime.date.today().isoformat()
    patched_any = False

    # ── helpers ──────────────────────────────────────────────────────────────

    def _ensure_releases_in_tree(root, version, date):
        """Add / update a <release> with *version* inside <releases> of *root*."""
        releases = root.find('releases')
        if releases is None:
            releases = ET.SubElement(root, 'releases')
        # Check whether this exact version is already present.
        for rel in releases.findall('release'):
            if rel.attrib.get('version') == version:
                return False  # nothing to do
        # Remove any release that has an empty or missing version (broken entry).
        for rel in list(releases.findall('release')):
            if not rel.attrib.get('version'):
                releases.remove(rel)
        # Prepend the new release so it's the first (= most recent) entry.
        new_rel = ET.Element('release', {'version': version, 'date': date})
        releases.insert(0, new_rel)
        return True

    def _patch_xml_file(path, version, date):
        """Parse path as XML, inject release, write back. Returns True on change."""
        try:
            ET.register_namespace('', 'https://www.freedesktop.org/software/appstream/docs/')
            tree = ET.parse(path)
            root = tree.getroot()
            if _ensure_releases_in_tree(root, version, date):
                # Preserve the original encoding declaration if present.
                ET.indent(tree, space='  ')
                tree.write(path, xml_declaration=True, encoding='utf-8')
                return True
        except Exception as exc:
            _log(f"Warning: could not patch {path}: {exc}")
        return False

    def _patch_gz_appstream(gz_path, version, date):
        """Read a gzipped AppStream XML, inject <release>, write back. Returns True on change."""
        try:
            with gzip.open(gz_path, 'rb') as fh:
                raw = fh.read()
            # AppStream XMLs from flatpak-builder use a <components> root.
            root = ET.fromstring(raw)
            changed = False
            # Find the matching <component> by id.
            for comp in root.iter('component'):
                id_el = comp.find('id')
                if id_el is not None and (id_el.text or '').strip() == package_id:
                    if _ensure_releases_in_tree(comp, version, date):
                        changed = True
                    break
            else:
                # No matching component — patch first component as fallback.
                for comp in root.iter('component'):
                    if _ensure_releases_in_tree(comp, version, date):
                        changed = True
                    break
            if changed:
                patched_xml = ET.tostring(root, encoding='utf-8', xml_declaration=True)
                with gzip.open(gz_path, 'wb') as fh:
                    fh.write(patched_xml)
                return True
        except Exception as exc:
            _log(f"Warning: could not patch appstream gz {gz_path}: {exc}")
        return False

    # ── 1. Source metainfo ───────────────────────────────────────────────────

    metainfo_path = None
    for d in [
        os.path.join(build_dir, 'files', 'share', 'metainfo'),
        os.path.join(build_dir, 'files', 'share', 'appdata'),
    ]:
        if not os.path.isdir(d):
            continue
        for name in (
            f'{package_id}.metainfo.xml',
            f'{package_id}.appdata.xml',
        ):
            c = os.path.join(d, name)
            if os.path.exists(c):
                metainfo_path = c
                break
        if metainfo_path:
            break

    if metainfo_path:
        if _patch_xml_file(metainfo_path, version, today):
            _log(f"Patched metainfo version → {version} in {os.path.relpath(metainfo_path, build_dir)}")
            patched_any = True
        else:
            _log(f"Metainfo already has version {version}")
    else:
        # Create a minimal metainfo so appstream tools have something to work with.
        meta_dir = os.path.join(build_dir, 'files', 'share', 'metainfo')
        os.makedirs(meta_dir, exist_ok=True)
        metainfo_path = os.path.join(meta_dir, f'{package_id}.metainfo.xml')
        minimal_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<component type="desktop-application">\n'
            f'  <id>{package_id}</id>\n'
            '  <metadata_license>FSFAP</metadata_license>\n'
            '  <project_license>LicenseRef-proprietary</project_license>\n'
            f'  <name>{package_id.split(".")[-1]}</name>\n'
            '  <summary>Flatpak application</summary>\n'
            '  <releases>\n'
            f'    <release version="{version}" date="{today}"/>\n'
            '  </releases>\n'
            '</component>\n'
        )
        with open(metainfo_path, 'w', encoding='utf-8') as fh:
            fh.write(minimal_xml)
        _log(f"Created minimal metainfo with version {version}")
        patched_any = True

    # ── 2. Gzipped appstream blob (export/ or files/share/app-info/xmls/) ─────
    # When appstreamcli succeeds, flatpak-builder's finish stage exports the gz
    # to export/share/app-info/xmls/.  When it fails our shim fallback writes the
    # gz to ${PREFIX}/share/app-info/xmls/ inside bwrap — that path surfaces on
    # the host as files/share/app-info/xmls/.  flatpak build-export commits all of
    # files/ to the OSTree ref, and flatpak build-update-repo reads appstream from
    # files/share/app-info/xmls/ — so either location is fine.

    gz_path = os.path.join(
        build_dir, 'export', 'share', 'app-info', 'xmls', f'{package_id}.xml.gz'
    )
    gz_path_in_files = os.path.join(
        build_dir, 'files', 'share', 'app-info', 'xmls', f'{package_id}.xml.gz'
    )
    if os.path.exists(gz_path):
        if _patch_gz_appstream(gz_path, version, today):
            _log(f"Patched appstream gz with version {version}")
            patched_any = True
    elif os.path.exists(gz_path_in_files):
        # Shim fallback path — gz was synthesised inside bwrap and is already
        # built from the metainfo (which has the correct version); no patching needed.
        _log("Appstream gz present in files/ (written by shim fallback) — version committed to OSTree")
    else:
        _log("No appstream gz found after build — shim fallback may not have run")

    return patched_any


@shared_task(bind=True, queue='build')
def package_from_git_task(self, package_id):
    """
    Build a flatpak from git repository using flatpak-builder.
    This task:
    1. Clones the git repository
    2. Runs flatpak-builder to build the app
    3. Exports the build to the build OSTree repository
    4. Updates build status and logs
    """
    from apps.flatpak.models import Package, Build, BuildLog, SiteConfig
    
    package = None
    build = None
    temp_dir = None
    
    try:
        package = Package.objects.get(id=package_id)
        
        # Validate git build
        if not package.git_repo_url:
            raise ValueError("No git repository URL specified")
        
        # Create Build history record for this attempt.
        # Use get_or_create to handle the race where check_pending_builds dispatches
        # a duplicate task before the first task has saved package.status='building'.
        build, created = Build.objects.get_or_create(
            package=package,
            build_number=package.build_number,
            defaults={
                'status': 'building',
                'started_at': timezone.now(),
                'celery_task_id': self.request.id or '',
            },
        )
        if not created:
            # A Build record already exists for this build_number — this task is
            # a stale duplicate queued before the first task saved status='building'.
            # Always skip: legitimate retries come via "Rebuild" which increments
            # build_number so get_or_create would produce a fresh record.
            logger.warning(
                f"Duplicate task for package {package_id} build "
                f"#{package.build_number} (existing status: '{build.status}') — skipping"
            )
            return

        # Update package status
        package.status = 'building'
        package.save()
        
        log_build(build, 'info', f"Starting package build for {package.package_id}")
        send_build_status_update(package_id, 'building', 'Cloning git repository')
        
        # Create temporary directory for build
        temp_dir = tempfile.mkdtemp(prefix=f'fmdc_build_{package.build_number}_')
        log_build(build, 'info', f"Created build directory: {temp_dir}")
        
        # Clone git repository
        log_build(build, 'info', f"Cloning {package.git_repo_url} (branch: {package.git_branch})")
        clone_result = subprocess.run(
            ['git', 'clone', '--branch', package.git_branch, '--depth', '1', '--recurse-submodules', package.git_repo_url, 'source'],
            cwd=temp_dir,
            capture_output=True,
            text=True,
            timeout=600  # Increased timeout for submodules
        )
        
        if clone_result.returncode != 0:
            raise RuntimeError(f"Git clone failed: {clone_result.stderr}")
        
        # Log clone output if any
        if clone_result.stdout.strip():
            log_build(build, 'info', f"Clone output: {clone_result.stdout.strip()}")
        
        source_dir = os.path.join(temp_dir, 'source')
        
        # Check if .gitmodules exists
        gitmodules_path = os.path.join(source_dir, '.gitmodules')
        if os.path.exists(gitmodules_path):
            log_build(build, 'info', "Found .gitmodules file, repository has submodules")
            
            # Read and log .gitmodules content
            try:
                with open(gitmodules_path, 'r') as f:
                    gitmodules_content = f.read()
                    log_build(build, 'info', f"Submodule configuration: {gitmodules_content[:200]}")
            except Exception as e:
                log_build(build, 'warning', f"Could not read .gitmodules: {e}")
        else:
            log_build(build, 'info', "No .gitmodules file found")
        
        # Log submodule status
        log_build(build, 'info', "Checking git submodules...")
        submodule_status = subprocess.run(
            ['git', 'submodule', 'status'],
            cwd=source_dir,
            capture_output=True,
            text=True
        )
        
        if submodule_status.stdout.strip():
            log_build(build, 'info', f"Submodule status:\n{submodule_status.stdout.strip()}")
        else:
            log_build(build, 'info', "No submodules found by git submodule status")
        
        # Ensure submodules are fully initialized (in case --recurse-submodules didn't work).
        # First attempt: shallow clone (fast, sufficient for most submodules).
        log_build(build, 'info', "Running git submodule update --init --recursive --depth 1...")
        submodule_init_result = subprocess.run(
            ['git', 'submodule', 'update', '--init', '--recursive', '--depth', '1'],
            cwd=source_dir,
            capture_output=True,
            text=True,
            timeout=300
        )

        if submodule_init_result.returncode != 0:
            log_build(build, 'warning',
                      f"Shallow submodule init failed (exit {submodule_init_result.returncode}): "
                      f"{submodule_init_result.stderr.strip()}")
            log_build(build, 'info', "Retrying git submodule update without --depth 1...")
            submodule_init_result = subprocess.run(
                ['git', 'submodule', 'update', '--init', '--recursive'],
                cwd=source_dir,
                capture_output=True,
                text=True,
                timeout=600
            )
            if submodule_init_result.returncode != 0:
                raise RuntimeError(
                    f"Git submodule update failed: {submodule_init_result.stderr.strip()}"
                )

        if submodule_init_result.stdout.strip():
            log_build(build, 'info', f"Submodule update output: {submodule_init_result.stdout.strip()}")
        else:
            log_build(build, 'info', "Submodule update completed (no output)")

        # Verify shared-modules directory if the manifest uses it
        shared_modules_path = os.path.join(source_dir, 'shared-modules')
        if os.path.exists(shared_modules_path):
            log_build(build, 'info', f"shared-modules directory exists: {os.listdir(shared_modules_path)[:10]}")
        elif os.path.exists(os.path.join(source_dir, '.gitmodules')):
            # .gitmodules present → submodules were expected; raise so the cause is clear
            raise RuntimeError("shared-modules directory NOT FOUND after submodule init — check that all submodule URLs are accessible")
        
        # Get commit hash
        commit_result = subprocess.run(
            ['git', 'rev-parse', 'HEAD'],
            cwd=source_dir,
            capture_output=True,
            text=True
        )
        
        if commit_result.returncode == 0:
            package.source_commit = commit_result.stdout.strip()
            package.save()
            log_build(build, 'info', f"Source commit: {package.source_commit}")
        
        send_build_status_update(package_id, 'building', 'Running flatpak-builder')
        
        # Find manifest file — use explicit override first, then exact names, then scan.
        manifest_file = None
        if package.manifest_file:
            override_path = os.path.join(source_dir, package.manifest_file)
            if os.path.exists(override_path):
                manifest_file = override_path
                log_build(build, 'info', f"Using manifest override: {package.manifest_file}")
            else:
                log_build(build, 'warning', f"Manifest override '{package.manifest_file}' not found in repo, falling back to auto-detection")

        if not manifest_file:
            for name in [
                f'{package.package_id}.yml', f'{package.package_id}.yaml', f'{package.package_id}.json',
                f'{package.package_id}.metainfo.xml',
                'flatpak.yml', 'flatpak.yaml', 'flatpak.json',
            ]:
                candidate = os.path.join(source_dir, name)
                if os.path.exists(candidate):
                    manifest_file = candidate
                    break

        # Fallback: scan root (and one level deep) for any .json/.yml/.yaml/.metainfo.xml
        # that contains a flatpak app-id field, preferring files whose name matches the
        # package ID prefix (e.g. org.kde.*).
        if not manifest_file:
            MANIFEST_EXTS = ('.json', '.yml', '.yaml', '.metainfo.xml')
            candidates = []
            for entry in os.scandir(source_dir):
                if entry.is_file() and entry.name.endswith(MANIFEST_EXTS):
                    candidates.append(entry.path)
                elif entry.is_dir(follow_symlinks=False):
                    try:
                        for sub in os.scandir(entry.path):
                            if sub.is_file() and sub.name.endswith(MANIFEST_EXTS):
                                candidates.append(sub.path)
                    except PermissionError:
                        pass
            # Score: higher = better match.  Prefer name containing package_id prefix.
            app_prefix = package.package_id.split('.')[0] + '.' + package.package_id.split('.')[1]
            def _score(p):
                n = os.path.basename(p)
                if package.package_id in n:
                    return 3
                if app_prefix in n:
                    return 2
                if n in ('flatpak.json', 'flatpak.yml', 'flatpak.yaml'):
                    return 1
                return 0
            for path in sorted(candidates, key=_score, reverse=True):
                manifest_file = path
                log_build(build, 'warning',
                    f"Manifest not found by expected name — using detected file: {os.path.relpath(path, source_dir)}")
                break

        if not manifest_file:
            # Log directory listing to help diagnose future cases
            try:
                top_files = [e.name for e in os.scandir(source_dir) if e.is_file()]
            except Exception:
                top_files = []
            raise FileNotFoundError(
                f"No manifest file found for {package.package_id}. "
                f"Files in repo root: {top_files}"
            )
        
        log_build(build, 'info', f"Using manifest: {os.path.basename(manifest_file)}")

        # Normalise cmake/meson library install dirs so .pc files always land in
        # /app/lib/pkgconfig (the only path on PKG_CONFIG_PATH) rather than
        # /app/lib64/pkgconfig on RHEL9/x86_64.
        normalize_manifest_libdirs(manifest_file, build)

        # Parse manifest to extract dependencies
        dependencies = parse_manifest_dependencies(package, manifest_file, build)
        if dependencies:
            package.dependencies = dependencies
            package.save()
            
            # Enhanced dependency logging
            dep_info = f"SDK={dependencies.get('sdk')}, Runtime={dependencies.get('runtime')}"
            if 'sdk_extensions' in dependencies:
                extensions = [ext['name'] for ext in dependencies['sdk_extensions']]
                dep_info += f", Extensions={extensions}"
            
            log_build(build, 'info', f"Detected dependencies: {dep_info}")
            
            # Install dependencies before building
            if not install_flatpak_dependencies(package, dependencies, build):
                raise RuntimeError("Failed to install required dependencies")
        
        # Create build directory
        build_dir = os.path.join(temp_dir, 'build')
        os.makedirs(build_dir, exist_ok=True)
        
        # Get build repo path
        build_repo_path = os.path.join(settings.REPOS_BASE_PATH, 'build-repo')
        os.makedirs(build_repo_path, exist_ok=True)
        
        # Initialize build repo if needed
        if not os.path.exists(os.path.join(build_repo_path, 'config')):
            subprocess.run(
                ['ostree', 'init', '--mode=archive-z2', f'--repo={build_repo_path}'],
                check=True,
                capture_output=True
            )
            log_build(build, 'info', "Initialized build-repo")
        
        # Run flatpak-builder (output is streamed line-by-line to the log in real-time)
        log_build(build, 'info', "Running flatpak-builder...")

        # Ensure appstream-compose shim exists inside every installed SDK's bin dir.
        # SDK 23.08+ dropped the standalone binary; flatpak-builder calls it from
        # inside the bwrap sandbox where only the SDK's files/bin is on PATH.
        ensure_appstream_compose_shims(build)

        install_flag = '--user' if getattr(package, 'installation_type', 'user') == 'user' else '--system'
        flatpak_builder_cmd = [
            'flatpak-builder',
            install_flag,
            '--force-clean',
            '--disable-rofiles-fuse',  # rofiles-fuse requires FUSE privs; service user lacks them
            '--repo', build_repo_path,
            '--default-branch', package.branch,
            build_dir,
            manifest_file
        ]
        log_build(build, 'info', f"flatpak-builder cmd: {' '.join(flatpak_builder_cmd)}")

        build_timeout = SiteConfig.get_solo().build_timeout_minutes * 60
        builder_result = run_cancellable(
            flatpak_builder_cmd,
            cwd=source_dir,
            build=build,
            timeout_seconds=build_timeout,
        )

        if builder_result.returncode != 0:
            error_msg = builder_result.stderr or "flatpak-builder failed"
            log_build(build, 'error', f"Package build failed: {error_msg}")
            
            # Try to detect and install missing dependencies
            if 'not installed' in error_msg or 'Unable to find' in error_msg:
                log_build(build, 'info', "Attempting to install missing dependencies...")
                if detect_and_install_dependencies(package, error_msg, build):
                    log_build(build, 'info', "Dependencies installed, retrying build...")
                    
                    # Retry flatpak-builder
                    builder_result = run_cancellable(
                        flatpak_builder_cmd,
                        cwd=source_dir,
                        build=build,
                        timeout_seconds=build_timeout,
                    )

                    if builder_result.returncode != 0:
                        error_msg = builder_result.stderr or "flatpak-builder failed after dependency install"
                        log_build(build, 'error', f"Build still failed: {error_msg}")
                        raise RuntimeError(f"flatpak-builder failed: {error_msg}")
                else:
                    raise RuntimeError(f"flatpak-builder failed: {error_msg}")
            else:
                raise RuntimeError(f"flatpak-builder failed: {error_msg}")
        
        # Patch metainfo/appstream version so 'flatpak list' shows a version.
        # This runs after flatpak-builder has already committed the build, so
        # we need to re-export with the patched files to update the OSTree ref.
        _version_for_patch = getattr(package, 'version', '') or ''
        if _version_for_patch:
            _patched = _patch_build_dir_versions(
                build_dir, package.package_id, _version_for_patch,
                log_fn=lambda m: log_build(build, 'info', m),
            )
            if _patched:
                log_build(build, 'info', "Re-exporting build with patched appstream metadata...")
                reexport_result = subprocess.run(
                    ['flatpak', 'build-export', build_repo_path, build_dir, package.branch],
                    capture_output=True, text=True, cwd=source_dir,
                )
                if reexport_result.returncode == 0:
                    log_build(build, 'info', "Re-export succeeded — version will appear in flatpak list")
                else:
                    log_build(build, 'warning',
                              f"Re-export after metainfo patch failed (non-fatal): "
                              f"{reexport_result.stderr.strip() or reexport_result.stdout.strip()}")
        else:
            log_build(build, 'info', "No version detected — skipping metainfo version patch")

        # Success - update Build history record
        build.status = 'built'
        build.completed_at = timezone.now()
        build.save()
        
        log_build(build, 'info', "Build completed successfully")

        # Capture produced refs from build-repo
        check_refs_result = subprocess.run(
            ['ostree', 'refs', f'--repo={build_repo_path}'],
            capture_output=True, text=True
        )
        if check_refs_result.returncode == 0:
            all_package_refs = sorted(
                r for r in check_refs_result.stdout.strip().split('\n')
                if r and package.package_id in r
            )
            if all_package_refs:
                package.produced_refs = '\n'.join(all_package_refs)
                log_build(build, 'info', f"Captured {len(all_package_refs)} produced ref(s): {', '.join(all_package_refs)}")

        # Snapshot which ExternalRefs this build depended on (at build time, so
        # the dep table is visible immediately without waiting for publish)
        _snapshot_build_external_refs(build, package)

        # The new snapshots were taken against the current upstream commits, so
        # the package is no longer stale — clear the flag immediately rather
        # than waiting for the next evaluate_dependency_staleness periodic run.
        package.deps_need_rebuild = False
        package.status = 'built'
        package.save()
        send_build_status_update(package_id, 'built', 'Build completed, ready to publish')

    except Package.DoesNotExist:
        logger.error(f"Package {package_id} not found")
    except Exception as e:
        logger.error(f"Error building from git {package_id}: {str(e)}")
        if package:
            package.status = 'failed'
            package.error_message = str(e)
            package.save()
        
        if build:
            build.status = 'failed'
            build.error_message = str(e)
            build.completed_at = timezone.now()
            build.save()
            log_build(build, 'error', f"Package build failed: {str(e)}")
        
        send_build_status_update(package_id, 'failed', f'Build failed: {str(e)}')
    finally:
        # Cleanup temp directory
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
                logger.info(f"Cleaned up temp directory: {temp_dir}")
            except Exception as e:
                logger.warning(f"Failed to cleanup temp directory {temp_dir}: {e}")


def _get_bst_binary(bst_version):
    """Return the path to the bst executable for the given BST major version.

    - BST 2: prefers the active Python virtualenv's ``bin/bst``.
    - BST 1: reads ``bst1_venv_path`` from SiteConfig (UI-configurable),
      falling back to ``BST1_VENV_PATH`` in settings.py.
    """
    # 1) Helper for resolving tools inside a venv root.
    def _tool_in_venv(venv_root, tool_name='bst'):
        if not venv_root:
            return ''
        candidate = os.path.join(venv_root, 'bin', tool_name)
        return candidate if os.path.exists(candidate) else ''

    if bst_version == 'bst1':
        venv = ''
        try:
            from apps.flatpak.models import SiteConfig
            venv = (SiteConfig.get_solo().bst1_venv_path or '').strip()
        except Exception:
            pass
        if not venv:
            from django.conf import settings
            venv = getattr(settings, 'BST1_VENV_PATH', '').strip()
        resolved = _tool_in_venv(venv, 'bst')
        if resolved:
            return resolved

    # BST 2: prefer the currently running interpreter's sibling binary.
    # Celery is started from /opt/flat-manager/venv/bin/celery in RPM installs,
    # so sys.executable is expected to be /opt/flat-manager/venv/bin/python.
    py_bin = os.path.dirname(sys.executable or '')
    if py_bin:
        candidate = os.path.join(py_bin, 'bst')
        if os.path.exists(candidate):
            return candidate

    # Secondary fallback: explicit VIRTUAL_ENV, if present.
    resolved = _tool_in_venv(os.environ.get('VIRTUAL_ENV', '').strip(), 'bst')
    if resolved:
        return resolved

    # Last resort: PATH lookup.
    which_bst = shutil.which('bst')
    return which_bst or 'bst'


@shared_task(bind=True, queue='build')
def buildstream_build_task(self, bst_source_id, force_rebuild=False):
    """
    Build a BuildStream project from a git repository.

    Pipeline:
      1. Clone the git repository at the requested branch.
      1b. (force_rebuild only) Run ``bst artifact delete <bst_element>`` to
          purge the cached artifact so BST performs a full rebuild from source.
      2. Run ``bst build <bst_element>`` inside the cloned project directory.
      3. Run ``bst artifact checkout <bst_element> --directory <checkout_dir>``
         to extract the artifact.  The checkout directory is an OSTree flatpak
         repo that is imported via ``flatpak build-commit-from``.
      4. Status goes to "built" (the normal publish pipeline takes it from there).
    """
    from apps.flatpak.models import BuildStreamSource, Build, BuildLog, SiteConfig

    source = None
    build = None
    temp_dir = None

    try:
        source = BuildStreamSource.objects.get(id=bst_source_id)

        bst_version_str = getattr(source, 'bst_version', 'bst2')
        bst_binary = _get_bst_binary(bst_version_str)
        logger.info(
            "BuildStream source %s resolved bst binary: %s",
            bst_source_id,
            bst_binary,
        )
        # BST 2 uses a FUSE-based CAS stager (buildboxcommon_fusestager) by
        # default.  On server hosts where /dev/fuse is absent or unprivileged
        # FUSE mounts are disallowed, the stager child process dies with exit
        # code 2 and the whole build fails.  --no-fuse forces BST 2 to use the
        # plain (non-FUSE) fallback stager which works in any environment.
        # BST 1 does not recognise this flag, so only add it for BST 2.
        bst_global_flags = ['--no-fuse'] if bst_version_str != 'bst1' else []

        # Create Build history record.
        # Use get_or_create to handle duplicate dispatch from check_pending_builds.
        build, created = Build.objects.get_or_create(
            bst_source=source,
            build_number=source.build_number,
            defaults={
                'status': 'building',
                'started_at': timezone.now(),
                'celery_task_id': self.request.id or '',
            },
        )
        if not created:
            # A Build record already exists for this build_number — stale duplicate.
            logger.warning(
                f"Duplicate task for BST source {bst_source_id} build "
                f"#{source.build_number} (existing status: '{build.status}') — skipping"
            )
            return

        source.status = 'building'
        source.save()

        log_build(build, 'info', f"Starting BuildStream build for {source.name}")
        log_build(build, 'info', f"Using BuildStream binary: {bst_binary}")
        log_build(build, 'info', f"Element: {source.bst_element}")
        send_build_status_update(bst_source_id, 'building', 'Cloning git repository')

        temp_dir = tempfile.mkdtemp(prefix=f'fmdc_bst_{source.build_number}_')
        log_build(build, 'info', f"Created build directory: {temp_dir}")

        # ── 1. Clone ────────────────────────────────────────────────────────
        log_build(build, 'info', f"Cloning {source.git_repo_url} (branch: {source.git_branch})")
        clone_result = subprocess.run(
            ['git', 'clone', '--branch', source.git_branch, '--depth', '1',
             '--recurse-submodules', source.git_repo_url, 'source'],
            cwd=temp_dir,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if clone_result.returncode != 0:
            raise RuntimeError(f"Git clone failed: {clone_result.stderr}")

        source_dir = os.path.join(temp_dir, 'source')

        # Record the source commit
        commit_result = subprocess.run(
            ['git', 'rev-parse', 'HEAD'],
            cwd=source_dir, capture_output=True, text=True,
        )
        if commit_result.returncode == 0:
            source.source_commit = commit_result.stdout.strip()
            source.save()
            log_build(build, 'info', f"Source commit: {source.source_commit}")

        # ── 1b. Clear artifact cache (force rebuild only) ──────────────────
        # Use --deps all so that every sub-element artifact is purged, not just
        # the top-level element.  Without this, BST re-assembles the top-level
        # from its still-cached (still-corrupt) dependencies and the import
        # step brings the corrupted content objects back in unchanged.
        # With all deps cleared, BST re-pulls each from the remote cache
        # (freedesktop-sdk's own servers, which are clean) or rebuilds from
        # source, giving us guaranteed-clean artifacts.
        if force_rebuild:
            send_build_status_update(bst_source_id, 'building', 'Clearing BST artifact cache for full rebuild')
            log_build(build, 'info', f"Force rebuild: deleting all cached artifacts (--deps all) for {source.bst_element}")
            delete_result = subprocess.run(
                [bst_binary, *bst_global_flags, 'artifact', 'delete', '--deps', 'all', source.bst_element],
                cwd=source_dir, capture_output=True, text=True, timeout=300,
            )
            if delete_result.returncode != 0:
                # Log the warning but don't abort — BST may report non-zero if
                # the artifact simply isn't cached yet (first-ever build).
                log_build(build, 'warning',
                    f"bst artifact delete exited {delete_result.returncode}: {delete_result.stderr.strip()[:500]}")
            else:
                log_build(build, 'info', "All artifact caches cleared — BST will re-pull or rebuild all elements")

        # bst build
        send_build_status_update(bst_source_id, 'building', f'Running bst build {source.bst_element}')
        log_build(build, 'info', f"Running: {bst_binary} build {source.bst_element}")

        try:
            config = SiteConfig.get_solo()
            build_timeout = max(getattr(config, 'build_timeout', 3600), 3600)
        except Exception:
            build_timeout = 7200

        bst_cmd = [bst_binary, *bst_global_flags, 'build', source.bst_element]
        bst_build_result = run_cancellable(bst_cmd, cwd=source_dir, build=build, timeout_seconds=build_timeout)

        if bst_build_result.returncode != 0:
            raise RuntimeError(
                f"bst build failed (exit {bst_build_result.returncode}):\n"
                f"{bst_build_result.stderr}"
            )

        log_build(build, 'info', "bst build completed successfully")

        # bst artifact checkout
        checkout_dir = os.path.join(temp_dir, 'bst-checkout')
        os.makedirs(checkout_dir, exist_ok=True)

        send_build_status_update(bst_source_id, 'building', 'Checking out BuildStream artifact')
        log_build(build, 'info', f"Checking out artifact to {checkout_dir}")

        try:
            _cfg = SiteConfig.get_solo()
            bst_checkout_timeout = max(getattr(_cfg, 'bst_checkout_timeout_minutes', 30), 10) * 60
        except Exception:
            bst_checkout_timeout = 1800

        checkout_cmd = [bst_binary, *bst_global_flags, 'artifact', 'checkout', source.bst_element, '--directory', checkout_dir]
        checkout_result = subprocess.run(
            checkout_cmd, cwd=source_dir,
            capture_output=True, text=True, timeout=bst_checkout_timeout,
        )
        if checkout_result.returncode != 0:
            raise RuntimeError(
                f"bst artifact checkout failed (exit {checkout_result.returncode}):\n"
                f"{checkout_result.stderr}"
            )

        log_build(build, 'info', "Artifact checked out")

        # Always capture the full list of OSTree refs exported by this element
        # so it can be displayed on the detail page without re-running BST.
        refs_in_checkout = subprocess.run(
            ['ostree', 'refs', f'--repo={checkout_dir}'],
            capture_output=True, text=True, timeout=30,
        )
        if refs_in_checkout.returncode == 0:
            ref_list = sorted(r.strip() for r in refs_in_checkout.stdout.splitlines() if r.strip())
            source.produced_refs = '\n'.join(ref_list)
            source.save(update_fields=['produced_refs'])
            log_build(build, 'info', f"Captured {len(ref_list)} produced refs from checkout")

        # flatpak build-commit-from -> build-repo
        # The checked-out artifact is a flatpak OSTree repo.
        build_repo_path = os.path.join(settings.REPOS_BASE_PATH, 'build-repo')
        os.makedirs(build_repo_path, exist_ok=True)

        # Initialise build-repo if it doesn't exist yet
        if not os.path.exists(os.path.join(build_repo_path, 'config')):
            subprocess.run(
                ['ostree', 'init', '--repo', build_repo_path, '--mode=archive'],
                check=True, capture_output=True, text=True,
            )

        # ── 3b. Force reimport: delete build-repo refs matching the checkout ──
        # This is the critical step that makes force_rebuild actually fix
        # OSTree-level corruption.  When the destination refs already exist with
        # the same commit hash, flatpak build-commit-from says "no change" and
        # never rewrites content objects — leaving corrupted ones in place.
        # By deleting the refs first and pruning orphaned objects, we guarantee
        # that every content object (including any corrupted ones) is rewritten
        # from scratch during the subsequent import.
        if force_rebuild:
            send_build_status_update(bst_source_id, 'building', 'Clearing build-repo refs for clean reimport')
            log_build(build, 'info', "Force rebuild: listing refs in BST checkout to clear from build-repo")
            list_refs_result = subprocess.run(
                ['ostree', 'refs', f'--repo={checkout_dir}'],
                capture_output=True, text=True, timeout=30,
            )
            if list_refs_result.returncode == 0:
                checkout_refs = [r.strip() for r in list_refs_result.stdout.splitlines() if r.strip()]
                log_build(build, 'info', f"Deleting {len(checkout_refs)} refs from build-repo to force fresh commits")
                for ref in checkout_refs:
                    subprocess.run(
                        ['ostree', 'refs', '--delete', ref, f'--repo={build_repo_path}'],
                        capture_output=True, text=True, timeout=30,
                    )
                # Prune objects no longer reachable from any ref
                prune_result = subprocess.run(
                    ['ostree', 'prune', '--refs-only', f'--repo={build_repo_path}'],
                    capture_output=True, text=True, timeout=300,
                )
                if prune_result.returncode == 0:
                    log_build(build, 'info',
                        f"Orphaned objects pruned from build-repo: {prune_result.stdout.strip()[:300]}")
                else:
                    log_build(build, 'warning',
                        f"ostree prune warning: {prune_result.stderr.strip()[:300]}")
                # Delete any corrupted objects that are still referenced by OTHER
                # refs (e.g. a different BST source's refs staying in build-repo).
                # Without this, OSTree skips re-writing objects it thinks it already
                # has, leaving the corrupted data in place even after a clean import.
                fsck_delete_result = subprocess.run(
                    ['ostree', 'fsck', '--delete', f'--repo={build_repo_path}'],
                    capture_output=True, text=True, timeout=300,
                )
                if fsck_delete_result.returncode == 0:
                    log_build(build, 'info', "ostree fsck --delete: no corrupted objects found (repo is clean)")
                else:
                    # Non-zero means corrupted objects were found AND deleted
                    log_build(build, 'info',
                        f"Corrupted objects removed from build-repo by fsck --delete: "
                        f"{fsck_delete_result.stderr.strip()[:500]}")
            else:
                log_build(build, 'warning',
                    f"Could not list checkout refs for cleanup: {list_refs_result.stderr.strip()[:300]}")

        send_build_status_update(bst_source_id, 'building', 'Importing artifact into build-repo')
        log_build(build, 'info', "Importing artifact into build-repo via flatpak build-commit-from")

        # NOTE: --disable-fsync is intentionally omitted.
        # build-repo uses archive-z2 mode (zlib-compressed .filez objects).  With
        # --disable-fsync the kernel may not flush dirty pages before the process
        # exits, leaving compressed objects whose decompressed content does not
        # match the filename (SHA256 mismatch → ostree fsck corruption).
        import_cmd = [
            'flatpak', 'build-commit-from',
            f'--src-repo={checkout_dir}',
            f'--subject=BuildStream build {source.build_number} of {source.bst_element}',
            build_repo_path,
        ]
        import_result = run_cancellable(import_cmd, cwd=temp_dir, build=build, timeout_seconds=bst_checkout_timeout)

        if import_result.returncode != 0:
            raise RuntimeError(
                f"flatpak build-commit-from failed (exit {import_result.returncode}):\n"
                f"{import_result.stderr}"
            )

        log_build(build, 'info', "Artifact imported into build-repo successfully")

        # Post-import integrity check: remove any corrupted objects that were
        # written during this import.  Runs on every build (not only force_rebuild)
        # so we catch hardware/fs corruption early and never ship a broken repo.
        post_fsck = subprocess.run(
            ['ostree', 'fsck', '--delete', f'--repo={build_repo_path}'],
            capture_output=True, text=True, timeout=300,
        )
        if post_fsck.returncode == 0:
            log_build(build, 'info', "Post-import fsck: build-repo is clean")
        else:
            log_build(build, 'warning',
                f"Post-import fsck found and deleted corrupted objects: "
                f"{post_fsck.stderr.strip()[:800]}")
            # Warn but do not abort — the corrupted objects have been removed;
            # the refs they belonged to will show up as broken in the UI checker.

        # Version detection: query BST variables for the top-level element only
        # and extract the 'version' key from the YAML vars block.
        version_result = subprocess.run(
            [bst_binary, *bst_global_flags, 'show', '--deps', 'none', '--format', '%{vars}', source.bst_element],
            cwd=source_dir, capture_output=True, text=True, timeout=60,
        )
        if version_result.returncode == 0:
            import re as _re
            # vars output is YAML-ish; parse 'version: X.Y.Z' from it
            m = _re.search(r'^version:\s*(\S+)', version_result.stdout, _re.MULTILINE)
            detected_version = m.group(1).strip('"\'') if m else ''
            # Sanity-check: reject BST format strings that weren't expanded
            if detected_version and '%{' not in detected_version:
                source.version = detected_version
                log_build(build, 'info', f"Detected version: {detected_version}")

        source.status = 'published'
        source.save()

        build.status = 'published'
        build.completed_at = timezone.now()
        build.save()

        log_build(build, 'info', "BuildStream build completed and published to build-repo")
        send_build_status_update(bst_source_id, 'published', 'Build completed and ready to promote')

        # Auto-reset any promotions that were waiting on a rebuild triggered by
        # missing build-repo objects — the rebuild has now completed successfully.
        waiting = source.promotions.filter(
            status='failed', error_message__contains='[REBUILD_TRIGGERED]'
        )
        reset_count = waiting.count()
        if reset_count:
            waiting.update(status='pending', error_message='', completed_at=None)
            log_build(build, 'info',
                      f"Auto-reset {reset_count} promotion(s) to pending after successful rebuild")
            logger.info(
                f"BST source {bst_source_id}: auto-reset {reset_count} failed promotion(s) "
                f"to pending after rebuild"
            )

    except BuildStreamSource.DoesNotExist:
        logger.error(f"BuildStreamSource {bst_source_id} not found")
    except Exception as e:
        logger.error(f"BuildStream build failed for source {bst_source_id}: {e}")

        if source:
            source.status = 'failed'
            source.error_message = str(e)[:2000]
            source.save()

        if build:
            build.status = 'failed'
            build.error_message = str(e)[:2000]
            build.completed_at = timezone.now()
            build.save()
            log_build(build, 'error', f"BuildStream build failed: {str(e)}")

        send_build_status_update(bst_source_id, 'failed', f'Build failed: {str(e)}')
    finally:
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
                logger.info(f"Cleaned up temp directory: {temp_dir}")
            except Exception as e:
                logger.warning(f"Failed to cleanup temp directory {temp_dir}: {e}")


@shared_task
def commit_package_task(package_id):
    """
    Commit a build - validates the build and marks it ready for publishing.
    For upload-based builds, this validates all refs have been uploaded.
    For git-based builds, this is called after flatpak-builder completes.
    """
    from apps.flatpak.models import Package, Build, BuildLog
    
    try:
        package = Package.objects.get(id=package_id)
        
        if package.status not in ['pending', 'building', 'built']:
            raise ValueError(f"Cannot commit build with status: {package.status}")
        
        # Get or create Build history record for this attempt
        build, created = Build.objects.get_or_create(
            package=package,
            build_number=package.build_number,
            defaults={'status': 'committing', 'started_at': timezone.now()}
        )
        if not created:
            build.status = 'committing'
            build.save()
        
        log_build(build, 'info', "Committing build")
        package.status = 'committing'
        package.save()
        
        send_build_status_update(package_id, 'committing', 'Validating build')
        
        # Get build repo
        build_repo_path = os.path.join(settings.REPOS_BASE_PATH, 'build-repo')
        
        if not os.path.exists(os.path.join(build_repo_path, 'config')):
            raise FileNotFoundError("Build repository not found")
        
        # Verify the ref exists in build-repo
        ref_name = f'app/{package.package_id}/{package.arch}/{package.branch}'
        
        check_ref = subprocess.run(
            ['ostree', 'refs', f'--repo={build_repo_path}'],
            capture_output=True,
            text=True
        )
        
        if check_ref.returncode == 0:
            refs = check_ref.stdout.strip().split('\n')
            log_build(build, 'info', f"Found refs in build-repo: {', '.join(refs)}")
            
            if ref_name not in refs and refs != ['']:
                # Try to find any ref for this app
                app_refs = [r for r in refs if package.package_id in r]
                if app_refs:
                    log_build(build, 'warning', f"Exact ref not found, but found: {', '.join(app_refs)}")
                    ref_name = app_refs[0]  # Use the first match
                else:
                    raise ValueError(f"No refs found for {package.package_id}")

            # Capture all refs that belong to this package (app + appstream + runtime variants)
            all_package_refs = sorted(r for r in refs if r and package.package_id in r)
            if all_package_refs:
                package.produced_refs = '\n'.join(all_package_refs)
                log_build(build, 'info', f"Captured {len(all_package_refs)} produced ref(s): {', '.join(all_package_refs)}")
        
        # Get the commit hash for this ref
        show_commit = subprocess.run(
            ['ostree', 'show', f'--repo={build_repo_path}', '--print-metadata-key=ostree.commit.timestamp', ref_name],
            capture_output=True,
            text=True
        )
        
        if show_commit.returncode == 0:
            # Extract commit hash from ostree show output
            rev_parse = subprocess.run(
                ['ostree', 'rev-parse', f'--repo={build_repo_path}', ref_name],
                capture_output=True,
                text=True
            )
            if rev_parse.returncode == 0:
                commit_hash = rev_parse.stdout.strip()
                package.commit_hash = commit_hash
                log_build(build, 'info', f"Commit hash: {commit_hash}")
        
        package.status = 'committed'
        package.save()
        
        build.status = 'committed'
        build.completed_at = timezone.now()
        build.save()
        
        log_build(build, 'info', "Build committed successfully, ready to publish")
        send_build_status_update(package_id, 'committed', 'Build committed, ready to publish')
        
    except Package.DoesNotExist:
        logger.error(f"Package {package_id} not found")
    except Exception as e:
        logger.error(f"Error committing build {package_id}: {str(e)}")
        if 'package' in locals() and package:
            package.status = 'failed'
            package.error_message = str(e)
            package.save()
        if 'build' in locals() and build:
            build.status = 'failed'
            build.error_message = str(e)
            build.completed_at = timezone.now()
            build.save()
            log_build(build, 'error', f"Commit failed: {str(e)}")
        send_build_status_update(package_id, 'failed', f'Commit failed: {str(e)}')


@shared_task
def publish_package_task(package_id, generate_deltas=False):
    """
    Publish a committed build to the target repository.
    This pulls the commit from build-repo and pushes it to the main repository.
    Pass generate_deltas=True to regenerate static deltas (expensive; off by default).
    """
    from apps.flatpak.models import Package, Build, BuildLog
    from apps.flatpak.utils.ostree import sign_repo_summary, temp_gpg_homedir, update_repo_metadata
    
    try:
        package = Package.objects.get(id=package_id)
        
        if package.status != 'committed':
            raise ValueError(f"Cannot publish build with status: {package.status}. Must be 'committed'.")
        
        # Get Build history record for this attempt
        build = Build.objects.filter(
            package=package,
            build_number=package.build_number
        ).first()
        
        if not build:
            # Create Build record if it doesn't exist (shouldn't happen but be defensive)
            build = Build.objects.create(
                package=package,
                build_number=package.build_number,
                status='publishing',
                started_at=timezone.now()
            )
        else:
            build.status = 'publishing'
            build.save()
        
        log_build(build, 'info', "Publishing build to repository")
        package.status = 'publishing'
        package.save()
        
        send_build_status_update(package_id, 'publishing', 'Publishing to repository')
        
        # Get repositories
        build_repo_path = os.path.join(settings.REPOS_BASE_PATH, 'build-repo')
        target_repo_path = package.repository.repo_path
        
        if not os.path.exists(os.path.join(target_repo_path, 'config')):
            raise FileNotFoundError(f"Target repository {package.repository.name} not found")
        
        # Determine the ref name
        ref_name = f'app/{package.package_id}/{package.arch}/{package.branch}'

        # Check whether the *same* commit is already in the target repo.
        # We must compare hashes, not just ref names — a new build for the same
        # package+branch produces a different commit that must replace the old one.
        def _resolve_ref(repo_path, ref):
            r = subprocess.run(
                ['ostree', 'rev-parse', f'--repo={repo_path}', ref],
                capture_output=True, text=True,
            )
            return r.stdout.strip() if r.returncode == 0 else None

        build_repo_commit = _resolve_ref(build_repo_path, ref_name)
        target_commit = _resolve_ref(target_repo_path, ref_name)
        already_current = (build_repo_commit and target_commit and
                           build_repo_commit == target_commit)

        if already_current:
            log_build(build, 'info',
                      f"{ref_name} already at commit {build_repo_commit[:12]} in "
                      f"{package.repository.name} — skipping pull")
        else:
            log_build(build, 'info', f"Pulling {ref_name} from build-repo to {package.repository.name}")

            # Ensure build-repo summary is up-to-date so ostree pull-local can resolve the ref.
            subprocess.run(
                ['ostree', 'summary', '--update', f'--repo={build_repo_path}'],
                capture_output=True, text=True,
            )

            pull_result = subprocess.run(
                ['ostree', 'pull-local',
                 f'--repo={target_repo_path}',
                 build_repo_path,
                 ref_name],
                capture_output=True,
                text=True,
                timeout=300,
            )

            if pull_result.returncode != 0:
                raise RuntimeError(f"Failed to pull commit: {pull_result.stderr}")

            log_build(build, 'info', f"Successfully pulled {ref_name}")

        # Also pull the .Locale ref if it exists in build-repo (contains locale files)
        locale_ref = f'runtime/{package.package_id}.Locale/{package.arch}/{package.branch}'
        locale_refs_result = subprocess.run(
            ['ostree', 'refs', f'--repo={build_repo_path}'],
            capture_output=True, text=True
        )
        if locale_ref in (locale_refs_result.stdout or ''):
            locale_pull = subprocess.run(
                ['ostree', 'pull-local', f'--repo={target_repo_path}', build_repo_path, locale_ref],
                capture_output=True, text=True, timeout=300
            )
            if locale_pull.returncode == 0:
                log_build(build, 'info', f"Pulled locale ref {locale_ref}")
            else:
                log_build(build, 'warning', f"Failed to pull locale ref: {locale_pull.stderr}")
        
        # Update repository metadata including appstream (version info visible via flatpak remote-ls).
        # update_repo_metadata: purges stale unsigned deltas, runs build-update-repo with GPG-signed
        # delta superblocks and summary, then signs every individual commit so non-delta pulls verify.
        log_build(build, 'info', "Updating repository metadata and appstream data")
        gpg_key = package.repository.gpg_key
        if gpg_key:
            log_build(build, 'info', f"Signing with GPG key {gpg_key.key_id}")
        meta_result = update_repo_metadata(target_repo_path, gpg_key, generate_deltas=generate_deltas)
        if meta_result['success']:
            log_build(build, 'info', "Repository metadata updated and signed successfully")
        else:
            log_build(build, 'warning',
                      f"Repository metadata update issue: {meta_result.get('message', '')} "
                      f"{meta_result.get('detail', meta_result.get('error', ''))}")
            logger.warning("update_repo_metadata warning for %s: %s", target_repo_path, meta_result)
        
        # Mark as published
        package.status = 'published'
        package.save()
        
        build.status = 'published'
        build.completed_at = timezone.now()
        build.save()
        
        log_build(build, 'info', f"Build published successfully to {package.repository.name}")
        send_build_status_update(package_id, 'published', 'Build published successfully')
        
    except Package.DoesNotExist:
        logger.error(f"Package {package_id} not found")
    except Exception as e:
        logger.error(f"Error publishing build {package_id}: {str(e)}")
        if 'package' in locals() and package:
            package.status = 'failed'
            package.error_message = str(e)
            package.save()
        if 'build' in locals() and build:
            build.status = 'failed'
            build.error_message = str(e)
            build.completed_at = timezone.now()
            build.save()
            log_build(build, 'error', f"Publish failed: {str(e)}")
        send_build_status_update(package_id, 'failed', f'Publish failed: {str(e)}')


# Pre-compiled regex for ANSI escape sequences (CSI + other ESC sequences)
_ANSI_ESC_RE = re.compile(r'\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')


def _log_external(ext_ref, level, message):
    """Append a timestamped line to ExternalRef.log and save."""
    from django.utils import timezone as tz
    # Sanitize to ASCII: the log column may not be utf8mb4, and ostree / other
    # tools can produce non-ASCII characters (progress arrows, em-dashes, etc.)
    message = str(message).encode('ascii', errors='replace').decode('ascii')
    line = f"[{tz.now().strftime('%H:%M:%S')}] [{level.upper()}] {message}"
    ext_ref.log = (ext_ref.log + '\n' + line).lstrip('\n')
    ext_ref.save(update_fields=['log', 'updated_at'])


def _fixup_upstream_appstream(build_repo_path, target_repo_path, gpg_key, log_fn=None):
    """
    Copy appstream/x86_64 and appstream2/x86_64 from build-repo into
    target_repo_path, then re-sign every copied commit and regenerate the
    summary so 'flatpak list' can show version numbers.

    Must be called AFTER update_repo_metadata() because flatpak
    build-update-repo regenerates (and empties) appstream/x86_64 from the
    stub XMLs embedded in individual Flathub app commits.

    Non-fatal: any failure is logged as a warning, not raised.
    """
    from apps.flatpak.utils.ostree import temp_gpg_homedir

    appstream_refs = ('appstream/x86_64', 'appstream2/x86_64')
    copied = []

    for ref in appstream_refs:
        # Resolve ref → commit hash.  ostree pull-local <ref> looks the ref up
        # in the source repo's *summary*, but the appstream refs we pulled from
        # a network remote may only be indexed under the remote namespace
        # (e.g. flathub:appstream/x86_64) and therefore absent from the plain
        # summary.  Pulling by commit hash bypasses the summary entirely.
        check = subprocess.run(
            ['ostree', 'rev-parse', f'--repo={build_repo_path}', ref],
            capture_output=True, text=True,
        )
        if check.returncode != 0 or not check.stdout.strip():
            if log_fn:
                log_fn('warning', f"appstream fixup: {ref} not in build-repo, skipping")
            continue
        commit = check.stdout.strip()

        pull = subprocess.run(
            ['ostree', 'pull-local', f'--repo={target_repo_path}', build_repo_path, commit],
            capture_output=True, text=True, timeout=120,
        )
        if pull.returncode != 0:
            if log_fn:
                log_fn('warning',
                       f"appstream fixup: pull-local {ref} failed: "
                       f"{pull.stderr.strip() or pull.stdout.strip()}")
            continue

        # Create (or update) the named ref in the target repo so flatpak can
        # find it by name when serving the appstream data to clients.
        subprocess.run(
            ['ostree', 'refs', f'--repo={target_repo_path}',
             '--force', f'--create={ref}', commit],
            capture_output=True, text=True,
        )

        copied.append(ref)
        if log_fn:
            log_fn('info', f"appstream fixup: copied {ref} from build-repo")

    if not copied:
        return

    # Sign every copied appstream commit with the repo GPG key.
    if gpg_key:
        with temp_gpg_homedir(gpg_key) as homedir:
            for ref in copied:
                rev = subprocess.run(
                    ['ostree', 'rev-parse', f'--repo={target_repo_path}', ref],
                    capture_output=True, text=True,
                )
                if rev.returncode != 0 or not rev.stdout.strip():
                    continue
                sign = subprocess.run(
                    ['ostree', f'--repo={target_repo_path}', 'gpg-sign',
                     f'--gpg-homedir={homedir}', rev.stdout.strip(), gpg_key.key_id],
                    capture_output=True, text=True,
                )
                if sign.returncode != 0 and log_fn:
                    log_fn('warning',
                           f"appstream fixup: gpg-sign {ref} failed: "
                           f"{sign.stderr.strip()}")

            # Re-generate + sign the summary to include the new appstream commits.
            subprocess.run(
                ['ostree', 'summary', f'--repo={target_repo_path}', '-u',
                 '--gpg-sign', gpg_key.key_id, '--gpg-homedir', homedir],
                capture_output=True, text=True,
            )
    else:
        subprocess.run(
            ['ostree', 'summary', f'--repo={target_repo_path}', '-u'],
            capture_output=True, text=True,
        )


def _resolve_remote_ref(remote_name, ref):
    """Resolve the current commit hash and *correct* OSTree ref path for a
    Flatpak remote ref.

    Returns (commit_hash, resolved_ref) where resolved_ref is the actual path
    used by the OSTree repository (which may differ from *ref* in the leading
    type prefix).  Flathub stores some BaseApps/Extensions under 'app/' even
    though they look like 'runtime/' refs at the flatpak level.

    Tries:
      1. ref as-is
      2. swapped prefix (runtime/ <-> app/)
      3. no leading prefix (name/arch/branch only)
    across '', --system, and --user flatpak scopes.

    Returns ('', ref) when nothing could be resolved.
    """
    parts = ref.split('/')
    ref_forms = [ref]
    if parts[0] in ('runtime', 'app'):
        alt = 'app' if parts[0] == 'runtime' else 'runtime'
        ref_forms.append('/'.join([alt] + parts[1:]))
        ref_forms.append('/'.join(parts[1:]))
    elif len(parts) >= 3:
        ref_forms.extend([f'runtime/{ref}', f'app/{ref}'])

    for ref_form in ref_forms:
        for scope_flag in ('', '--system', '--user'):
            cmd = ['flatpak', 'remote-info']
            if scope_flag:
                cmd.append(scope_flag)
            cmd.extend(['--show-commit', remote_name, ref_form])
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode == 0:
                lines = (result.stdout or '').strip().splitlines()
                if lines:
                    value = lines[-1].strip()
                    if re.fullmatch(r'[0-9a-f]{64}', value):
                        return value, ref_form
    return '', ref


def _get_flatpak_remote_commit(remote_name, ref):
    """Convenience wrapper — returns only the commit hash (backwards compat)."""
    commit, _ = _resolve_remote_ref(remote_name, ref)
    return commit


@shared_task
def pull_external_ref_task(external_ref_id):
    """
    Pull an OSTree ref from its configured flatpak remote into build-repo,
    then immediately publish it to the target repository.
    """
    from apps.flatpak.models import ExternalRef, ExternalRefVersion
    from apps.flatpak.utils.ostree import update_repo_metadata

    try:
        ext = ExternalRef.objects.select_related('repository', 'remote').get(pk=external_ref_id)
    except ExternalRef.DoesNotExist:
        logger.error(f"ExternalRef {external_ref_id} not found")
        return

    try:
        ext.status = 'pulling'
        ext.error_message = ''
        ext.log = ''
        ext.save()

        if not ext.remote:
            raise ValueError("No remote associated with this external ref")

        remote_name = ext.remote.name
        ref = ext.ref

        _log_external(ext, 'info', f"Pulling {ref} from remote '{remote_name}'")

        # Ensure the flatpak remote is registered (try system first, then user)
        ensure_flatpak_remote(remote_name, ext.remote.url, '--system')
        # Also try user scope in case system scope fails silently
        ensure_flatpak_remote(remote_name, ext.remote.url, '--user')

        # Get the actual OSTree URL from flatpak
        url_result = subprocess.run(
            ['flatpak', 'remotes', '--columns=name,url'],
            capture_output=True, text=True
        )
        remote_url = None
        for line in url_result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] == remote_name:
                remote_url = parts[1]
                break

        if not remote_url:
            # Fall back to the URL stored in the FlatpakRemote record (may be .flatpakrepo URL)
            # Try to derive OSTree URL by removing .flatpakrepo suffix
            remote_url = ext.remote.url
            if remote_url.endswith('.flatpakrepo'):
                # Common pattern: https://dl.flathub.org/repo/ from https://dl.flathub.org/repo/flathub.flatpakrepo
                remote_url = remote_url.rsplit('/', 1)[0] + '/'
            _log_external(ext, 'warning', f"Could not detect remote URL via flatpak, using: {remote_url}")

        _log_external(ext, 'info', f"Remote OSTree URL: {remote_url}")

        build_repo_path = os.path.join(settings.REPOS_BASE_PATH, 'build-repo')
        os.makedirs(build_repo_path, exist_ok=True)
        if not os.path.exists(os.path.join(build_repo_path, 'config')):
            subprocess.run(
                ['ostree', 'init', '--mode=archive-z2', f'--repo={build_repo_path}'],
                check=True, capture_output=True
            )

        # Register the remote in the build-repo OSTree config so we can pull
        # from it. --no-gpg-verify avoids needing to import remote GPG keys.
        subprocess.run(
            ['ostree', 'remote', 'add', '--if-not-exists', '--no-gpg-verify',
             f'--repo={build_repo_path}', remote_name, remote_url],
            capture_output=True, text=True
        )
        _log_external(ext, 'info', f"Remote '{remote_name}' configured in build-repo")

        upstream_commit, resolved_ref = _resolve_remote_ref(remote_name, ref)
        if upstream_commit:
            _log_external(ext, 'info', f"Upstream commit: {upstream_commit[:12]}")
            if resolved_ref != ref:
                _log_external(ext, 'info',
                              f"Ref corrected: {ref} -> {resolved_ref} (remote uses different prefix)")
                ref = resolved_ref
                ext.ref = resolved_ref
        else:
            _log_external(ext, 'warning',
                          f"Could not determine upstream commit for {ref} via flatpak remote-info")

        # Pull the exact commit from the remote. We create/update the plain ref
        # explicitly afterwards instead of relying on --mirror ref semantics.
        pull_target = f'{ref}@{upstream_commit}' if upstream_commit else ref
        _log_external(ext, 'info',
                      f"Starting ostree pull of {pull_target} (may take several minutes for large refs)")
        pull_start = time.monotonic()
        pull_proc = subprocess.Popen(
            ['ostree', 'pull', f'--repo={build_repo_path}', remote_name, pull_target],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        try:
            last_heartbeat = time.monotonic()
            for line in pull_proc.stdout:
                line = line.rstrip('\r\n')
                if not line or line.startswith('\r') or '\r' in line:
                    # Skip carriage-return progress bars
                    continue
                _log_external(ext, 'info', line)
                last_heartbeat = time.monotonic()
            pull_proc.wait(timeout=1800)
        except subprocess.TimeoutExpired:
            pull_proc.kill()
            pull_proc.wait()
            raise RuntimeError("ostree pull timed out after 30 minutes")
        elapsed = time.monotonic() - pull_start

        if pull_proc.returncode != 0:
            raise RuntimeError(f"ostree pull failed (exit {pull_proc.returncode})")
        _log_external(ext, 'info', f"ostree pull completed in {elapsed:.0f}s")

        _log_external(ext, 'info', f"ostree pull succeeded")

        # Resolve the pulled commit in build-repo. Prefer the upstream commit we
        # just queried; otherwise fall back to resolving the ref locally.
        commit = upstream_commit
        resolved_ref_name = f'{ref}@{upstream_commit}' if upstream_commit else None
        if not commit:
            rev_result = subprocess.run(
                ['ostree', 'rev-parse', f'--repo={build_repo_path}', ref],
                capture_output=True, text=True
            )
            if rev_result.returncode == 0:
                commit = rev_result.stdout.strip()
                resolved_ref_name = ref

        if commit:
            ext.commit_hash = commit
            _log_external(ext, 'info',
                          f"Resolved commit {commit[:12]} from {resolved_ref_name}")

            # Ensure the canonical plain ref exists in build-repo so later
            # pull-local / promotion paths can reference ext.ref directly.
            refs_result = subprocess.run(
                ['ostree', 'refs', f'--repo={build_repo_path}'],
                capture_output=True, text=True
            )
            visible_refs = [r.strip() for r in refs_result.stdout.splitlines() if r.strip()]
            if ref not in visible_refs:
                create_ref = subprocess.run(
                    ['ostree', 'refs', f'--repo={build_repo_path}', '--force', f'--create={ref}', commit],
                    capture_output=True, text=True
                )
                if create_ref.returncode == 0:
                    _log_external(ext, 'info',
                                  f"Created canonical local ref {ref} -> {commit[:12]}")
                else:
                    _log_external(ext, 'warning',
                                  "Could not create canonical local ref "
                                  f"{ref}: {create_ref.stderr.strip() or create_ref.stdout.strip()}")
        else:
            refs_result = subprocess.run(
                ['ostree', 'refs', f'--repo={build_repo_path}'],
                capture_output=True, text=True
            )
            visible_refs = [r.strip() for r in refs_result.stdout.splitlines() if r.strip()]
            _log_external(
                ext,
                'warning',
                "Could not resolve commit hash after mirror pull. "
                f"Visible refs: {', '.join(visible_refs[:20]) or '(none)'}"
            )

        # Also pull the upstream appstream refs so version metadata (shown by
        # 'flatpak list') is available in the target repo. Flathub and similar
        # remotes do NOT embed AppStream in individual app commits; the data
        # lives exclusively in the appstream/x86_64 (and appstream2/x86_64)
        # refs. We pull them now into build-repo so publish/promote can copy
        # them to the target after flatpak build-update-repo runs.
        for appstream_ref in ('appstream/x86_64', 'appstream2/x86_64'):
            _log_external(ext, 'info', f"Pulling {appstream_ref} from remote")
            as_proc = subprocess.Popen(
                ['ostree', 'pull', f'--repo={build_repo_path}', remote_name, appstream_ref],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
            try:
                for line in as_proc.stdout:
                    line = line.rstrip('\r\n')
                    if line and '\r' not in line:
                        _log_external(ext, 'info', line)
                as_proc.wait(timeout=300)
            except subprocess.TimeoutExpired:
                as_proc.kill()
                as_proc.wait()
                _log_external(ext, 'warning', f"Timed out pulling {appstream_ref} (non-fatal)")
                continue
            if as_proc.returncode == 0:
                _log_external(ext, 'info', f"Pulled {appstream_ref} successfully")
            else:
                _log_external(ext, 'warning',
                              f"Could not pull {appstream_ref} (non-fatal, exit {as_proc.returncode})")

        # Regenerate the summary so pull-local can find the ref by name.
        subprocess.run(
            ['ostree', 'summary', f'--repo={build_repo_path}', '-u'],
            capture_output=True, text=True
        )

        ext.status = 'pulled'
        from django.utils import timezone as tz
        _now = tz.now()
        ext.last_pulled_at = _now
        # Record the upstream commit we just fetched so the Update column shows
        # "Up to date" immediately — without waiting for the periodic check task.
        if upstream_commit:
            ext.upstream_commit = upstream_commit
            ext.upstream_checked_at = _now
            ext.update_available = False
        ext.save()

        if commit:
            version, created = ExternalRefVersion.objects.get_or_create(
                external_ref=ext,
                commit_hash=commit,
                defaults={
                    'ref': ref,
                    'upstream_commit': upstream_commit or '',
                    'pulled_at': _now,
                    'status': 'pulled',
                    'error_message': '',
                },
            )
            if not created:
                version.ref = ref
                version.upstream_commit = upstream_commit or ''
                version.status = 'pulled'
                version.error_message = ''
                version.save(update_fields=['ref', 'upstream_commit', 'status', 'error_message'])
            _log_external(ext, 'info', f"Recorded version {version.commit_hash[:12]}")

        _log_external(ext, 'info', "Pull complete - publishing to repository")

        # Immediately publish to target repo
        publish_external_ref_task(external_ref_id)

    except Exception as e:
        logger.error(f"pull_external_ref_task failed for {external_ref_id}: {e}")
        try:
            ext.status = 'failed'
            ext.error_message = str(e)
            ext.save()
            _log_external(ext, 'error', f"Pull failed: {e}")
        except Exception:
            pass


@shared_task
def publish_external_ref_task(external_ref_id):
    """
    Publish an already-pulled ExternalRef from build-repo into the target repository.
    """
    from apps.flatpak.models import ExternalRef, ExternalRefVersion
    from apps.flatpak.utils.ostree import update_repo_metadata, temp_gpg_homedir

    try:
        ext = ExternalRef.objects.select_related('repository').get(pk=external_ref_id)
    except ExternalRef.DoesNotExist:
        logger.error(f"ExternalRef {external_ref_id} not found")
        return

    version = None
    try:
        ext.status = 'publishing'
        ext.save()

        if ext.commit_hash:
            version = (
                ExternalRefVersion.objects
                .filter(external_ref=ext, commit_hash=ext.commit_hash)
                .order_by('-id')
                .first()
            )
            if version is None:
                version = ExternalRefVersion.objects.create(
                    external_ref=ext,
                    ref=ext.ref,
                    commit_hash=ext.commit_hash,
                    upstream_commit=ext.upstream_commit or '',
                    status='pulled',
                )

        build_repo_path = os.path.join(settings.REPOS_BASE_PATH, 'build-repo')
        target_repo_path = ext.repository.repo_path

        if not os.path.exists(os.path.join(target_repo_path, 'config')):
            raise FileNotFoundError(f"Target repository {ext.repository.name} not found at {target_repo_path}")

        _log_external(ext, 'info', f"Publishing {ext.ref} to {ext.repository.name}")

        # Try canonical ref first, then remote-namespaced ref, then commit hash.
        source_candidates = [ext.ref]
        if ext.commit_hash:
            source_candidates.append(ext.commit_hash)

        pull_result = None
        used_source = None
        pull_errors = []
        for source in source_candidates:
            pull_result = subprocess.run(
                ['ostree', 'pull-local', f'--repo={target_repo_path}', build_repo_path, source],
                capture_output=True, text=True, timeout=600
            )
            if pull_result.returncode == 0:
                used_source = source
                break
            pull_errors.append(f"{source}: {pull_result.stderr.strip() or pull_result.stdout.strip()}")

        if used_source is None:
            raise RuntimeError(f"ostree pull-local failed: {' | '.join(pull_errors)}")

        if used_source != ext.ref and ext.commit_hash:
            # Ensure target repo always exports the expected branch name.
            create_target_ref = subprocess.run(
                ['ostree', 'refs', f'--repo={target_repo_path}', '--force', f'--create={ext.ref}', ext.commit_hash],
                capture_output=True, text=True
            )
            if create_target_ref.returncode != 0:
                raise RuntimeError(
                    f"Copied via {used_source}, but could not create target ref {ext.ref}: "
                    f"{create_target_ref.stderr.strip() or create_target_ref.stdout.strip()}"
                )
            else:
                _log_external(ext, 'info',
                              f"Copied via {used_source} and created target ref {ext.ref}")
        elif used_source != ext.ref:
            _log_external(ext, 'info', f"Copied via source {used_source}")

        _log_external(ext, 'info', "Ref copied to repository — updating metadata")

        gpg_key = ext.repository.gpg_key

        # Explicitly sign the imported commit in the target repo with the
        # repository's key before metadata refresh. This guarantees that each
        # imported external artifact is signed even if metadata generation later
        # downgrades to a warning/fallback path.
        target_commit = ''
        target_commit_result = subprocess.run(
            ['ostree', 'rev-parse', f'--repo={target_repo_path}', ext.ref],
            capture_output=True, text=True
        )
        if target_commit_result.returncode == 0:
            target_commit = target_commit_result.stdout.strip()
        elif ext.commit_hash:
            target_commit = ext.commit_hash

        if gpg_key and target_commit:
            with temp_gpg_homedir(gpg_key) as homedir:
                sign_result = subprocess.run(
                    ['ostree', f'--repo={target_repo_path}', 'gpg-sign',
                     f'--gpg-homedir={homedir}', target_commit, gpg_key.key_id],
                    capture_output=True, text=True
                )
            if sign_result.returncode != 0:
                raise RuntimeError(
                    "Imported commit signing failed: "
                    f"{sign_result.stderr.strip() or sign_result.stdout.strip()}"
                )
            _log_external(ext, 'info',
                          f"Signed imported commit {target_commit[:12]} with key {gpg_key.key_id}")
        elif gpg_key and not target_commit:
            raise RuntimeError(
                "Imported ref copied, but could not resolve target commit for GPG signing"
            )

        # Hard verification: published ref must be resolvable by name in target repo.
        verify_ref = subprocess.run(
            ['ostree', 'rev-parse', f'--repo={target_repo_path}', ext.ref],
            capture_output=True, text=True
        )
        if verify_ref.returncode != 0 or not (verify_ref.stdout or '').strip():
            raise RuntimeError(
                f"Published ref missing from target repo after copy: {ext.ref} — "
                f"{verify_ref.stderr.strip() or verify_ref.stdout.strip()}"
            )

        # External dependency pulls can happen frequently; regenerating static
        # deltas on every import is expensive and not required for correctness.
        # Keep the repo metadata + signatures fresh, but skip delta rebuilds.
        meta_result = update_repo_metadata(target_repo_path, gpg_key, generate_deltas=False)
        if not meta_result['success']:
            raise RuntimeError(
                f"Metadata update failed: {meta_result.get('message', '')} "
                f"{meta_result.get('detail', '') or meta_result.get('error', '')}"
            )

        ext.status = 'published'
        ext.save()
        if version is not None:
            version.status = 'published'
            version.source_published_at = timezone.now()
            version.error_message = ''
            version.save(update_fields=['status', 'source_published_at', 'error_message'])
        _log_external(ext, 'info', "Published successfully")

    except Exception as e:
        logger.error(f"publish_external_ref_task failed for {external_ref_id}: {e}")
        try:
            ext.status = 'failed'
            ext.error_message = str(e)
            ext.save()
            if version is not None:
                version.status = 'failed'
                version.error_message = str(e)
                version.save(update_fields=['status', 'error_message'])
            _log_external(ext, 'error', f"Publish failed: {e}")
        except Exception:
            pass


@shared_task
def check_external_ref_updates():
    """
    Periodic task: query each published/pulled ExternalRef's remote for its
    current commit hash and flag update_available when it differs from the
    last-pulled commit_hash.  Interval is read from SiteConfig.
    """
    from apps.flatpak.models import ExternalRef, SiteConfig

    config = SiteConfig.get_solo()
    interval_hours = config.external_ref_check_interval_hours

    # Sync the beat schedule
    try:
        from django_celery_beat.models import PeriodicTask, IntervalSchedule
        if interval_hours > 0:
            schedule, _ = IntervalSchedule.objects.get_or_create(
                every=interval_hours,
                period=IntervalSchedule.HOURS,
            )
            PeriodicTask.objects.filter(name='check-external-ref-updates').update(
                interval=schedule, enabled=True
            )
        else:
            PeriodicTask.objects.filter(name='check-external-ref-updates').update(enabled=False)
            logger.info('External ref update check is disabled (interval=0)')
            return 'External ref update check disabled'
    except Exception as e:
        logger.warning(f'Failed to sync external ref check schedule: {e}')

    if interval_hours == 0:
        return 'Disabled'

    # Check all refs that have a remote and are in a terminal state
    refs = ExternalRef.objects.select_related('remote').filter(
        remote__isnull=False,
        status__in=['published', 'pulled', 'failed'],
    )

    checked = 0
    updates_found = 0
    now = timezone.now()

    for ext in refs:
        try:
            upstream = _get_flatpak_remote_commit(ext.remote.name, ext.ref)
            if not upstream:
                continue

            changed = False
            if ext.upstream_commit != upstream:
                ext.upstream_commit = upstream
                changed = True

            # update_available: upstream differs from what was last pulled
            new_update_available = bool(upstream and upstream != ext.commit_hash)
            if ext.update_available != new_update_available:
                ext.update_available = new_update_available
                changed = True

            ext.upstream_checked_at = now
            if changed or True:  # always save the checked_at timestamp
                ext.save(update_fields=['upstream_commit', 'upstream_checked_at', 'update_available'])

            checked += 1
            if ext.update_available:
                updates_found += 1
                logger.info(
                    f"External ref {ext.ref}: update available "
                    f"(local={ext.commit_hash[:12] if ext.commit_hash else 'none'!r}, "
                    f"upstream={upstream[:12]})"
                )
        except Exception as e:
            logger.warning(f"check_external_ref_updates: error checking {ext.ref}: {e}")

    return f"Checked {checked} external refs; {updates_found} update(s) available"


@shared_task
def evaluate_dependency_staleness():
    """
    Periodic task: for every published Package that has build dependency
    snapshots (BuildExternalRef records), compare the upstream_commit currently
    on each ExternalRef with the upstream_commit_at_build value recorded when
    the package last built.  Sets/clears Package.deps_need_rebuild accordingly.
    Interval is read from SiteConfig.dependency_ref_check_interval_hours.
    """
    from apps.flatpak.models import Package, BuildExternalRef, ExternalRef, SiteConfig

    config = SiteConfig.get_solo()
    interval_hours = config.dependency_ref_check_interval_hours

    # Sync beat schedule
    try:
        from django_celery_beat.models import PeriodicTask, IntervalSchedule
        if interval_hours > 0:
            schedule, _ = IntervalSchedule.objects.get_or_create(
                every=interval_hours,
                period=IntervalSchedule.HOURS,
            )
            PeriodicTask.objects.filter(name='evaluate-dependency-staleness').update(
                interval=schedule, enabled=True
            )
        else:
            PeriodicTask.objects.filter(name='evaluate-dependency-staleness').update(enabled=False)
            return 'Dependency staleness evaluation disabled (interval=0)'
    except Exception as e:
        logger.warning(f'Failed to sync dependency staleness schedule: {e}')

    if interval_hours == 0:
        return 'Disabled'

    # Build a lookup: ref -> current upstream_commit for all known ExternalRefs
    upstream_by_ref = {
        ext.ref: ext.upstream_commit
        for ext in ExternalRef.objects.exclude(upstream_commit='').only('ref', 'upstream_commit')
    }

    packages = Package.objects.filter(
        status__in=['published', 'failed'],
    ).prefetch_related('builds__external_ref_snapshots')

    updated = stale = 0
    for package in packages:
        # Find the most recent build that has snapshot data
        latest_build = None
        for b in package.builds.all():  # already ordered -build_number
            if b.external_ref_snapshots.exists():
                latest_build = b
                break

        if not latest_build:
            # No snapshot data yet — skip (will be populated on next build)
            continue

        snapshots = latest_build.external_ref_snapshots.all()
        needs_rebuild = any(
            upstream_by_ref.get(snap.ref, snap.upstream_commit_at_build)
            != snap.upstream_commit_at_build
            for snap in snapshots
        )

        if package.deps_need_rebuild != needs_rebuild:
            package.deps_need_rebuild = needs_rebuild
            package.save(update_fields=['deps_need_rebuild'])
            updated += 1

        if needs_rebuild:
            stale += 1

    return f"Evaluated {packages.count()} packages; {stale} need rebuild, {updated} status change(s)"


def _get_installed_flatpak_commit(ref_str, installation_type=None):
    """
    Query the locally installed commit for a dep ref like
    'org.freedesktop.Sdk/x86_64/25.08'.  Returns a 64-char hex string or ''.

    Tries the package's own installation scope first, then system and user.
    """
    scopes = []
    if installation_type:
        scopes.append(f'--{installation_type}')
    for s in ('--system', '--user', ''):
        if s not in scopes:
            scopes.append(s)

    for scope in scopes:
        cmd = ['flatpak', 'info', '--show-commit']
        if scope:
            cmd.append(scope)
        cmd.append(ref_str)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if result.returncode == 0:
                value = result.stdout.strip()
                if re.fullmatch(r'[0-9a-f]{64}', value):
                    return value
        except Exception:
            pass
    return ''


def _snapshot_build_external_refs(build, package):
    """
    Called at build time.  Creates BuildExternalRef records for every
    dependency listed in package.dependencies.

    Priority:
    1. If the dep matches a tracked ExternalRef, use that object and its
       upstream_commit as the baseline.
    2. For deps that are only installed locally (e.g. org.freedesktop.Sdk
       pulled via flatpak but not tracked as an ExternalRef), fall back to
       `flatpak info --show-commit` to capture the installed commit hash.
       These records have external_ref=None so they show the commit but
       can't track future upstream changes until an ExternalRef is added.
    """
    from apps.flatpak.models import BuildExternalRef, ExternalRef

    deps = package.dependencies
    if not deps:
        return

    # Collect the full OSTree ref strings for all deps (name/arch/branch)
    dep_refs = []
    for key in ('sdk_full', 'runtime_full', 'base_full'):
        val = deps.get(key)
        if val:
            dep_refs.append(val)
    for ext_entry in deps.get('sdk_extensions', []):
        full = ext_entry.get('full')
        if full:
            dep_refs.append(full)

    if not dep_refs:
        return

    # Match on the last 3 path components (name/arch/branch), ignoring any
    # leading "runtime"/"app"/"sdk" segment present in ExternalRef.ref values.
    def _ref_tail(ref_str):
        parts = ref_str.split('/')
        return '/'.join(parts[-3:]) if len(parts) >= 3 else ref_str

    dep_tails = {_ref_tail(r): r for r in dep_refs}
    matched_tails = set()

    all_ext = ExternalRef.objects.only('ref', 'upstream_commit')
    for ext in all_ext:
        tail = _ref_tail(ext.ref)
        if tail not in dep_tails:
            continue
        matched_tails.add(tail)
        BuildExternalRef.objects.update_or_create(
            build=build,
            ref=ext.ref,
            defaults={
                'external_ref': ext,
                # Prefer upstream_commit (set by periodic check), but fall back
                # to commit_hash (the last pulled/published commit, always set).
                'upstream_commit_at_build': ext.upstream_commit or ext.commit_hash,
            }
        )

    # For deps with no matching ExternalRef, query the locally installed commit
    installation_type = getattr(package, 'installation_type', None)
    for tail, full_ref in dep_tails.items():
        if tail in matched_tails:
            continue
        commit = _get_installed_flatpak_commit(full_ref, installation_type)
        BuildExternalRef.objects.update_or_create(
            build=build,
            ref=full_ref,
            defaults={
                'external_ref': None,
                'upstream_commit_at_build': commit,
            }
        )
        logger.info(
            f"Snapshotted untracked dep {full_ref} for build #{build.build_number}"
            f" (installed commit: {commit[:12] if commit else 'unknown'})"
        )

    logger.info(
        f"Snapshotted {len(dep_tails)} dep ref(s) for {package.package_name}"
        f" build #{build.build_number}"
        f" ({len(matched_tails)} via ExternalRef, {len(dep_tails) - len(matched_tails)} via flatpak info)"
    )


def log_build(build, level, message):
    """Helper to create build log entries and broadcast via WebSocket."""
    from apps.flatpak.models import BuildLog

    # --- Sanitise raw terminal output before storing ---
    # 1. Strip ANSI/VT100 colour-escape sequences (e.g. \033[32m) — they are
    #    invisible in HTML and appear as garbage characters in the log view.
    message = _ANSI_ESC_RE.sub('', message)
    # 2. Handle carriage-return overwrite sequences used by curl/wget progress
    #    bars: \r moves the cursor to the start of the line in a real terminal,
    #    overwriting previous text. Simulate that by keeping only the text after
    #    the last \r so the stored line matches what a terminal would show.
    if '\r' in message:
        message = message.split('\r')[-1]
    message = message.strip()
    if not message:
        return

    log = BuildLog.objects.create(
        build=build,
        message=message,
        level=level
    )
    logger.log(
        getattr(logging, level.upper(), logging.INFO),
        f"[Build #{build.build_number}] {message}"
    )
    # Log lines are fetched by the client via 2-second polling (updateLogs).
    # We do NOT broadcast individual log lines over WebSocket: during streaming
    # builds, hundreds of rapid group_send calls overwhelm Channels and cause
    # WebSocket disconnections.  Status changes are broadcast separately via
    # send_build_status_update() which is called at coarse-grained state transitions.


def detect_and_install_dependencies(package, error_message, build=None):
    """Detect missing Flatpak SDK/runtime from error and install it."""
    import re
    
    # Pattern: "org.gnome.Sdk/x86_64/3.30 not installed"
    # Or: "Unable to find sdk org.gnome.Sdk version 3.30"
    patterns = [
        r'(org\.[\\w.]+)/(x86_64|aarch64|arm)/(\\S+) not installed',
        r'Unable to find (sdk|runtime) (org\\.[\\w.]+) version (\\S+)'
    ]
    
    dependencies = []
    
    for pattern in patterns:
        matches = re.findall(pattern, error_message, re.IGNORECASE)
        for match in matches:
            if len(match) == 3:
                if '/' in error_message and 'not installed' in error_message:
                    # First pattern: full ref
                    ref = f"{match[0]}/{match[1]}/{match[2]}"
                    dependencies.append(ref)
                else:
                    # Second pattern: name and version
                    name = match[1]
                    version = match[2]
                    arch = package.arch or 'x86_64'
                    ref = f"{name}/{arch}/{version}"
                    dependencies.append(ref)
    
    if not dependencies:
        log_build(build, 'warning', "Could not detect missing dependencies from error message")
        return False
    
    log_build(build, 'info', f"Detected missing dependencies: {', '.join(dependencies)}")
    
    # Install each dependency
    for dep in dependencies:
        log_build(build, 'info', f"Installing dependency: {dep}")
        try:
            from apps.flatpak.models import FlatpakRemote as _FR
            _remotes = list(_FR.objects.filter(is_active=True))
            _remote = _remotes[0].name if _remotes else 'flathub'
            install_result = subprocess.run(
                ['flatpak', 'install', '-y', '--noninteractive', _remote, dep],
                capture_output=True,
                text=True,
                timeout=600
            )
            
            if install_result.returncode == 0:
                log_build(build, 'info', f"Successfully installed {dep}")
            else:
                # Dependency might already be installed
                if 'already installed' in install_result.stderr.lower():
                    log_build(build, 'info', f"{dep} is already installed")
                else:
                    log_build(build, 'error', f"Failed to install {dep}: {install_result.stderr}")
                    return False
        except subprocess.TimeoutExpired:
            log_build(build, 'error', f"Timeout installing {dep}")
            return False
        except Exception as e:
            log_build(build, 'error', f"Error installing {dep}: {str(e)}")
            return False
    
    return True


def _extract_version_from_manifest(package_id, manifest_file):
    """Detect an application's version from a flatpak manifest file.

    Mirrors the version-detection logic in ``parse_manifest_dependencies`` but
    has **no side effects** — it never writes to the database or emits build
    log messages.  Returns the version string, or ``None`` if no version could
    be determined.
    """
    import re
    try:
        import yaml
        import json as _json

        with open(manifest_file, 'r') as fh:
            if manifest_file.endswith(('.yml', '.yaml')):
                manifest = yaml.safe_load(fh)
            else:
                manifest = _json.load(fh)

        if not manifest:
            return None

        version = None

        # 1. Explicit top-level version fields
        if 'version' in manifest:
            version = str(manifest['version'])
        elif 'app-version' in manifest:
            version = str(manifest['app-version'])
        elif 'build-options' in manifest and 'app-version' in manifest.get('build-options', {}):
            version = str(manifest['build-options']['app-version'])

        # 2. Module scan (same logic as parse_manifest_dependencies)
        if not version and 'modules' in manifest:
            # Build candidate names from all meaningful package ID segments.
            # e.g. com.jgraph.drawio.desktop → ['jgraph', 'drawio', 'desktop']
            # This handles IDs where the last segment is generic ('desktop',
            # 'app', etc.) and the real app name appears earlier.
            _skip_parts = {'com', 'org', 'net', 'io', 'de', 'app', 'apps',
                           'github', 'gitlab', 'codeberg'}
            _id_parts = package_id.split('.') if package_id else []
            app_name_candidates = [p.lower() for p in _id_parts
                                   if p.lower() not in _skip_parts and len(p) > 1]
            app_name = app_name_candidates[-1] if app_name_candidates else None
            for module in reversed(manifest['modules']):
                if isinstance(module, str):
                    continue
                module_name = module.get('name', '').lower()
                is_likely_match = False
                if app_name_candidates:
                    is_likely_match = any(
                        cand in module_name or module_name in cand
                        or module_name.replace('-', '') == cand
                        or module_name.replace('_', '') == cand
                        for cand in app_name_candidates
                    )
                if not is_likely_match:
                    continue
                # Collect the best version candidate per source type, then pick
                # by priority: git tag > archive URL > extra-data > file URL.
                # This prevents tool downloads (e.g. a pnpm file source) from
                # shadowing the real application archive.
                candidates = {}
                for source in module.get('sources', []):
                    if isinstance(source, str):
                        continue
                    source_type = source.get('type', '')
                    if source_type == 'git' and 'git' not in candidates:
                        tag = source.get('tag', '')
                        if tag:
                            candidates['git'] = tag.lstrip('v')
                        else:
                            branch = source.get('branch', '')
                            if branch and branch[0].isdigit():
                                candidates['git'] = branch
                    elif source_type == 'archive' and 'archive' not in candidates:
                        url = source.get('url', '')
                        for pattern in [
                            r'[-_/]v?(\d+\.\d+(?:\.\d+)?(?:\.\d+)?)',
                            r'/(\d+\.\d+(?:\.\d+)?(?:\.\d+)?)/',
                            r'/(\d{4}-\d{2})(?:/|$)',  # YYYY-MM style (e.g. Eclipse)
                        ]:
                            m = re.search(pattern, url)
                            if m:
                                candidates['archive'] = m.group(1)
                                break
                    elif source_type == 'extra-data' and 'extra-data' not in candidates:
                        if source.get('version'):
                            candidates['extra-data'] = str(source['version'])
                        else:
                            url = source.get('url', '')
                            for pattern in [
                                r'[-_/]v?(\d+\.\d+(?:\.\d+)?(?:\.\d+)?)',
                                r'/(\d+\.\d+(?:\.\d+)?(?:\.\d+)?)/',
                            ]:
                                m = re.search(pattern, url)
                                if m:
                                    candidates['extra-data'] = m.group(1)
                                    break
                    elif source_type == 'file' and 'file' not in candidates:
                        for _candidate in [source.get('url', ''), source.get('path', '')]:
                            if not _candidate:
                                continue
                            m = re.search(r'[-_/]v?(\d+\.\d+(?:\.\d+)?(?:\.\d+)?)', _candidate)
                            if m:
                                candidates['file'] = m.group(1)
                                break
                # Pick highest-priority candidate
                for ptype in ('git', 'archive', 'extra-data', 'file'):
                    if ptype in candidates:
                        version = candidates[ptype]
                        break
                if version:
                    break

        if not version:
            return None

        # Strip leading word prefix (e.g. "RELEASE.2.5.0" → "2.5.0")
        _m = re.match(r'^[a-zA-Z][a-zA-Z0-9_.+-]*?(\d)', version)
        if _m:
            version = version[version.index(_m.group(1)):]
        # Normalise underscore-separated versions (e.g. "1_4_3" → "1.4.3")
        version = re.sub(r'(\d)_(\d)', r'\1.\2', version)
        return version
    except Exception:
        return None


def _get_freedesktop_sdk_version(sdk, arch, sdk_version, scope_flag, build=None):
    """Return the org.freedesktop.Sdk version that *sdk* is based on.

    For SDKs like org.kde.Sdk that are layered on top of org.freedesktop.Sdk,
    extensions belonging to org.freedesktop.Sdk.Extension.* must be installed
    with the *freedesktop* version, not the KDE/GNOME SDK version.

    Strategy:
    1. Query ``flatpak info --show-metadata`` for the installed SDK and look for
       a line such as ``sdk=org.freedesktop.Sdk//24.08``.
    2. If the SDK is not yet installed (metadata unavailable), fall back to
       ``flatpak list --runtime`` and return the highest installed
       org.freedesktop.Sdk version.
    """
    import re as _re

    def _parse_metadata(output):
        for line in output.splitlines():
            line = line.strip()
            for prefix in (
                'sdk=org.freedesktop.Sdk//',
                'runtime=org.freedesktop.Platform//',
                'base=org.freedesktop.Sdk//',
            ):
                if line.startswith(prefix):
                    ver = line.split('//')[-1].strip()
                    if ver:
                        return ver
        return None

    # Try to query from the installed SDK metadata
    try:
        result = subprocess.run(
            ['flatpak', 'info', '--show-metadata', scope_flag,
             f"{sdk}/{arch}/{sdk_version}"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            ver = _parse_metadata(result.stdout)
            if ver:
                if build:
                    log_build(build, 'info',
                              f"Resolved freedesktop SDK version {ver} from {sdk}/{sdk_version} metadata")
                return ver
    except Exception as e:
        if build:
            log_build(build, 'warning', f"Could not query {sdk} metadata: {e}")

    # Fallback: find installed org.freedesktop.Sdk runtimes
    try:
        result = subprocess.run(
            ['flatpak', 'list', '--runtime', '--columns=ref', scope_flag],
            capture_output=True, text=True, timeout=30
        )
        versions = []
        for line in result.stdout.splitlines():
            m = _re.match(r'org\.freedesktop\.Sdk/[^/]+/(.+)', line.strip())
            if m:
                versions.append(m.group(1))
        if versions:
            # Return the lexicographically largest (most recent) version
            ver = sorted(versions)[-1]
            if build:
                log_build(build, 'info',
                          f"Freedesktop SDK version {ver} found via flatpak list (fallback)")
            return ver
    except Exception as e:
        if build:
            log_build(build, 'warning', f"Could not list runtimes for freedesktop SDK version: {e}")

    return None


def parse_manifest_dependencies(package, manifest_file, build=None):
    """Parse flatpak manifest file to extract SDK and runtime dependencies."""
    import yaml
    import json
    
    try:
        with open(manifest_file, 'r') as f:
            if manifest_file.endswith(('.yml', '.yaml')):
                manifest = yaml.safe_load(f)
            else:
                manifest = json.load(f)
        
        if not manifest:
            log_build(build, 'warning', "Manifest file is empty")
            return {}
        
        dependencies = {}
        
        # Extract version from various possible locations
        version = None
        
        # 1. Check top-level version fields
        if 'version' in manifest:
            version = str(manifest['version'])
        elif 'app-version' in manifest:
            version = str(manifest['app-version'])
        elif 'build-options' in manifest and 'app-version' in manifest['build-options']:
            version = str(manifest['build-options']['app-version'])
        
        # 2. If not found, look for version in modules (common pattern for main app)
        if not version and 'modules' in manifest:
            import re
            # Build candidate names from all meaningful package ID segments.
            # e.g. com.jgraph.drawio.desktop → ['jgraph', 'drawio', 'desktop']
            _skip_parts = {'com', 'org', 'net', 'io', 'de', 'app', 'apps',
                           'github', 'gitlab', 'codeberg'}
            _id_parts = package.package_id.split('.') if package.package_id else []
            app_name_candidates = [p.lower() for p in _id_parts
                                   if p.lower() not in _skip_parts and len(p) > 1]
            app_name = app_name_candidates[-1] if app_name_candidates else None

            # Try to find matching modules (check in reverse - last modules are usually the app)
            for module in reversed(manifest['modules']):  # Start from last module
                # Skip string modules (file references like "shared-modules/libsecret/libsecret.json")
                if isinstance(module, str):
                    continue

                module_name = module.get('name', '').lower()

                # Check if this is likely the main app module (flexible matching)
                is_likely_match = False
                if app_name_candidates:
                    is_likely_match = any(
                        cand in module_name or module_name in cand
                        or module_name.replace('-', '') == cand
                        or module_name.replace('_', '') == cand
                        for cand in app_name_candidates
                    )
                
                if is_likely_match:
                    log_build(build, 'info', f"Checking module '{module.get('name')}' for version...")
                    # Collect best candidate per source type, then pick by
                    # priority: git tag > archive URL > extra-data > file URL.
                    # This prevents tool helper files (e.g. pnpm download) from
                    # shadowing the actual application archive.
                    if 'sources' in module:
                        candidates = {}
                        for source in module['sources']:
                            if isinstance(source, str):
                                continue
                            source_type = source.get('type', '')
                            if source_type == 'git' and 'git' not in candidates:
                                tag = source.get('tag', '')
                                if tag:
                                    candidates['git'] = tag.lstrip('v')
                                    log_build(build, 'info', f"Found version in git tag: {tag}")
                                else:
                                    branch = source.get('branch', '')
                                    if branch and branch[0].isdigit():
                                        candidates['git'] = branch
                                        log_build(build, 'info', f"Found version in git branch: {branch}")
                            elif source_type == 'archive' and 'archive' not in candidates:
                                url = source.get('url', '')
                                for pattern in [
                                    r'[-_/]v?(\d+\.\d+(?:\.\d+)?(?:\.\d+)?)',
                                    r'/(\d+\.\d+(?:\.\d+)?(?:\.\d+)?)/',
                                    r'/(\d{4}-\d{2})(?:/|$)',  # YYYY-MM style (e.g. Eclipse)
                                ]:
                                    match = re.search(pattern, url)
                                    if match:
                                        candidates['archive'] = match.group(1)
                                        log_build(build, 'info', f"Extracted version from archive URL: {match.group(1)}")
                                        break
                            elif source_type == 'extra-data' and 'extra-data' not in candidates:
                                if source.get('version'):
                                    candidates['extra-data'] = str(source['version'])
                                    log_build(build, 'info', f"Found version in extra-data version field: {source['version']}")
                                else:
                                    url = source.get('url', '')
                                    for pattern in [
                                        r'[-_/]v?(\d+\.\d+(?:\.\d+)?(?:\.\d+)?)',
                                        r'/(\d+\.\d+(?:\.\d+)?(?:\.\d+)?)/',
                                    ]:
                                        match = re.search(pattern, url)
                                        if match:
                                            candidates['extra-data'] = match.group(1)
                                            log_build(build, 'info', f"Extracted version from extra-data URL: {match.group(1)}")
                                            break
                            elif source_type == 'file' and 'file' not in candidates:
                                for _candidate in [source.get('url', ''), source.get('path', '')]:
                                    if not _candidate:
                                        continue
                                    match = re.search(r'[-_/]v?(\d+\.\d+(?:\.\d+)?(?:\.\d+)?)', _candidate)
                                    if match:
                                        candidates['file'] = match.group(1)
                                        log_build(build, 'info', f"Extracted version from file source: {match.group(1)}")
                                        break
                        for ptype in ('git', 'archive', 'extra-data', 'file'):
                            if ptype in candidates:
                                version = candidates[ptype]
                                break
                    if version:
                        break
        
        # If version found, save it to both package and build
        if version:
            # Strip leading word prefix (e.g. "RELEASE.2.5.0" → "2.5.0", "version-1.0" → "1.0")
            _m = re.match(r'^[a-zA-Z][a-zA-Z0-9_.+-]*?(\d)', version)
            if _m:
                version = version[version.index(_m.group(1)):]
            # Normalise underscore-separated versions (e.g. "1_4_3" → "1.4.3")
            version = re.sub(r'(\d)_(\d)', r'\1.\2', version)
            package.version = version
            package.save(update_fields=['version'])
            build.version = version
            build.save(update_fields=['version'])
            log_build(build, 'info', f"Detected application version: {version}")
        
        # Extract SDK
        if 'sdk' in manifest:
            sdk = manifest['sdk']
            sdk_version = manifest.get('runtime-version', manifest.get('sdk-version', ''))
            dependencies['sdk'] = sdk
            dependencies['sdk_version'] = sdk_version
            dependencies['sdk_full'] = f"{sdk}/{package.arch or 'x86_64'}/{sdk_version}"
        
        # Extract Runtime
        if 'runtime' in manifest:
            runtime = manifest['runtime']
            runtime_version = manifest.get('runtime-version', '')
            dependencies['runtime'] = runtime
            dependencies['runtime_version'] = runtime_version
            dependencies['runtime_full'] = f"{runtime}/{package.arch or 'x86_64'}/{runtime_version}"
        
        # Extract base app if present
        if 'base' in manifest:
            base = manifest['base']
            base_version = manifest.get('base-version', runtime_version)
            dependencies['base'] = base
            dependencies['base_version'] = base_version
            dependencies['base_full'] = f"{base}/{package.arch or 'x86_64'}/{base_version}"
        
        # Extract SDK extensions if present
        if 'sdk-extensions' in manifest:
            sdk_extensions = manifest['sdk-extensions']
            dependencies['sdk_extensions'] = []
            sdk_version = dependencies.get('sdk_version', '')
            sdk_name = dependencies.get('sdk', '')
            arch = package.arch or 'x86_64'

            # Determine install scope for metadata queries
            _scope = (
                f"--{package.installation_type}"
                if hasattr(package, 'installation_type') and package.installation_type
                else '--system'
            )
            # Cache the freedesktop version so we only query once per manifest
            _fd_version_cache = {}

            for extension in sdk_extensions:
                ext_version = sdk_version

                # org.freedesktop.Sdk.Extension.* extensions are versioned by the
                # freedesktop SDK, not by the parent SDK (KDE, GNOME, etc.)
                if (
                    extension.startswith('org.freedesktop.Sdk.Extension.')
                    and not sdk_name.startswith('org.freedesktop.Sdk')
                ):
                    cache_key = f"{sdk_name}/{arch}/{sdk_version}"
                    if cache_key not in _fd_version_cache:
                        _fd_version_cache[cache_key] = _get_freedesktop_sdk_version(
                            sdk_name, arch, sdk_version, _scope, build
                        )
                    fd_ver = _fd_version_cache[cache_key]
                    if fd_ver:
                        log_build(build, 'info',
                                  f"Extension {extension}: using freedesktop SDK version "
                                  f"{fd_ver} (instead of {sdk_version})")
                        ext_version = fd_ver
                    else:
                        log_build(build, 'warning',
                                  f"Could not resolve freedesktop SDK version for {extension}; "
                                  f"falling back to {sdk_version}")

                extension_full = f"{extension}/{arch}/{ext_version}"
                dependencies['sdk_extensions'].append({
                    'name': extension,
                    'full': extension_full
                })

            log_build(build, 'info', f"Found SDK extensions: {[ext['name'] for ext in dependencies['sdk_extensions']]}")
        
        log_build(build, 'info', f"Parsed manifest dependencies: {json.dumps(dependencies, indent=2)}")
        return dependencies
        
    except Exception as e:
        log_build(build, 'error', f"Failed to parse manifest: {str(e)}")
        return {}


def ensure_flatpak_remote(remote_name, remote_url, scope_flag, build=None):
    """Add the Flatpak remote if it is not already registered in the given scope."""
    check = subprocess.run(
        ['flatpak', 'remotes', scope_flag],
        capture_output=True, text=True, timeout=30
    )
    if remote_name in check.stdout:
        return  # already present
    if build:
        log_build(build, 'info', f"Adding Flatpak remote '{remote_name}' from {remote_url} ({scope_flag.lstrip('-')})...")
    try:
        result = subprocess.run(
            ['flatpak', 'remote-add', '--if-not-exists', scope_flag, remote_name, remote_url],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode != 0:
            if build:
                log_build(build, 'warning', f"Could not add remote '{remote_name}': {result.stderr.strip()}")
        else:
            if build:
                log_build(build, 'info', f"✓ Remote '{remote_name}' added ({scope_flag.lstrip('-')})")
    except Exception as e:
        if build:
            log_build(build, 'warning', f"Error adding remote '{remote_name}': {e}")


def install_flatpak_dependencies(package, dependencies, build=None):
    """Install required Flatpak SDK and runtime dependencies."""
    refs_to_install = []
    
    # Collect all refs to install
    for key in ['sdk_full', 'runtime_full', 'base_full']:
        if key in dependencies:
            refs_to_install.append(dependencies[key])
    
    # Add SDK extensions
    if 'sdk_extensions' in dependencies:
        for extension in dependencies['sdk_extensions']:
            refs_to_install.append(extension['full'])
    
    if not refs_to_install:
        log_build(build, 'warning', "No dependencies found to install")
        return True
    
    # Determine installation scope from build settings
    install_scope = f"--{package.installation_type}" if hasattr(package, 'installation_type') and package.installation_type else '--system'
    scope_name = package.installation_type if hasattr(package, 'installation_type') and package.installation_type else 'system'

    # Load remote config and ensure all active remotes are registered
    from apps.flatpak.models import FlatpakRemote
    active_remotes = list(FlatpakRemote.objects.filter(is_active=True))
    if not active_remotes:
        # Fall back to flathub if nothing configured
        log_build(build, 'warning', "No active Flatpak remotes configured — falling back to flathub")
        active_remotes_info = [('flathub', 'https://dl.flathub.org/repo/flathub.flatpakrepo')]
    else:
        active_remotes_info = [(r.name, r.url) for r in active_remotes]

    for remote_name, remote_url in active_remotes_info:
        ensure_flatpak_remote(remote_name, remote_url, install_scope, build)

    log_build(build, 'info', f"Installing {len(refs_to_install)} dependencies to {scope_name} (remotes: {', '.join(n for n,_ in active_remotes_info)})...")
    
    for ref in refs_to_install:
        log_build(build, 'info', f"Checking/installing: {ref}")
        
        try:
            # Check if already installed in the target scope
            check_result = subprocess.run(
                ['flatpak', 'info', install_scope, ref],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if check_result.returncode == 0:
                log_build(build, 'info', f"✓ {ref} is already installed ({scope_name}), checking for updates...")
                update_result = subprocess.run(
                    ['flatpak', 'update', '-y', '--noninteractive', install_scope, ref],
                    capture_output=True,
                    text=True,
                    timeout=600
                )
                if update_result.returncode == 0:
                    if 'Nothing to do' in update_result.stdout or 'is up to date' in update_result.stdout:
                        log_build(build, 'info', f"  {ref} is up to date")
                    else:
                        log_build(build, 'info', f"  Updated {ref}")
                else:
                    log_build(build, 'warning', f"  Could not update {ref}: {update_result.stderr.strip()}")
                continue

            # If not in target scope, check the other scope
            other_scope = '--user' if scope_name == 'system' else '--system'
            other_scope_name = 'user' if scope_name == 'system' else 'system'
            check_other = subprocess.run(
                ['flatpak', 'info', other_scope, ref],
                capture_output=True,
                text=True,
                timeout=30
            )

            if check_other.returncode == 0:
                log_build(build, 'info', f"✓ {ref} is already installed in {other_scope_name}, checking for updates...")
                update_result = subprocess.run(
                    ['flatpak', 'update', '-y', '--noninteractive', other_scope, ref],
                    capture_output=True,
                    text=True,
                    timeout=600
                )
                if update_result.returncode == 0:
                    if 'Nothing to do' in update_result.stdout or 'is up to date' in update_result.stdout:
                        log_build(build, 'info', f"  {ref} is up to date")
                    else:
                        log_build(build, 'info', f"  Updated {ref}")
                else:
                    log_build(build, 'warning', f"  Could not update {ref}: {update_result.stderr.strip()}")
                continue
            
            # Install to the specified scope — try each active remote in order
            log_build(build, 'info', f"Installing {ref} to {scope_name}...")
            install_result = None
            for remote_name, _ in active_remotes_info:
                install_result = subprocess.run(
                    ['flatpak', 'install', '-y', install_scope, '--noninteractive', remote_name, ref],
                    capture_output=True,
                    text=True,
                    timeout=600
                )
                if install_result.returncode == 0:
                    break

            if install_result and install_result.returncode == 0:
                log_build(build, 'info', f"✓ Successfully installed {ref} to {scope_name}")
            else:
                error_msg = install_result.stderr.strip()
                if 'already installed' in error_msg.lower():
                    log_build(build, 'info', f"✓ {ref} is already installed")
                elif scope_name == 'system' and ('insufficient permissions' in error_msg.lower() or 'permission denied' in error_msg.lower()):
                    # Try installing to user space instead, using each remote
                    for remote_name, remote_url in active_remotes_info:
                        ensure_flatpak_remote(remote_name, remote_url, '--user', build)
                    log_build(build, 'warning', f"Cannot install to system (permission denied), trying user installation...")
                    user_install = None
                    for remote_name, _ in active_remotes_info:
                        user_install = subprocess.run(
                            ['flatpak', 'install', '-y', '--user', '--noninteractive', remote_name, ref],
                            capture_output=True,
                            text=True,
                            timeout=600
                        )
                        if user_install.returncode == 0:
                            break
                    if user_install and user_install.returncode == 0:
                        log_build(build, 'info', f"✓ Successfully installed {ref} to user")
                    else:
                        log_build(build, 'error', f"✗ Failed to install {ref} to user: {user_install.stderr.strip() if user_install else 'no result'}")
                        return False
                else:
                    log_build(build, 'error', f"✗ Failed to install {ref}: {error_msg}")
                    # Log additional details for debugging
                    if install_result.stdout.strip():
                        log_build(build, 'info', f"Install output: {install_result.stdout.strip()}")
                    return False
                    
        except subprocess.TimeoutExpired:
            log_build(build, 'error', f"✗ Timeout installing {ref}")
            return False
        except Exception as e:
            log_build(build, 'error', f"✗ Error installing {ref}: {str(e)}")
            return False
    
    log_build(build, 'info', "All dependencies installed successfully")
    return True


@shared_task
def check_pending_builds():
    """
    Periodic task that checks for pending builds and triggers them.
    This runs every minute via Celery Beat.
    """
    from apps.flatpak.models import Package, BuildStreamSource

    # Flatpak git-based builds
    pending_packages = Package.objects.filter(
        status='pending',
        git_repo_url__isnull=False
    ).exclude(git_repo_url='')

    count = pending_packages.count()
    if count > 0:
        logger.info(f"Found {count} pending flatpak git build(s), triggering...")
        for package in pending_packages:
            logger.info(f"Triggering build {package.build_number} - {package.package_id}")
            package_from_git_task.delay(package.id)

    # BuildStream builds
    pending_bst = BuildStreamSource.objects.filter(status='pending')
    bst_count = pending_bst.count()
    if bst_count > 0:
        logger.info(f"Found {bst_count} pending BuildStream build(s), triggering...")
        for source in pending_bst:
            logger.info(f"Triggering BST build {source.build_number} - {source.name}")
            buildstream_build_task.delay(source.id)

    total = count + bst_count
    return f"Checked pending builds: {total} triggered"


@shared_task
def cleanup_stale_builds():
    """
    Periodic task that detects and fails stale builds that are stuck in active states.
    This handles cases where builds were interrupted by service restarts or crashes.
    Interval and stale threshold are configurable via SiteConfig.
    """
    from apps.flatpak.models import Package, Build, SiteConfig
    from datetime import timedelta

    config = SiteConfig.get_solo()
    interval_seconds = config.stale_build_check_interval_seconds
    timeout_minutes = config.stale_build_timeout_minutes

    # Sync the beat schedule with the current config value
    try:
        from django_celery_beat.models import PeriodicTask, IntervalSchedule
        if interval_seconds > 0:
            schedule, _ = IntervalSchedule.objects.get_or_create(
                every=interval_seconds,
                period=IntervalSchedule.SECONDS,
            )
            PeriodicTask.objects.filter(name='cleanup-stale-builds').update(
                interval=schedule, enabled=True
            )
        else:
            PeriodicTask.objects.filter(name='cleanup-stale-builds').update(enabled=False)
            logger.info('Stale build check is disabled (interval=0)')
            return 'Stale build check disabled'
    except Exception as e:
        logger.warning(f'Failed to sync stale build check schedule: {e}')

    # Active states that should have log activity
    active_states = ['building', 'committing', 'publishing']
    stale_threshold = timezone.now() - timedelta(minutes=timeout_minutes)
    recent_activity_threshold = timezone.now() - timedelta(minutes=timeout_minutes)

    stale_packages = Package.objects.filter(
        status__in=active_states,
        started_at__lt=stale_threshold
    )

    count = 0
    for package in stale_packages:
        build = Build.objects.filter(
            package=package,
            build_number=package.build_number
        ).first()

        # Check for recent log activity
        has_recent_logs = False
        if build:
            has_recent_logs = build.logs.filter(
                timestamp__gte=recent_activity_threshold
            ).exists()

        if not has_recent_logs:
            stuck_status = package.status  # capture before overwrite
            logger.warning(
                f'Stale build detected: {package.package_id} (build #{package.build_number}) '
                f'stuck in \'{stuck_status}\' for >{timeout_minutes} min with no log activity'
            )

            package.status = 'failed'
            package.error_message = (
                f"Build was interrupted (stuck in '{stuck_status}' state with no log "
                f"activity for >{timeout_minutes} minutes). "
                f"Possibly caused by a service restart or crash."
            )
            package.save(update_fields=['status', 'error_message'])

            if build:
                build.status = 'failed'
                build.error_message = package.error_message
                build.completed_at = timezone.now()
                build.save(update_fields=['status', 'error_message', 'completed_at'])
                log_build(build, 'error', f'Build marked as failed: stuck in \'{stuck_status}\' state with no activity')

            send_build_status_update(package.id, 'failed', 'Build was interrupted and marked as failed')
            count += 1

    # --- BuildStream sources ---
    from apps.flatpak.models import BuildStreamSource
    stale_bst = BuildStreamSource.objects.filter(status__in=active_states)
    for source in stale_bst:
        build = Build.objects.filter(
            bst_source=source,
            build_number=source.build_number,
        ).first()
        if not build or build.started_at > stale_threshold:
            continue
        has_recent_logs = build.logs.filter(
            timestamp__gte=recent_activity_threshold
        ).exists()
        if not has_recent_logs:
            stuck_status = source.status
            logger.warning(
                f'Stale BST build detected: {source.name} (build #{source.build_number}) '
                f'stuck in \'{stuck_status}\' for >{timeout_minutes} min with no log activity'
            )
            error_msg = (
                f"Build was interrupted (stuck in '{stuck_status}' state with no log "
                f"activity for >{timeout_minutes} minutes). "
                f"Possibly caused by a service restart or crash."
            )
            source.status = 'failed'
            source.error_message = error_msg
            source.save(update_fields=['status', 'error_message'])

            build.status = 'failed'
            build.error_message = error_msg
            build.completed_at = timezone.now()
            build.save(update_fields=['status', 'error_message', 'completed_at'])
            log_build(build, 'error', f'Build marked as failed: stuck in \'{stuck_status}\' state with no activity')

            send_build_status_update(source.id, 'failed', 'Build was interrupted and marked as failed')
            count += 1

    if count > 0:
        logger.info(f'cleanup_stale_builds: marked {count} stuck build(s) as failed')

    return f'Checked stale builds: {count} failed'


@shared_task
def cleanup_failed_builds():
    """
    Periodic task that removes old failed builds per package,
    keeping only the N most recent ones as configured in SiteConfig.
    Runs hourly via Celery Beat.
    """
    from apps.flatpak.models import Package, Build, SiteConfig

    config = SiteConfig.get_solo()
    keep = config.failed_builds_to_keep

    if keep == 0:
        return "Cleanup skipped: keeping all failed builds"

    total_deleted = 0
    for package in Package.objects.all():
        failed_ids = list(
            Build.objects.filter(package=package, status='failed')
            .order_by('-build_number')
            .values_list('id', flat=True)
        )
        to_delete = failed_ids[keep:]
        if to_delete:
            deleted, _ = Build.objects.filter(id__in=to_delete).delete()
            total_deleted += deleted
            logger.info(
                f"Deleted {deleted} old failed build(s) for package {package.package_id}"
            )

    if total_deleted > 0:
        logger.info(f"cleanup_failed_builds: removed {total_deleted} build record(s)")

    return f"Cleaned up {total_deleted} old failed build(s)"


def remove_external_ref_from_repos(ext):
    """
    Delete the OSTree ref for an ExternalRef from build-repo and the target
    repository, then regenerate the repository summary.

    Called before the DB record is removed so the repo stays consistent.
    Non-fatal: logs warnings but does not raise so the delete can still proceed.
    """
    from apps.flatpak.utils.ostree import update_repo_metadata

    build_repo_path = os.path.join(settings.REPOS_BASE_PATH, 'build-repo')
    target_repo_path = ext.repository.repo_path
    ref = ext.ref

    for label, repo_path in (('build-repo', build_repo_path), (ext.repository.name, target_repo_path)):
        if not os.path.exists(os.path.join(repo_path, 'config')):
            continue
        # Check whether the ref actually exists before attempting deletion.
        check = subprocess.run(
            ['ostree', 'refs', f'--repo={repo_path}'],
            capture_output=True, text=True,
        )
        if check.returncode != 0 or ref not in check.stdout.splitlines():
            logger.info("remove_external_ref_from_repos: %s not in %s, skipping", ref, label)
            continue
        del_result = subprocess.run(
            ['ostree', 'refs', f'--repo={repo_path}', '--delete', ref],
            capture_output=True, text=True,
        )
        if del_result.returncode == 0:
            logger.info("remove_external_ref_from_repos: deleted %s from %s", ref, label)
        else:
            logger.warning(
                "remove_external_ref_from_repos: could not delete %s from %s: %s",
                ref, label, del_result.stderr.strip(),
            )

    # Regenerate summary + signatures for the target repo.
    if os.path.exists(os.path.join(target_repo_path, 'config')):
        try:
            update_repo_metadata(target_repo_path, ext.repository.gpg_key, generate_deltas=False)
        except Exception as exc:
            logger.warning("remove_external_ref_from_repos: summary update failed: %s", exc)


@shared_task
def prune_orphaned_refs_task(task_id, items):
    """
    Delete one or more orphaned OSTree refs in the background (Celery task).

    items: [{'repo': repo_name, 'ref': ref_name}, ...]

    On completion sends a 'task_update' message to the 'notifications' channel
    group so the browser can update without polling.
    """
    from collections import defaultdict
    from apps.flatpak.models import Repository
    from apps.flatpak.utils.ostree import update_repo_metadata as _update_repo_metadata
    from channels.layers import get_channel_layer
    from asgiref.sync import async_to_sync

    def _notify(status, message, deleted=None):
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)('notifications', {
            'type': 'task_update',
            'task_id': task_id,
            'status': status,
            'message': message,
            'deleted': deleted or [],
        })

    by_repo = defaultdict(list)
    for item in items:
        by_repo[item['repo']].append(item['ref'])

    deleted = []
    errors = []

    for repo_name, refs in by_repo.items():
        try:
            repo = Repository.objects.get(name=repo_name)
        except Repository.DoesNotExist:
            errors.append(f'{repo_name}: repository not found')
            continue

        repo_path = repo.repo_path
        if not os.path.exists(os.path.join(repo_path, 'config')):
            errors.append(f'{repo_name}: not found on disk')
            continue

        for ref in refs:
            del_result = subprocess.run(
                ['ostree', 'refs', f'--repo={repo_path}', '--delete', ref],
                capture_output=True, text=True,
            )
            if del_result.returncode == 0:
                deleted.append({'repo': repo_name, 'ref': ref})
                logger.info("Pruned orphaned ref %s from %s (task %s)", ref, repo_name, task_id)
            else:
                errors.append(
                    f'{repo_name}/{ref}: {del_result.stderr.strip() or del_result.stdout.strip()}'
                )

        # Regenerate summary once per repo (only if at least one deletion succeeded).
        if any(d['repo'] == repo_name for d in deleted):
            _update_repo_metadata(repo_path, repo.gpg_key, generate_deltas=False)

    if errors:
        _notify('error', '; '.join(errors), deleted=deleted)
    else:
        _notify('ok', f'Deleted {len(deleted)} ref(s)', deleted=deleted)



    """Periodic + post-mutation task: reconcile Build/Promotion DB records against
    the actual OSTree refs present on disk in all active repositories."""
    from apps.flatpak.utils.sync import run_repo_sync
    stats = run_repo_sync()
    return stats


@shared_task
def promote_build_task(promotion_id):
    """
    Celery task that promotes a published build to a child repository.
    Always pulls from build-repo to avoid OSTree collection-ID binding
    issues that occur when pulling between repos that have different collection IDs.
    """
    from apps.flatpak.models import Promotion
    from apps.flatpak.utils.ostree import sign_repo_summary, temp_gpg_homedir, update_repo_metadata

    try:
        promotion = Promotion.objects.select_related(
            'build', 'package', 'target_repo', 'target_repo__gpg_key'
        ).get(id=promotion_id)

        promotion.status = 'promoting'
        promotion.save()
        send_promotion_status_update(promotion)

        package = promotion.package
        target_repo = promotion.target_repo

        # Always pull from build-repo (source of truth, no collection-id issues)
        build_repo_path = os.path.join(settings.REPOS_BASE_PATH, 'build-repo')
        target_repo_path = target_repo.repo_path

        if not os.path.exists(os.path.join(target_repo_path, 'config')):
            raise FileNotFoundError(f"Target repository '{target_repo.name}' not found on disk")

        ref_name = f'app/{package.package_id}/{package.arch}/{package.branch}'
        logger.info(f"Promoting {ref_name} from build-repo to {target_repo.name}")

        pull_result = subprocess.run(
            ['ostree', 'pull-local', f'--repo={target_repo_path}', build_repo_path, ref_name],
            capture_output=True, text=True, timeout=300
        )
        if pull_result.returncode != 0:
            raise RuntimeError(f"ostree pull-local failed: {pull_result.stderr.strip()}")

        # Update repository metadata. Skip delta generation: existing deltas for
        # other refs in the repo are still valid, and regenerating all deltas on
        # every promotion (especially for large runtimes) wastes significant CPU.
        update_repo_metadata(target_repo_path, target_repo.gpg_key, generate_deltas=False)

        promotion.status = 'promoted'
        promotion.completed_at = timezone.now()
        promotion.save()
        send_promotion_status_update(promotion)
        logger.info(f"Promotion {promotion_id} complete: {ref_name} → {target_repo.name}")

    except Promotion.DoesNotExist:
        logger.error(f"Promotion {promotion_id} not found")
    except Exception as e:
        logger.error(f"Promotion {promotion_id} failed: {e}")
        try:
            p = Promotion.objects.get(id=promotion_id)
            p.status = 'failed'
            p.error_message = str(e)
            p.completed_at = timezone.now()
            p.save()
            send_promotion_status_update(p)
        except Exception:
            pass


def send_promotion_status_update(promotion):
    """
    Send promotion status via WebSocket to the notifications group.
    """
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    async_to_sync(channel_layer.group_send)(
        'notifications',
        {
            'type': 'promotion_status_update',
            'promotion_id': promotion.id,
            'status': promotion.status,
            'error_message': promotion.error_message,
            'promoted_by': promotion.promoted_by.username if promotion.promoted_by else None,
            'completed_at': promotion.completed_at.strftime('%b %d, %H:%M') if promotion.completed_at else None,
        }
    )


def send_external_ref_promotion_status_update(promotion):
    """
    Send external-ref promotion status via WebSocket to the notifications group.
    Reuses the same event type so the existing page JS can refresh after changes.
    """
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    async_to_sync(channel_layer.group_send)(
        'notifications',
        {
            'type': 'promotion_status_update',
            'promotion_id': f'external-{promotion.id}',
            'status': promotion.status,
            'error_message': promotion.error_message,
            'promoted_by': promotion.promoted_by.username if promotion.promoted_by else None,
            'completed_at': promotion.completed_at.strftime('%b %d, %H:%M') if promotion.completed_at else None,
        }
    )


@shared_task
def promote_external_ref_task(external_promotion_id):
    """
    Promote a published ExternalRef from build-repo to a child repository.
    """
    from apps.flatpak.models import ExternalRefPromotion
    from apps.flatpak.utils.ostree import update_repo_metadata

    try:
        promotion = ExternalRefPromotion.objects.select_related(
            'external_ref', 'external_ref_version', 'target_repo', 'target_repo__gpg_key'
        ).get(id=external_promotion_id)

        promotion.status = 'promoting'
        promotion.save()
        send_external_ref_promotion_status_update(promotion)

        ext = promotion.external_ref
        version = promotion.external_ref_version
        target_repo = promotion.target_repo
        build_repo_path = os.path.join(settings.REPOS_BASE_PATH, 'build-repo')
        target_repo_path = target_repo.repo_path

        source_ref = (version.ref if version else ext.ref)
        source_commit = (version.commit_hash if version else ext.commit_hash)

        if not os.path.exists(os.path.join(target_repo_path, 'config')):
            raise FileNotFoundError(f"Target repository '{target_repo.name}' not found on disk")

        logger.info(
            f"Promoting external ref {source_ref}"
            f"@{(source_commit or 'unknown')[:12]} from build-repo to {target_repo.name}"
        )

        source_candidates = [c for c in (source_commit, source_ref) if c]
        pull_result = None
        used_source = None
        pull_errors = []
        for source in source_candidates:
            pull_result = subprocess.run(
                ['ostree', 'pull-local', f'--repo={target_repo_path}', build_repo_path, source],
                capture_output=True, text=True, timeout=600
            )
            if pull_result.returncode == 0:
                used_source = source
                break
            pull_errors.append(f"{source}: {pull_result.stderr.strip() or pull_result.stdout.strip()}")

        if used_source is None:
            raise RuntimeError(f"ostree pull-local failed: {' | '.join(pull_errors)}")

        if source_commit and source_ref and used_source != source_ref:
            create_target_ref = subprocess.run(
                ['ostree', 'refs', f'--repo={target_repo_path}', '--force', f'--create={source_ref}', source_commit],
                capture_output=True, text=True
            )
            if create_target_ref.returncode != 0:
                raise RuntimeError(
                    f"Copied via {used_source}, but could not create target ref {source_ref}: "
                    f"{create_target_ref.stderr.strip() or create_target_ref.stdout.strip()}"
                )

        update_repo_metadata(target_repo_path, target_repo.gpg_key, generate_deltas=False)

        promotion.status = 'promoted'
        promotion.completed_at = timezone.now()
        promotion.save()
        send_external_ref_promotion_status_update(promotion)
        logger.info(
            f"External ref promotion {external_promotion_id} complete: "
            f"{source_ref}@{(source_commit or 'unknown')[:12]} → {target_repo.name}"
        )

    except ExternalRefPromotion.DoesNotExist:
        logger.error(f"ExternalRefPromotion {external_promotion_id} not found")
    except Exception as e:
        logger.error(f"ExternalRefPromotion {external_promotion_id} failed: {e}")
        try:
            p = ExternalRefPromotion.objects.get(id=external_promotion_id)
            p.status = 'failed'
            p.error_message = str(e)
            p.completed_at = timezone.now()
            p.save()
            send_external_ref_promotion_status_update(p)
        except Exception:
            pass


@shared_task
def promote_bst_task(bst_promotion_id):
    """
    Copy all refs from build-repo to a child repository for a BST build.
    Uses flatpak build-commit-from so metadata is correctly rewritten.
    """
    from apps.flatpak.models import BstPromotion
    from apps.flatpak.utils.ostree import update_repo_metadata

    try:
        promo = BstPromotion.objects.select_related(
            'build', 'bst_source', 'target_repo', 'target_repo__gpg_key'
        ).get(id=bst_promotion_id)

        promo.status = 'promoting'
        promo.save()
        send_bst_promotion_status_update(promo)

        build_repo_path = os.path.join(settings.REPOS_BASE_PATH, 'build-repo')
        target_repo_path = promo.target_repo.repo_path

        if not os.path.exists(os.path.join(target_repo_path, 'config')):
            raise FileNotFoundError(
                f"Target repository '{promo.target_repo.name}' not found on disk"
            )

        logger.info(
            f"BST promote: build-repo → {promo.target_repo.name} "
            f"for {promo.bst_source.name} build #{promo.build.build_number}"
        )

        # Enumerate only the BST-produced refs from build-repo so we never
        # accidentally overwrite Platform/SDK runtimes that were originally
        # promoted from a different source (e.g. via the regular flatpak pipeline).
        # We keep: app/<bst_name>/*, runtime/<bst_name>.Locale/*, runtime/<bst_name>.Debug/*
        # We exclude: appstream, appstream2, ostree-metadata, freedesktop.*, flathub.*
        # Enumerate which refs to promote.
        # Primary: use the refs recorded on the source at build time (most accurate).
        # Fallback: scan build-repo and filter by source name prefix (for old builds
        # that predate the produced_refs field).
        if promo.bst_source.produced_refs.strip():
            bst_refs = [r.strip() for r in promo.bst_source.produced_refs.splitlines() if r.strip()]
            logger.info(f"Using {len(bst_refs)} refs from bst_source.produced_refs")
        else:
            refs_result = subprocess.run(
                ['ostree', 'refs', '--list', f'--repo={build_repo_path}'],
                capture_output=True, text=True, timeout=30,
            )
            if refs_result.returncode != 0:
                raise RuntimeError(f"ostree refs failed: {refs_result.stderr.strip()}")
            bst_name = promo.bst_source.name
            bst_refs = [
                r.strip() for r in refs_result.stdout.splitlines()
                if r.strip() and (
                    r.strip().startswith(f"app/{bst_name}/")
                    or r.strip().startswith(f"runtime/{bst_name}.")
                )
            ]
            if not bst_refs:
                raise RuntimeError(
                    f"No refs found in build-repo for BST source '{bst_name}'. "
                    f"Build-repo refs: {refs_result.stdout.strip()[:500]}"
                )

        logger.info(f"BST refs to promote: {bst_refs}")

        result = subprocess.run(
            [
                'flatpak', 'build-commit-from',
                f'--src-repo={build_repo_path}',
                target_repo_path,
            ] + bst_refs,
            capture_output=True, text=True, timeout=600,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"flatpak build-commit-from failed (exit {result.returncode}):\n"
                f"{result.stderr.strip()}"
            )

        # Use generate_deltas=False: existing deltas (from prior promotions) are
        # still valid for the packages already in the repo.  We only need to
        # re-sign the summary after build-commit-from added the new BST commits.
        # Regenerating all deltas from scratch would exceed the subprocess timeout.
        meta_result = update_repo_metadata(target_repo_path, promo.target_repo.gpg_key,
                                           generate_deltas=False)
        if not meta_result['success']:
            logger.warning("BST promotion metadata update issue for %s: %s",
                           promo.target_repo.name, meta_result)

        promo.status = 'promoted'
        promo.completed_at = timezone.now()
        promo.save()
        send_bst_promotion_status_update(promo)
        logger.info(f"BST promotion {bst_promotion_id} complete → {promo.target_repo.name}")

    except BstPromotion.DoesNotExist:
        logger.error(f"BstPromotion {bst_promotion_id} not found")
    except Exception as e:
        logger.error(f"BstPromotion {bst_promotion_id} failed: {e}")
        try:
            p = BstPromotion.objects.get(id=bst_promotion_id)
            error_str = str(e)
            # linkat failure → build-repo objects missing/corrupted.
            # Auto-trigger a rebuild; once it finishes the promotion will be
            # reset to pending automatically (see buildstream_build_task success path).
            if 'linkat' in error_str or ('build-commit-from failed' in error_str and 'No such file' in error_str):
                logger.warning(
                    f"BstPromotion {bst_promotion_id}: build-repo objects missing; "
                    f"triggering automatic rebuild of {p.bst_source.name}"
                )
                error_str = (
                    f"Build-repo objects missing (linkat failed). "
                    f"A rebuild of '{p.bst_source.name}' has been triggered automatically. "
                    f"Retry this promotion after the rebuild completes. "
                    f"[REBUILD_TRIGGERED]"
                )
                try:
                    src = p.bst_source
                    src.status = 'pending'
                    src.save(update_fields=['status'])
                    buildstream_build_task.delay(src.id, force_rebuild=True)
                    logger.info(f"Auto-rebuild queued for BST source {src.id} ({src.name})")
                except Exception as rebuild_err:
                    logger.error(f"Failed to queue auto-rebuild: {rebuild_err}")
                    error_str += f" (rebuild queue failed: {rebuild_err})"
            p.status = 'failed'
            p.error_message = error_str
            p.completed_at = timezone.now()
            p.save()
            send_bst_promotion_status_update(p)
        except Exception:
            pass


def send_bst_promotion_status_update(promo):
    """Send BST promotion status via WebSocket to the notifications group."""
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    async_to_sync(channel_layer.group_send)(
        'notifications',
        {
            'type': 'promotion_status_update',
            'promotion_id': promo.id,
            'status': promo.status,
            'error_message': promo.error_message,
            'completed_at': promo.completed_at.strftime('%b %d, %H:%M') if promo.completed_at else None,
        }
    )


def send_build_status_update(package_id, status, message='', repository_id=None):
    """
    Send build status update via WebSocket to both specific build and general builds group.
    """
    from apps.flatpak.models import Package
    
    # Get repository_id if not provided
    if not repository_id:
        try:
            package = Package.objects.get(id=package_id)
            repository_id = package.repository.id
        except Package.DoesNotExist:
            repository_id = None
    
    channel_layer = get_channel_layer()
    
    event_data = {
        'type': 'build_status_update',
        'build_id': package_id,
        'status': status,
        'message': message,
        'timestamp': timezone.now().isoformat(),
        'repository_id': repository_id,
    }
    
    # Send to specific build group
    async_to_sync(channel_layer.group_send)(
        f'build_{package_id}',
        event_data
    )
    
    # Send to general builds group (for build list page)
    async_to_sync(channel_layer.group_send)(
        'builds',
        event_data
    )


def _parse_version_from_tag(tag):
    """
    Extract a sortable version tuple, a pre-release flag, and a date-version
    flag from a tag name.

    Handles common formats:
      v8.4.2                → (8, 4, 2),        is_prerelease=False, is_date=False
      8.4.2                 → (8, 4, 2),        is_prerelease=False, is_date=False
      grass_8_4_2           → (8, 4, 2),        is_prerelease=False, is_date=False
      grass_7_6_1RC1        → (7, 6, 1),        is_prerelease=True,  is_date=False
      release-3.10.1        → (3, 10, 1),       is_prerelease=False, is_date=False
      v2.0.0-beta.1         → (2, 0, 0),        is_prerelease=True,  is_date=False
      FIREFOX_149_0b10_BUILD1 → (149, 0),       is_prerelease=True,  is_date=False
      FIREFOX_149_0_BUILD1    → (149, 0),       is_prerelease=False, is_date=False
      2022-08-12-01         → (2022, 8, 12, 1), is_prerelease=False, is_date=True

    Date-version tags (YYYY-MM-DD snapshots/nightlies) are flagged so the
    caller can deprioritise them in favour of real release version numbers.

    Returns ``(tuple_of_ints, is_prerelease, is_date_version)`` or
    ``(None, True, False)`` if no version numbers could be extracted.
    """
    import re
    # Normalise separators to dots: underscores → dots
    normalised = tag.replace('_', '.')
    # Strip common non-numeric prefixes (v, V, release-, rel-, grass.)
    normalised = re.sub(r'^(?:[vV]|release[-.]|rel[-.]|[a-zA-Z]+[-.])', '', normalised)
    # Pre-release marker check (case-insensitive).
    # Strip Mozilla-style build artifact suffixes first (e.g. _BUILD1, .BUILD2)
    # so that the beta marker in tags like FIREFOX_149_0b10_BUILD1 is visible
    # at the end of the string where the regex can find it.
    # b\d+ (not just b\d) covers double-digit betas like b10.
    normalised_for_pre = re.sub(r'[._]BUILD\d+.*$', '', normalised, flags=re.IGNORECASE)
    is_prerelease = bool(re.search(
        r'[._-]?(alpha|beta|rc|dev|pre|a\d+|b\d+)[._\-\d]*$',
        normalised_for_pre, re.IGNORECASE,
    ))
    # Extract leading numeric components only
    nums = re.match(r'^(\d+(?:\.\d+)*)', normalised)
    if not nums:
        return None, True, False
    parts = tuple(int(x) for x in nums.group(1).split('.'))
    # Detect date-version tags — these are never real release version numbers
    # and should be deprioritised.  Two common forms:
    #   YYYY-MM-DD / YYYY.MM.DD  → parts (2022, 8, 12, ...)  len>=3
    #   YYYYMMDD compact          → parts (20241212,)          single 8-digit
    is_date_version = (
        (
            len(parts) >= 3
            and 2000 <= parts[0] <= 2100
            and 1 <= parts[1] <= 12
            and 1 <= parts[2] <= 31
        ) or (
            len(parts) == 1
            and len(str(parts[0])) == 8
            and 20000101 <= parts[0] <= 21001231
        )
    )
    return parts, is_prerelease, is_date_version


def _fetch_latest_upstream_tag(url):
    """
    Fetch the latest *stable* version tag from a remote git repository.

    Uses ``git ls-remote --tags --refs`` and then sorts / filters entirely
    in Python so that unusual tag formats (e.g. ``grass_8_4_2``) are handled
    correctly.  git's own ``--sort=-version:refname`` only works reliably for
    semver-style ``vX.Y.Z`` tags.

    Strategy:
    1. Pull all tags.
    2. Parse each into a numeric version tuple.
    3. Prefer stable releases; fall back to pre-releases only when no stable
       tag is found at all.
    4. Return the highest version's original tag string.

    Returns ``(version_string, error_string)`` where exactly one is non-None.
    """
    import re
    try:
        result = subprocess.run(
            ['git', 'ls-remote', '--tags', '--refs', url],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return None, result.stderr.strip() or 'git ls-remote failed'

        lines = [l for l in result.stdout.strip().splitlines() if '\t' in l]
        if not lines:
            return '', None  # repository has no tags

        candidates = []
        for line in lines:
            raw_tag = line.split('\t', 1)[-1].replace('refs/tags/', '').strip()
            version_tuple, is_prerelease, is_date_version = _parse_version_from_tag(raw_tag)
            if version_tuple is not None:
                candidates.append((version_tuple, is_prerelease, is_date_version, raw_tag))

        if not candidates:
            # Nothing parseable — fall back to the last tag alphabetically
            last = sorted(lines)[-1].split('\t', 1)[-1].replace('refs/tags/', '').strip()
            return last, None

        # Priority order:
        #   1. stable non-date versions  (e.g. 3.0.23)
        #   2. pre-release non-date      (e.g. 4.0.0-rc1)  — only if no stable non-date
        #   3. date-based only           → return '' (not a real release number)
        non_date = [(v, pre, tag) for v, pre, date, tag in candidates if not date]
        if non_date:
            stable = [(v, tag) for v, pre, tag in non_date if not pre]
            pool = stable if stable else [(v, tag) for v, pre, tag in non_date]
            # Prefer versions with 2+ components (e.g. 1.112.0) over bare
            # single-integer tags (e.g. v14) which are almost always old
            # artefacts or branch markers, not real release versions.
            multi = [(v, tag) for v, tag in pool if len(v) >= 2]
            if multi:
                pool = multi
        else:
            # All tags are date-based snapshots (e.g. 2022-08-12-01 nightlies).
            # Returning a date string as an upstream version is misleading —
            # the caller expects a real release number.  Signal "not found" so
            # the stored value is not overwritten with noise.
            return '', None
        best_tag = max(pool, key=lambda x: x[0])[1]

        # Strip common non-numeric tag prefixes so the stored value is a bare
        # version number that can be compared directly with the package version.
        # Handles: v1.2, V1.2, release_5.8.0, release-5.8.0, version-1.2, etc.
        m = re.match(r'^[a-zA-Z][a-zA-Z0-9_-]*?(\d)', best_tag)
        if m:
            best_tag = best_tag[best_tag.index(m.group(1)):]

        return best_tag, None

    except subprocess.TimeoutExpired:
        return None, 'Timed out after 30 s'
    except FileNotFoundError:
        return None, 'git binary not found'
    except Exception as e:
        return None, str(e)


def _run_version_script(script_text, package_id):
    """
    Execute a user-supplied version script and return (version, error).

    The script's shebang line determines the interpreter:
      #!/usr/bin/env python3  /  #!/usr/bin/python*  → python3
      anything else (or no shebang)                  → /bin/bash

    stdout is captured; the first non-empty stripped line is the version.
    A 30-second timeout is enforced; non-zero exit codes are treated as errors.
    """
    import stat
    import tempfile

    first_line = script_text.strip().splitlines()[0] if script_text.strip() else ''
    if 'python' in first_line:
        interpreter = 'python3'
    else:
        interpreter = '/bin/bash'

    try:
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.sh', prefix='fmd_verscript_', delete=False
        ) as tmp:
            tmp.write(script_text)
            tmp_path = tmp.name

        os.chmod(tmp_path, stat.S_IRWXU)

        result = subprocess.run(
            [interpreter, tmp_path],
            capture_output=True,
            text=True,
            timeout=30,
        )

        os.unlink(tmp_path)

        if result.returncode != 0:
            stderr = result.stderr.strip()
            return None, f"Script exited {result.returncode}: {stderr[:200]}"

        for line in result.stdout.splitlines():
            line = line.strip()
            if line:
                return line, None

        return None, "Script produced no output"

    except subprocess.TimeoutExpired:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        return None, "Script timed out after 30 seconds"
    except Exception as e:
        return None, str(e)


def _fetch_available_version(package):
    """Clone *package*'s git repository and extract its available version from the manifest.

    Returns ``(version, error)`` where exactly one of the two is ``None``:
    * ``(str, None)`` on success — version is **not** persisted here; callers
      are responsible for saving it.
    * ``(None, str)`` on failure — error contains a human-readable description.

    This is the low-level helper used by both the Celery task and the
    synchronous AJAX view so they share identical logic.
    """
    if not package.git_repo_url:
        return None, 'No git repository URL configured'

    temp_dir = None
    try:
        temp_dir = tempfile.mkdtemp(prefix=f'fmdc_avail_{package.pk}_')
        # UMask=0111 in the systemd service strips execute bits from every
        # mkdir() call in the entire process tree — including all the
        # subdirectories git creates inside .git/ during clone.  Manually
        # chmod'ing the entry point is not enough.
        # Fix: wrap the clone in bash so 'umask 0022' is applied before git
        # touches the filesystem, restoring normal directory permissions for
        # the whole subprocess.
        os.chmod(temp_dir, 0o700)
        source_dir = os.path.join(temp_dir, 'source')
        branch = package.git_branch or 'master'
        import shlex
        git_cmd = (
            f'umask 0022 && git clone --branch {shlex.quote(branch)}'
            f' --depth 1 --no-recurse-submodules'
            f' {shlex.quote(package.git_repo_url)} {shlex.quote(source_dir)}'
        )
        clone_result = subprocess.run(
            ['bash', '-c', git_cmd],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if clone_result.returncode != 0:
            msg = f'git clone failed: {clone_result.stderr.strip()}'
            logger.warning(f"_fetch_available_version: {package.package_id}: {msg}")
            return None, msg

        # Find manifest file (same search order as the build task)
        manifest_file = None
        for name in [
            f'{package.package_id}.yml', f'{package.package_id}.yaml',
            f'{package.package_id}.json',
            f'{package.package_id}.metainfo.xml',
            'flatpak.yml', 'flatpak.yaml', 'flatpak.json',
        ]:
            candidate = os.path.join(source_dir, name)
            if os.path.exists(candidate):
                manifest_file = candidate
                break

        if not manifest_file:
            # No flatpak manifest in this repo (e.g. git_repo_url points to the
            # upstream source rather than the flathub manifest repo). Fall back
            # to git tag detection on the same URL — same logic as upstream_url.
            version, tag_err = _fetch_latest_upstream_tag(package.git_repo_url)
            if version:
                return _normalise_version(version), None
            msg = f'No manifest file found; git tag fallback also failed: {tag_err}'
            logger.warning(f"_fetch_available_version: {package.package_id}: {msg}")
            return None, msg

        version = _extract_version_from_manifest(package.package_id, manifest_file)
        if not version:
            msg = 'Could not detect version from manifest'
            logger.info(f"_fetch_available_version: {package.package_id}: {msg}")
            return None, msg

        return version, None
    except Exception as e:
        logger.exception(f"_fetch_available_version: unexpected error for {package.package_id}: {e}")
        return None, str(e)
    finally:
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)


@shared_task
def check_available_version_task(package_id):
    """Detect and store the *available* version for a single git-based package.

    Delegates the actual work to :func:`_fetch_available_version` and persists
    the result.  The result is written to ``package.available_version`` and
    ``package.available_version_checked_at``.
    """
    from apps.flatpak.models import Package
    try:
        package = Package.objects.get(id=package_id)
    except Package.DoesNotExist:
        return None

    version, error = _fetch_available_version(package)
    if not version:
        if error:
            logger.warning(f"check_available_version_task: {package.package_id}: {error}")
        return None

    package.available_version = version
    package.available_version_checked_at = timezone.now()
    package.save(update_fields=['available_version', 'available_version_checked_at'])
    logger.info(f"Available version for {package.package_id}: {version!r}")
    return version


@shared_task
def check_all_available_versions():
    """Periodic task: refresh available versions for every git-based package.

    Reads its interval from ``SiteConfig.available_version_check_interval_hours``
    and also keeps the celery-beat schedule in sync with that value.
    """
    from apps.flatpak.models import Package, SiteConfig
    config = SiteConfig.get_solo()
    interval_hours = config.available_version_check_interval_hours

    # Sync beat schedule with current config
    try:
        import json
        from django_celery_beat.models import PeriodicTask, IntervalSchedule
        schedule, _ = IntervalSchedule.objects.get_or_create(
            every=max(interval_hours, 1),
            period=IntervalSchedule.HOURS,
        )
        PeriodicTask.objects.filter(name='Check all available versions').update(
            interval=schedule,
            enabled=interval_hours > 0,
        )
    except Exception as e:
        logger.warning(f"Failed to sync available-version check schedule: {e}")

    if interval_hours == 0:
        logger.info("Available version check is disabled (interval=0)")
        return "Available version check disabled"

    packages = Package.objects.filter(
        git_repo_url__isnull=False
    ).exclude(git_repo_url='')
    count = packages.count()
    for p in packages:
        check_available_version_task.delay(p.id)
    logger.info(f"Queued available version check for {count} package(s)")
    return f"Queued {count} available version check(s)"


def _normalise_version(version):
    """Normalise a raw upstream tag string into a clean version number.

    * Strips leading non-numeric word prefixes, e.g.:
        ``RELEASE.2.9.0``  →  ``2.9.0``
        ``v1.2.3``         →  ``1.2.3``
    * Converts underscore-separated digit sequences to dot-separated, e.g.:
        ``1_4_3``  →  ``1.4.3``
    * Strips trailing build-artifact suffixes, e.g.:
        ``149_0b10_BUILD1``  →  ``149.0b10``
        ``149_0_BUILD1``     →  ``149.0``
    """
    import re as _re
    if not version:
        return version
    # Strip prefix: any leading letters/symbols up to (but not including) the
    # first digit.  Use match span so we don't accidentally skip an earlier
    # occurrence of the same digit character in the prefix itself.
    _m = _re.match(r'^[a-zA-Z][a-zA-Z0-9_.+-]*?(\d)', version)
    if _m:
        version = version[_m.start(1):]
    # Replace _ between digits with . (use look-around so all separators in a
    # sequence like 1_4_3 are replaced in a single pass).
    version = _re.sub(r'(?<=\d)_(?=\d)', '.', version)
    # Strip trailing build-artifact suffixes: _BUILD1, .BUILD2, _BUILD1_SOMETHING etc.
    # These are release-engineering markers (common in Mozilla tags) and are not
    # part of the human-readable version number.
    version = _re.sub(r'[._]BUILD\d+.*$', '', version, flags=_re.IGNORECASE)
    # Strip any remaining trailing word-only suffix (no digits), e.g. _RELEASE,
    # .RELEASE, _STABLE, .FINAL — these are tag decorations, not version parts.
    version = _re.sub(r'[._][a-zA-Z]+$', '', version)
    # Strip pre-release suffixes that are directly attached to the last digit
    # component, e.g. 149.0b10 → 149.0, 3.0a2 → 3.0, 5.0rc1 → 5.0.
    # These appear when a script or upstream URL returns a beta/RC tag instead
    # of the latest stable (e.g. Firefox product-details API).
    version = _re.sub(r'(a|b)\d+$', '', version)
    version = _re.sub(r'[._-]?(rc|alpha|beta|pre|dev)\d*$', '', version, flags=_re.IGNORECASE)
    version = version.rstrip('._-')
    return version


@shared_task
def check_upstream_version_task(package_id):
    """Check and store the latest upstream version for a single package.

    Version resolution order:
      1. Run ``upstream_version_script`` if set; use its stdout as the version.
      2. Fall back to git-tag detection via ``upstream_url`` (if set) when the
         script is absent, empty, or fails.
    """
    from apps.flatpak.models import Package
    try:
        package = Package.objects.get(id=package_id)
    except Package.DoesNotExist:
        return None

    version = None

    # --- Step 1: custom version script ---
    if package.upstream_version_script.strip():
        version, script_error = _run_version_script(
            package.upstream_version_script, package.package_id
        )
        if script_error:
            logger.warning(
                f"Version script failed for {package.package_id}: {script_error}"
            )
        else:
            logger.info(
                f"Version script returned {version!r} for {package.package_id}"
            )

    # --- Step 2: git tag detection (fallback or primary when no script) ---
    if not version and package.upstream_url:
        version, error = _fetch_latest_upstream_tag(package.upstream_url)
        if error:
            logger.warning(
                f"Upstream tag check failed for {package.package_id}: {error}"
            )

    if not version:
        return None

    version = _normalise_version(version)

    package.upstream_version = version
    package.upstream_checked_at = timezone.now()
    package.save(update_fields=['upstream_version', 'upstream_checked_at'])
    logger.info(f"Upstream version for {package.package_id}: {version!r}")
    return version


@shared_task
def check_all_upstream_versions():
    """Periodic task: refresh upstream versions for every package that has an upstream_url.
    Also updates the celery-beat schedule if the configured interval has changed.
    """
    from apps.flatpak.models import Package, SiteConfig
    config = SiteConfig.get_solo()
    interval_hours = config.upstream_version_check_interval_hours

    # Sync beat schedule with current config
    try:
        import json
        from django_celery_beat.models import PeriodicTask, IntervalSchedule
        schedule, _ = IntervalSchedule.objects.get_or_create(
            every=max(interval_hours, 1),
            period=IntervalSchedule.HOURS,
        )
        PeriodicTask.objects.filter(name='Check all upstream versions').update(
            interval=schedule,
            enabled=interval_hours > 0,
        )
    except Exception as e:
        logger.warning(f"Failed to sync upstream check schedule: {e}")

    if interval_hours == 0:
        logger.info("Upstream version check is disabled (interval=0)")
        return "Upstream version check disabled"

    packages = Package.objects.filter(upstream_url__isnull=False).exclude(upstream_url='')
    script_only = Package.objects.filter(upstream_url='').exclude(upstream_version_script='')
    all_packages = (packages | script_only).distinct()
    count = all_packages.count()
    for p in all_packages:
        check_upstream_version_task.delay(p.id)
    logger.info(f"Queued upstream version check for {count} package(s)")
    return f"Queued {count} upstream version check(s)"


@shared_task
def retry_pending_promotions():
    """
    Periodic beat task: manages stuck promotions.

    Step 1 — Expire stale promotions:
        Any promotion in 'pending' or 'promoting' state whose created_at is
        older than SiteConfig.promotion_stale_timeout_minutes is marked as
        'failed'.  Set the timeout to 0 to disable expiry.

    Step 2 — Re-queue recent pending promotions:
        Remaining 'pending' promotions (not yet stale, or when expiry is
        disabled) are dispatched to promote_build_task so they are always
        picked up even if the original task message was dropped.
    """
    from apps.flatpak.models import Promotion, SiteConfig
    from datetime import timedelta
    config = SiteConfig.get_solo()
    timeout_minutes = config.promotion_stale_timeout_minutes

    expired_count = 0
    if timeout_minutes > 0:
        stale_cutoff = timezone.now() - timedelta(minutes=timeout_minutes)
        stale = Promotion.objects.filter(
            status__in=['pending', 'promoting'],
            created_at__lt=stale_cutoff,
        ).select_related('package')
        for promotion in stale:
            logger.warning(
                f"retry_pending_promotions: promotion {promotion.id} "
                f"({promotion.package.package_id} → {promotion.target_repo_id}) "
                f"stuck in '{promotion.status}' for >{timeout_minutes} min — marking failed"
            )
            promotion.status = 'failed'
            promotion.error_message = (
                f"Promotion timed out after {timeout_minutes} minute(s) "
                f"in '{promotion.status}' state and was automatically failed."
            )
            promotion.save(update_fields=['status', 'error_message'])
            expired_count += 1

    # Re-queue any remaining (non-stale) pending promotions
    pending = Promotion.objects.filter(status='pending').select_related('build', 'package')
    queued_count = 0
    for promotion in pending:
        logger.info(
            f"retry_pending_promotions: dispatching promotion {promotion.id} "
            f"({promotion.package.package_id} → {promotion.target_repo_id})"
        )
        promote_build_task.delay(promotion.id)
        queued_count += 1

    summary = []
    if expired_count:
        summary.append(f"Expired {expired_count} stale promotion(s)")
    if queued_count:
        summary.append(f"Queued {queued_count} pending promotion(s)")
    result = "; ".join(summary) if summary else "Nothing to do"
    if expired_count or queued_count:
        logger.info(f"retry_pending_promotions: {result}")
    return result