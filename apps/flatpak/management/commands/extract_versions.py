"""
Management command to extract versions from existing builds.
"""
from django.core.management.base import BaseCommand
from apps.flatpak.models import Build
import os
import tempfile
import subprocess
import json
import yaml


class Command(BaseCommand):
    help = 'Extract version information from existing builds'

    def add_arguments(self, parser):
        parser.add_argument(
            '--build-id',
            type=int,
            help='Specific build ID to process',
        )

    def parse_version_from_manifest(self, manifest_data, app_id):
        """Extract version from manifest data."""
        version = None
        
        # 1. Check top-level version fields
        if 'version' in manifest_data:
            version = str(manifest_data['version'])
        elif 'app-version' in manifest_data:
            version = str(manifest_data['app-version'])
        elif 'build-options' in manifest_data and 'app-version' in manifest_data['build-options']:
            version = str(manifest_data['build-options']['app-version'])
        
        # 2. If not found, look for version in modules (common pattern for main app)
        if not version and 'modules' in manifest_data:
            # Find the module that matches the app name (usually the last module is the main app)
            app_name = app_id.split('.')[-1].lower() if app_id else None
            
            for module in reversed(manifest_data['modules']):  # Start from last module
                module_name = module.get('name', '').lower()
                
                # Check if this is likely the main app module
                if app_name and app_name in module_name:
                    # Look for version in sources
                    if 'sources' in module:
                        for source in module['sources']:
                            if source.get('type') == 'git':
                                # Check for tag field
                                tag = source.get('tag', '')
                                if tag:
                                    # Strip 'v' prefix if present
                                    version = tag.lstrip('v')
                                    break
                                # Also check branch if it looks like a version
                                branch = source.get('branch', '')
                                if branch and branch[0].isdigit():
                                    version = branch
                                    break
                    if version:
                        break
        
        return version

    def handle(self, *args, **options):
        if options['build_id']:
            builds = Build.objects.filter(id=options['build_id'])
        else:
            # Only process builds without version
            builds = Build.objects.filter(version='', git_repo_url__isnull=False).exclude(git_repo_url='')

        self.stdout.write(f"Processing {builds.count()} builds...")

        for build in builds:
            temp_dir = None
            try:
                self.stdout.write(f'Build {build.id} ({build.app_id}): Processing...')
                
                # Create temporary directory
                temp_dir = tempfile.mkdtemp(prefix=f'version_extract_{build.id}_')
                
                # Clone repository to get manifest
                self.stdout.write(f'  Cloning {build.git_repo_url} (branch: {build.git_branch})...')
                clone_result = subprocess.run(
                    ['git', 'clone', '--branch', build.git_branch, '--depth', '1', build.git_repo_url, 'source'],
                    cwd=temp_dir,
                    capture_output=True,
                    text=True,
                    timeout=120
                )
                
                if clone_result.returncode != 0:
                    self.stdout.write(self.style.ERROR(
                        f'  Failed to clone repository: {clone_result.stderr}'
                    ))
                    continue

                source_dir = os.path.join(temp_dir, 'source')
                
                # Find manifest file
                manifest_file = None
                for name in [f'{build.app_id}.yml', f'{build.app_id}.yaml', f'{build.app_id}.json',
                             'flatpak.yml', 'flatpak.yaml', 'flatpak.json']:
                    candidate = os.path.join(source_dir, name)
                    if os.path.exists(candidate):
                        manifest_file = candidate
                        break

                if not manifest_file:
                    self.stdout.write(self.style.WARNING(
                        f'  No manifest file found'
                    ))
                    continue

                # Parse manifest
                self.stdout.write(f'  Parsing {os.path.basename(manifest_file)}...')
                
                with open(manifest_file, 'r') as f:
                    if manifest_file.endswith(('.yml', '.yaml')):
                        manifest_data = yaml.safe_load(f)
                    else:
                        manifest_data = json.load(f)
                
                # Extract version
                version = self.parse_version_from_manifest(manifest_data, build.app_id)
                
                if version:
                    build.version = version
                    build.save(update_fields=['version'])
                    self.stdout.write(self.style.SUCCESS(
                        f'  Version = {version}'
                    ))
                else:
                    self.stdout.write(self.style.WARNING(
                        f'  No version found in manifest'
                    ))

            except Exception as e:
                self.stdout.write(self.style.ERROR(
                    f'  Error - {str(e)}'
                ))
            finally:
                # Clean up temp directory
                if temp_dir and os.path.exists(temp_dir):
                    subprocess.run(['rm', '-rf', temp_dir])

        self.stdout.write(self.style.SUCCESS('Done!'))
