from celery import shared_task
from django.utils import timezone
from django.conf import settings
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
import json
import logging
import os
import subprocess
import shutil
import tempfile
import time
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
    # where ID (the app/runtime ID) is passed as the positional SRCDIR.
    # Inside bwrap that relative path doesn't exist.  appstreamcli compose
    # uses a completely different invocation style:
    #   appstreamcli compose --prefix=/ --origin=O --components=ID,ID.desktop \
    #     --result-root=PREFIX --data-dir=PREFIX/share/app-info/xmls \
    #     --icons-dir=PREFIX/share/app-info/icons/flatpak  PREFIX
    # Key insight: use old PREFIX (/app for apps, /usr for runtimes) as the
    # new SRCDIR (it holds the app data), change --prefix to /, and add the
    # required --result-root / --data-dir / --icons-dir flags.
    shim_content = (
        '#!/bin/sh\n'
        '# appstream-compose shim installed by flat-manager-django\n'
        '# Translates flatpak-builder 1.2.3 appstream-compose call to appstreamcli compose.\n'
        '#\n'
        '# Old call: appstream-compose --prefix=PREFIX --origin=O --basename=ID ID\n'
        '# New call: appstreamcli compose --prefix=/ --origin=O\n'
        '#             --result-root=PREFIX\n'
        '#             --data-dir=PREFIX/share/app-info/xmls\n'
        '#             --icons-dir=PREFIX/share/app-info/icons/flatpak PREFIX\n'
        '# Note: --basename (old output-file hint) and --components (filter) are\n'
        '# intentionally omitted — PREFIX contains only one app, no filter needed,\n'
        '# and appstreamcli 1.0.6 on RHEL9 raises filters-but-no-output otherwise.\n'
        'PREFIX=/app\n'
        'ORIGIN=flatpak\n'
        'OTHER_ARGS=\n'
        'for arg; do\n'
        '    case "$arg" in\n'
        '        --prefix=*)   PREFIX="${arg#--prefix=}" ;;\n'
        '        --origin=*)   ORIGIN="${arg#--origin=}" ;;\n'
        '        --basename=*) : ;;  # Drop — output filename set by --result-root\n'
        '        --*)          OTHER_ARGS="${OTHER_ARGS} ${arg}" ;;\n'
        '        *)            : ;;  # Drop old positional SRCDIR (app ID); replaced by PREFIX\n'
        '    esac\n'
        'done\n'
        '# shellcheck disable=SC2086\n'
        'exec appstreamcli compose \\\n'
        '    --prefix=/ \\\n'
        '    --origin="${ORIGIN}" \\\n'
        '    --result-root="${PREFIX}" \\\n'
        '    --data-dir="${PREFIX}/share/app-info/xmls" \\\n'
        '    --icons-dir="${PREFIX}/share/app-info/icons/flatpak" \\\n'
        '    ${OTHER_ARGS} \\\n'
        '    "${PREFIX}"\n'
    )
    patched = []

    for root in search_roots:
        # Match all SDK runtimes (org.freedesktop.Sdk, org.kde.Sdk, org.gnome.Sdk, …)
        pattern = os.path.join(
            root, 'runtime', '*.Sdk',
            '*', '*', 'active', 'files', 'bin'
        )
        for bin_dir in _glob.glob(pattern):
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
                        # appstream-compose) — no shim needed, leave it alone.
                        needs_write = False
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


@shared_task(bind=True)
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
        
        # Create Build history record for this attempt
        build = Build.objects.create(
            package=package,
            build_number=package.build_number,
            status='building',
            started_at=timezone.now(),
            celery_task_id=self.request.id or '',
        )
        
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
            ['git', 'clone', '--branch', package.git_branch, '--depth', '1', '--recurse-submodules', '--shallow-submodules', package.git_repo_url, 'source'],
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
        
        # Ensure submodules are fully initialized (in case --recurse-submodules didn't work)
        log_build(build, 'info', "Running git submodule update --init --recursive...")
        submodule_init_result = subprocess.run(
            ['git', 'submodule', 'update', '--init', '--recursive', '--depth', '1'],
            cwd=source_dir,
            capture_output=True,
            text=True,
            timeout=300
        )
        
        if submodule_init_result.returncode != 0:
            log_build(build, 'error', f"Submodule init failed: {submodule_init_result.stderr}")
        else:
            if submodule_init_result.stdout.strip():
                log_build(build, 'info', f"Submodule update output: {submodule_init_result.stdout.strip()}")
            else:
                log_build(build, 'info', "Submodule update completed (no output)")
        
        # Verify shared-modules directory exists
        shared_modules_path = os.path.join(source_dir, 'shared-modules')
        if os.path.exists(shared_modules_path):
            log_build(build, 'info', f"shared-modules directory exists: {os.listdir(shared_modules_path)[:10]}")
        else:
            log_build(build, 'error', "shared-modules directory NOT FOUND after submodule init!")
        
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
        
        # Find manifest file (common names)
        manifest_file = None
        for name in [f'{package.package_id}.yml', f'{package.package_id}.yaml', f'{package.package_id}.json', 
                     'flatpak.yml', 'flatpak.yaml', 'flatpak.json']:
            candidate = os.path.join(source_dir, name)
            if os.path.exists(candidate):
                manifest_file = candidate
                break
        
        if not manifest_file:
            raise FileNotFoundError(
                f"No manifest file found. Looking for {package.package_id}.yml, flatpak.yml, etc."
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
        
        # Success - update both Package and Build history
        package.status = 'built'
        package.save()
        
        build.status = 'built'
        build.completed_at = timezone.now()
        build.save()
        
        log_build(build, 'info', "Build completed successfully")
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
        
        # Get the commit hash for this ref
        show_commit = subprocess.run(
            ['ostree', 'show', ref_name, f'--repo={build_repo_path}', '--print-metadata-key=ostree.commit.timestamp'],
            capture_output=True,
            text=True
        )
        
        if show_commit.returncode == 0:
            # Extract commit hash from ostree show output
            rev_parse = subprocess.run(
                ['ostree', 'rev-parse', ref_name, f'--repo={build_repo_path}'],
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
def publish_package_task(package_id):
    """
    Publish a committed build to the target repository.
    This pulls the commit from build-repo and pushes it to the main repository.
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
        
        log_build(build, 'info', f"Pulling {ref_name} from build-repo to {package.repository.name}")
        
        # Pull the app commit from build-repo to target repo
        pull_cmd = [
            'ostree', 'pull-local',
            build_repo_path,
            ref_name,
            f'--repo={target_repo_path}'
        ]
        
        pull_result = subprocess.run(
            pull_cmd,
            capture_output=True,
            text=True,
            timeout=300
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
                ['ostree', 'pull-local', build_repo_path, locale_ref, f'--repo={target_repo_path}'],
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
        meta_result = update_repo_metadata(target_repo_path, gpg_key)
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


def log_build(build, level, message):
    """Helper to create build log entries and broadcast via WebSocket."""
    from apps.flatpak.models import BuildLog

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
            # Find the module that matches the app name (usually the last module is the main app)
            app_name = package.package_id.split('.')[-1].lower() if package.package_id else None
            
            # Try to find matching modules (check in reverse - last modules are usually the app)
            for module in reversed(manifest['modules']):  # Start from last module
                # Skip string modules (file references like "shared-modules/libsecret/libsecret.json")
                if isinstance(module, str):
                    continue
                    
                module_name = module.get('name', '').lower()
                
                # Check if this is likely the main app module (flexible matching)
                # Check if app_name is in module_name OR module_name is in app_name
                is_likely_match = False
                if app_name:
                    is_likely_match = (app_name in module_name or module_name in app_name or 
                                      module_name.replace('-', '') == app_name or
                                      module_name.replace('_', '') == app_name)
                
                if is_likely_match:
                    log_build(build, 'info', f"Checking module '{module.get('name')}' for version...")
                    # Look for version in sources
                    if 'sources' in module:
                        for source in module['sources']:
                            if isinstance(source, str):
                                continue
                                
                            source_type = source.get('type', '')
                            
                            if source_type == 'git':
                                # Check for tag field
                                tag = source.get('tag', '')
                                if tag:
                                    # Strip 'v' prefix if present
                                    version = tag.lstrip('v')
                                    log_build(build, 'info', f"Found version in git tag: {version}")
                                    break
                                # Also check branch if it looks like a version
                                branch = source.get('branch', '')
                                if branch and branch[0].isdigit():
                                    version = branch
                                    log_build(build, 'info', f"Found version in git branch: {version}")
                                    break
                            
                            elif source_type == 'archive':
                                # Extract version from archive URL or filename
                                url = source.get('url', '')
                                if url:
                                    # Try to extract version from URL
                                    # Patterns: app-1.2.3.tar.gz, app_v1.2.3.zip, app-version-1.2.3.tar.xz
                                    patterns = [
                                        r'[-_/]v?(\d+\.\d+(?:\.\d+)?(?:\.\d+)?)',  # Most common: -1.2.3 or -v1.2.3
                                        r'/(\d+\.\d+(?:\.\d+)?(?:\.\d+)?)/',        # Version in path: /1.2.3/
                                    ]
                                    for pattern in patterns:
                                        match = re.search(pattern, url)
                                        if match:
                                            version = match.group(1)
                                            log_build(build, 'info', f"Extracted version from archive URL: {version}")
                                            break
                                if version:
                                    break
                            
                            elif source_type == 'file':
                                # Check filename
                                path = source.get('path', '')
                                if path:
                                    match = re.search(r'[-_/]v?(\d+\.\d+(?:\.\d+)?(?:\.\d+)?)', path)
                                    if match:
                                        version = match.group(1)
                                        log_build(build, 'info', f"Extracted version from file path: {version}")
                                        break

                            elif source_type == 'extra-data':
                                # Apps like Chrome use extra-data with a download URL.
                                # Some manifests also carry an explicit 'version' field.
                                if source.get('version'):
                                    version = str(source['version'])
                                    log_build(build, 'info', f"Found version in extra-data version field: {version}")
                                    break
                                url = source.get('url', '')
                                if url:
                                    patterns = [
                                        r'[-_/]v?(\d+\.\d+(?:\.\d+)?(?:\.\d+)?)',
                                        r'/(\d+\.\d+(?:\.\d+)?(?:\.\d+)?)/',
                                    ]
                                    for pattern in patterns:
                                        match = re.search(pattern, url)
                                        if match:
                                            version = match.group(1)
                                            log_build(build, 'info', f"Extracted version from extra-data URL: {version}")
                                            break
                                if version:
                                    break
                    if version:
                        break
        
        # If version found, save it to both package and build
        if version:
            # Strip leading word prefix (e.g. "RELEASE.2.5.0" → "2.5.0", "version-1.0" → "1.0")
            _m = re.match(r'^[a-zA-Z][a-zA-Z0-9_.+-]*?(\d)', version)
            if _m:
                version = version[version.index(_m.group(1)):]
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
            arch = package.arch or 'x86_64'
            
            for extension in sdk_extensions:
                extension_full = f"{extension}/{arch}/{sdk_version}"
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
                log_build(build, 'info', f"✓ {ref} is already installed ({scope_name})")
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
                log_build(build, 'info', f"✓ {ref} is already installed in {other_scope_name} (will use that)")
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
    from apps.flatpak.models import Package
    
    # Find all pending builds with git URLs that haven't been triggered
    pending_packages = Package.objects.filter(
        status='pending',
        git_repo_url__isnull=False
    ).exclude(git_repo_url='')
    
    count = pending_packages.count()
    if count > 0:
        logger.info(f"Found {count} pending git-based build(s), triggering...")
        
        for package in pending_packages:
            logger.info(f"Triggering build {package.build_number} - {package.package_id}")
            package_from_git_task.delay(package.id)
    
    return f"Checked pending builds: {count} triggered"


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


@shared_task
def sync_repo_state():
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
            ['ostree', 'pull-local', build_repo_path, ref_name, f'--repo={target_repo_path}'],
            capture_output=True, text=True, timeout=300
        )
        if pull_result.returncode != 0:
            raise RuntimeError(f"ostree pull-local failed: {pull_result.stderr.strip()}")

        # Update repository metadata (appstream, signed deltas, commit signatures)
        update_repo_metadata(target_repo_path, target_repo.gpg_key)

        promotion.status = 'promoted'
        promotion.completed_at = timezone.now()
        promotion.save()
        send_promotion_status_update(promotion)
        logger.info(f"Promotion {promotion_id} complete: {ref_name} → {target_repo.name}")
        # Kick off a sync so any indirect state drift is caught immediately
        sync_repo_state.delay()

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
    Extract a sortable version tuple and a pre-release flag from a tag name.

    Handles common formats:
      v8.4.2          → (8, 4, 2),  is_prerelease=False
      8.4.2           → (8, 4, 2),  is_prerelease=False
      grass_8_4_2     → (8, 4, 2),  is_prerelease=False
      grass_7_6_1RC1  → (7, 6, 1),  is_prerelease=True
      release-3.10.1  → (3, 10, 1), is_prerelease=False
      v2.0.0-beta.1   → (2, 0, 0),  is_prerelease=True

    Returns ``(tuple_of_ints, is_prerelease)`` or ``(None, True)`` if no
    version numbers could be extracted.
    """
    import re
    # Normalise separators to dots: underscores → dots
    normalised = tag.replace('_', '.')
    # Strip common non-numeric prefixes (v, V, release-, rel-, grass.)
    normalised = re.sub(r'^(?:[vV]|release[-.]|rel[-.]|[a-zA-Z]+[-.])', '', normalised)
    # Pre-release marker check (case-insensitive) — before we strip letters
    is_prerelease = bool(re.search(r'[._-]?(alpha|beta|rc|dev|pre|a\d|b\d)[._\-\d]*$',
                                   normalised, re.IGNORECASE))
    # Extract leading numeric components only
    nums = re.match(r'^(\d+(?:\.\d+)*)', normalised)
    if not nums:
        return None, True
    parts = tuple(int(x) for x in nums.group(1).split('.'))
    return parts, is_prerelease


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
            version_tuple, is_prerelease = _parse_version_from_tag(raw_tag)
            if version_tuple is not None:
                candidates.append((version_tuple, is_prerelease, raw_tag))

        if not candidates:
            # Nothing parseable — fall back to the last tag alphabetically
            last = sorted(lines)[-1].split('\t', 1)[-1].replace('refs/tags/', '').strip()
            return last, None

        # Prefer stable releases; fall back to pre-releases if nothing stable exists
        stable = [(v, tag) for v, pre, tag in candidates if not pre]
        pool = stable if stable else [(v, tag) for v, pre, tag in candidates]
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