from celery import shared_task
from django.utils import timezone
from django.conf import settings
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
import logging
import os
import subprocess
import shutil
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


@shared_task
def build_from_git_task(build_id):
    """
    Build a flatpak from git repository using flatpak-builder.
    This task:
    1. Clones the git repository
    2. Runs flatpak-builder to build the app
    3. Exports the build to the build OSTree repository
    4. Updates build status and logs
    """
    from apps.flatpak.models import Build, BuildLog
    
    build = None
    temp_dir = None
    
    try:
        build = Build.objects.get(id=build_id)
        
        # Validate git build
        if not build.git_repo_url:
            raise ValueError("No git repository URL specified")
        
        # Update status
        build.status = 'building'
        build.started_at = timezone.now()
        build.save()
        
        log_build(build, 'info', f"Starting git build for {build.app_id}")
        send_build_status_update(build_id, 'building', 'Cloning git repository')
        
        # Create temporary directory for build
        temp_dir = tempfile.mkdtemp(prefix=f'fmdc_build_{build.build_id}_')
        log_build(build, 'info', f"Created build directory: {temp_dir}")
        
        # Clone git repository
        log_build(build, 'info', f"Cloning {build.git_repo_url} (branch: {build.git_branch})")
        clone_result = subprocess.run(
            ['git', 'clone', '--branch', build.git_branch, '--depth', '1', '--recurse-submodules', '--shallow-submodules', build.git_repo_url, 'source'],
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
            build.source_commit = commit_result.stdout.strip()
            build.save()
            log_build(build, 'info', f"Source commit: {build.source_commit}")
        
        send_build_status_update(build_id, 'building', 'Running flatpak-builder')
        
        # Find manifest file (common names)
        manifest_file = None
        for name in [f'{build.app_id}.yml', f'{build.app_id}.yaml', f'{build.app_id}.json', 
                     'flatpak.yml', 'flatpak.yaml', 'flatpak.json']:
            candidate = os.path.join(source_dir, name)
            if os.path.exists(candidate):
                manifest_file = candidate
                break
        
        if not manifest_file:
            raise FileNotFoundError(
                f"No manifest file found. Looking for {build.app_id}.yml, flatpak.yml, etc."
            )
        
        log_build(build, 'info', f"Using manifest: {os.path.basename(manifest_file)}")
        
        # Parse manifest to extract dependencies
        dependencies = parse_manifest_dependencies(build, manifest_file)
        if dependencies:
            build.dependencies = dependencies
            build.save()
            
            # Enhanced dependency logging
            dep_info = f"SDK={dependencies.get('sdk')}, Runtime={dependencies.get('runtime')}"
            if 'sdk_extensions' in dependencies:
                extensions = [ext['name'] for ext in dependencies['sdk_extensions']]
                dep_info += f", Extensions={extensions}"
            
            log_build(build, 'info', f"Detected dependencies: {dep_info}")
            
            # Install dependencies before building
            if not install_flatpak_dependencies(build, dependencies):
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
        
        # Run flatpak-builder
        log_build(build, 'info', "Running flatpak-builder (this may take a while)...")
        
        flatpak_builder_cmd = [
            'flatpak-builder',
            '--force-clean',
            '--repo', build_repo_path,
            '--default-branch', build.branch,
            build_dir,
            manifest_file
        ]
        
        builder_result = subprocess.run(
            flatpak_builder_cmd,
            cwd=source_dir,
            capture_output=True,
            text=True,
            timeout=1800  # 30 minutes max
        )
        
        # Log output
        if builder_result.stdout:
            for line in builder_result.stdout.split('\n'):
                if line.strip():
                    log_build(build, 'info', line.strip())
        
        if builder_result.returncode != 0:
            error_msg = builder_result.stderr or "flatpak-builder failed"
            log_build(build, 'error', f"Build failed: {error_msg}")
            
            # Try to detect and install missing dependencies
            if 'not installed' in error_msg or 'Unable to find' in error_msg:
                log_build(build, 'info', "Attempting to install missing dependencies...")
                if detect_and_install_dependencies(build, error_msg):
                    log_build(build, 'info', "Dependencies installed, retrying build...")
                    
                    # Retry flatpak-builder
                    builder_result = subprocess.run(
                        flatpak_builder_cmd,
                        cwd=source_dir,
                        capture_output=True,
                        text=True,
                        timeout=1800
                    )
                    
                    if builder_result.stdout:
                        for line in builder_result.stdout.split('\n'):
                            if line.strip():
                                log_build(build, 'info', line.strip())
                    
                    if builder_result.returncode != 0:
                        error_msg = builder_result.stderr or "flatpak-builder failed after dependency install"
                        log_build(build, 'error', f"Build still failed: {error_msg}")
                        raise RuntimeError(f"flatpak-builder failed: {error_msg}")
                else:
                    raise RuntimeError(f"flatpak-builder failed: {error_msg}")
            else:
                raise RuntimeError(f"flatpak-builder failed: {error_msg}")
        
        # Success
        build.status = 'built'
        build.completed_at = timezone.now()
        build.save()
        
        log_build(build, 'info', "Build completed successfully")
        send_build_status_update(build_id, 'built', 'Build completed, ready to publish')
        
    except Build.DoesNotExist:
        logger.error(f"Build {build_id} not found")
    except Exception as e:
        logger.error(f"Error building from git {build_id}: {str(e)}")
        if build:
            build.status = 'failed'
            build.error_message = str(e)
            build.completed_at = timezone.now()
            build.save()
            
            log_build(build, 'error', f"Build failed: {str(e)}")
            send_build_status_update(build_id, 'failed', f'Build failed: {str(e)}')
    finally:
        # Cleanup temp directory
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
                logger.info(f"Cleaned up temp directory: {temp_dir}")
            except Exception as e:
                logger.warning(f"Failed to cleanup temp directory {temp_dir}: {e}")


@shared_task
def commit_build_task(build_id):
    """
    Commit a build - validates the build and marks it ready for publishing.
    For upload-based builds, this validates all refs have been uploaded.
    For git-based builds, this is called after flatpak-builder completes.
    """
    from apps.flatpak.models import Build, BuildLog
    
    try:
        build = Build.objects.get(id=build_id)
        
        if build.status not in ['pending', 'building', 'built']:
            raise ValueError(f"Cannot commit build with status: {build.status}")
        
        log_build(build, 'info', "Committing build")
        build.status = 'committing'
        build.save()
        
        send_build_status_update(build_id, 'committing', 'Validating build')
        
        # Get build repo
        build_repo_path = os.path.join(settings.REPOS_BASE_PATH, 'build-repo')
        
        if not os.path.exists(os.path.join(build_repo_path, 'config')):
            raise FileNotFoundError("Build repository not found")
        
        # Verify the ref exists in build-repo
        ref_name = f'app/{build.app_id}/{build.arch}/{build.branch}'
        
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
                app_refs = [r for r in refs if build.app_id in r]
                if app_refs:
                    log_build(build, 'warning', f"Exact ref not found, but found: {', '.join(app_refs)}")
                    ref_name = app_refs[0]  # Use the first match
                else:
                    raise ValueError(f"No refs found for {build.app_id}")
        
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
                build.commit_hash = commit_hash
                log_build(build, 'info', f"Commit hash: {commit_hash}")
        
        build.status = 'committed'
        build.save()
        
        log_build(build, 'info', "Build committed successfully, ready to publish")
        send_build_status_update(build_id, 'committed', 'Build committed, ready to publish')
        
    except Build.DoesNotExist:
        logger.error(f"Build {build_id} not found")
    except Exception as e:
        logger.error(f"Error committing build {build_id}: {str(e)}")
        if build:
            build.status = 'failed'
            build.error_message = str(e)
            build.save()
            log_build(build, 'error', f"Commit failed: {str(e)}")
            send_build_status_update(build_id, 'failed', f'Commit failed: {str(e)}')


@shared_task
def publish_build_task(build_id):
    """
    Publish a committed build to the target repository.
    This pulls the commit from build-repo and pushes it to the main repository.
    """
    from apps.flatpak.models import Build, BuildLog
    from apps.flatpak.utils.ostree import sign_repo_summary
    
    try:
        build = Build.objects.get(id=build_id)
        
        if build.status != 'committed':
            raise ValueError(f"Cannot publish build with status: {build.status}. Must be 'committed'.")
        
        log_build(build, 'info', "Publishing build to repository")
        build.status = 'publishing'
        build.save()
        
        send_build_status_update(build_id, 'publishing', 'Publishing to repository')
        
        # Get repositories
        build_repo_path = os.path.join(settings.REPOS_BASE_PATH, 'build-repo')
        target_repo_path = os.path.join(settings.REPOS_BASE_PATH, build.repository.name)
        
        if not os.path.exists(os.path.join(target_repo_path, 'config')):
            raise FileNotFoundError(f"Target repository {build.repository.name} not found")
        
        # Determine the ref name
        ref_name = f'app/{build.app_id}/{build.arch}/{build.branch}'
        
        log_build(build, 'info', f"Pulling {ref_name} from build-repo to {build.repository.name}")
        
        # Pull the commit from build-repo to target repo
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
        
        # Update repository summary
        log_build(build, 'info', "Updating repository summary")
        summary_cmd = ['ostree', 'summary', '-u', f'--repo={target_repo_path}']
        
        summary_result = subprocess.run(
            summary_cmd,
            capture_output=True,
            text=True
        )
        
        if summary_result.returncode != 0:
            logger.warning(f"Failed to update summary: {summary_result.stderr}")
        
        # Sign the repository if GPG key is configured
        if build.repository.gpg_key:
            log_build(build, 'info', f"Signing repository with key {build.repository.gpg_key.key_id}")
            try:
                result = sign_repo_summary(target_repo_path, build.repository.gpg_key.key_id)
                if result['success']:
                    log_build(build, 'info', "Repository signed successfully")
                else:
                    logger.warning(f"Failed to sign repository: {result.get('error')}")
                    log_build(build, 'warning', f"Repository signing failed: {result.get('error')}")
            except Exception as e:
                logger.warning(f"Failed to sign repository: {e}")
                log_build(build, 'warning', f"Repository signing failed: {e}")
        
        # Mark as published
        build.status = 'published'
        build.published_at = timezone.now()
        build.save()
        
        log_build(build, 'info', f"Build published successfully to {build.repository.name}")
        send_build_status_update(build_id, 'published', 'Build published successfully')
        
    except Build.DoesNotExist:
        logger.error(f"Build {build_id} not found")
    except Exception as e:
        logger.error(f"Error publishing build {build_id}: {str(e)}")
        if build:
            build.status = 'failed'
            build.error_message = str(e)
            build.save()
            log_build(build, 'error', f"Publish failed: {str(e)}")
            send_build_status_update(build_id, 'failed', f'Publish failed: {str(e)}')


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
        f"[Build {build.build_id}] {message}"
    )
    
    # Broadcast log via WebSocket
    channel_layer = get_channel_layer()
    if channel_layer:
        async_to_sync(channel_layer.group_send)(
            'builds',
            {
                'type': 'build_log_update',
                'build_id': build.id,
                'log': {
                    'id': log.id,
                    'message': message,
                    'level': level,
                    'timestamp': log.timestamp.strftime('%H:%M:%S')
                }
            }
        )
    
    # Broadcast log via WebSocket
    channel_layer = get_channel_layer()
    if channel_layer:
        async_to_sync(channel_layer.group_send)(
            'builds',
            {
                'type': 'build_log_update',
                'build_id': build.id,
                'log': {
                    'id': log.id,
                    'message': message,
                    'level': level,
                    'timestamp': log.timestamp.strftime('%H:%M:%S')
                }
            }
        )


def detect_and_install_dependencies(build, error_message):
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
                    arch = build.arch or 'x86_64'
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
            install_result = subprocess.run(
                ['flatpak', 'install', '-y', '--noninteractive', 'flathub', dep],
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


def parse_manifest_dependencies(build, manifest_file):
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
            app_name = build.app_id.split('.')[-1].lower() if build.app_id else None
            
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
                    if version:
                        break
            
            # 3. If still not found, try all modules' sources (not just main app)
            if not version:
                for module in manifest.get('modules', []):
                    if isinstance(module, str):
                        continue
                    
                    for source in module.get('sources', []):
                        if isinstance(source, str):
                            continue
                        
                        source_type = source.get('type', '')
                        
                        # Check git tags
                        if source_type == 'git':
                            tag = source.get('tag', '')
                            if tag and re.match(r'v?\d+\.\d+', tag):
                                version = tag.lstrip('v')
                                log_build(build, 'info', f"Found version in git tag: {version}")
                                break
                        
                        # Check archive URLs
                        elif source_type == 'archive':
                            url = source.get('url', '')
                            if url:
                                match = re.search(r'[-_/]v?(\d+\.\d+(?:\.\d+)?(?:\.\d+)?)', url)
                                if match:
                                    version = match.group(1)
                                    log_build(build, 'info', f"Found version in archive URL: {version}")
                                    break
                    
                    if version:
                        break
        
        # If version found, save it to build
        if version:
            build.version = version
            build.save(update_fields=['version'])
            log_build(build, 'info', f"Detected application version: {version}")
        
        # Extract SDK
        if 'sdk' in manifest:
            sdk = manifest['sdk']
            sdk_version = manifest.get('runtime-version', manifest.get('sdk-version', ''))
            dependencies['sdk'] = sdk
            dependencies['sdk_version'] = sdk_version
            dependencies['sdk_full'] = f"{sdk}/{build.arch or 'x86_64'}/{sdk_version}"
        
        # Extract Runtime
        if 'runtime' in manifest:
            runtime = manifest['runtime']
            runtime_version = manifest.get('runtime-version', '')
            dependencies['runtime'] = runtime
            dependencies['runtime_version'] = runtime_version
            dependencies['runtime_full'] = f"{runtime}/{build.arch or 'x86_64'}/{runtime_version}"
        
        # Extract base app if present
        if 'base' in manifest:
            base = manifest['base']
            base_version = manifest.get('base-version', runtime_version)
            dependencies['base'] = base
            dependencies['base_version'] = base_version
            dependencies['base_full'] = f"{base}/{build.arch or 'x86_64'}/{base_version}"
        
        # Extract SDK extensions if present
        if 'sdk-extensions' in manifest:
            sdk_extensions = manifest['sdk-extensions']
            dependencies['sdk_extensions'] = []
            sdk_version = dependencies.get('sdk_version', '')
            arch = build.arch or 'x86_64'
            
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


def install_flatpak_dependencies(build, dependencies):
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
    install_scope = f"--{build.installation_type}" if hasattr(build, 'installation_type') and build.installation_type else '--system'
    scope_name = build.installation_type if hasattr(build, 'installation_type') and build.installation_type else 'system'
    
    log_build(build, 'info', f"Installing {len(refs_to_install)} dependencies from flathub to {scope_name}...")
    
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
            
            # Install to the specified scope
            log_build(build, 'info', f"Installing {ref} to {scope_name}...")
            install_result = subprocess.run(
                ['flatpak', 'install', '-y', install_scope, '--noninteractive', 'flathub', ref],
                capture_output=True,
                text=True,
                timeout=600
            )
            
            if install_result.returncode == 0:
                log_build(build, 'info', f"✓ Successfully installed {ref} to {scope_name}")
            else:
                error_msg = install_result.stderr.strip()
                if 'already installed' in error_msg.lower():
                    log_build(build, 'info', f"✓ {ref} is already installed")
                elif scope_name == 'system' and ('insufficient permissions' in error_msg.lower() or 'permission denied' in error_msg.lower()):
                    # Try installing to user space instead
                    log_build(build, 'warning', f"Cannot install to system (permission denied), trying user installation...")
                    user_install = subprocess.run(
                        ['flatpak', 'install', '-y', '--user', '--noninteractive', 'flathub', ref],
                        capture_output=True,
                        text=True,
                        timeout=600
                    )
                    if user_install.returncode == 0:
                        log_build(build, 'info', f"✓ Successfully installed {ref} to user")
                    else:
                        log_build(build, 'error', f"✗ Failed to install {ref}: {user_install.stderr.strip()}")
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
    from apps.flatpak.models import Build
    
    # Find all pending builds with git URLs that haven't been triggered
    pending_builds = Build.objects.filter(
        status='pending',
        git_repo_url__isnull=False
    ).exclude(git_repo_url='')
    
    count = pending_builds.count()
    if count > 0:
        logger.info(f"Found {count} pending git-based build(s), triggering...")
        
        for build in pending_builds:
            logger.info(f"Triggering build {build.build_id} - {build.app_id}")
            build_from_git_task.delay(build.id)
    
    return f"Checked pending builds: {count} triggered"


def send_build_status_update(build_id, status, message='', repository_id=None):
    """
    Send build status update via WebSocket to both specific build and general builds group.
    """
    from apps.flatpak.models import Build
    
    # Get repository_id if not provided
    if not repository_id:
        try:
            build = Build.objects.get(id=build_id)
            repository_id = build.repository.id
        except Build.DoesNotExist:
            repository_id = None
    
    channel_layer = get_channel_layer()
    
    event_data = {
        'type': 'build_status_update',
        'build_id': build_id,
        'status': status,
        'message': message,
        'timestamp': timezone.now().isoformat(),
        'repository_id': repository_id,
    }
    
    # Send to specific build group
    async_to_sync(channel_layer.group_send)(
        f'build_{build_id}',
        event_data
    )
    
    # Send to general builds group (for build list page)
    async_to_sync(channel_layer.group_send)(
        'builds',
        event_data
    )
