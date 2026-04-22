#!/usr/bin/env python3
"""
Refactoring script to update tasks.py from Build to Package model.

This script updates:
1. Function names: build_from_git_task → package_from_git_task, etc.
2. Model imports: Build → Package
3. Variable names: build → package throughout
4. Field references: app_id → package_id, build_id → build_number
5. Log messages and comments
"""

import re
import sys

def refactor_tasks_file(file_path):
    """Refactor tasks.py file."""
    
    with open(file_path, 'r') as f:
        content = f.read()
    
    original = content
    
    # Step 1: Update function names
    print("Step 1: Updating function names...")
    replacements = [
        (r'def build_from_git_task\(', 'def package_from_git_task('),
        (r'build_from_git_task\.delay', 'package_from_git_task.delay'),
        (r'def commit_build_task\(', 'def commit_package_task('),
        (r'commit_build_task\.delay', 'commit_package_task.delay'),
        (r'def publish_build_task\(', 'def publish_package_task('),
        (r'publish_build_task\.delay', 'publish_package_task.delay'),
    ]
    
    for pattern, replacement in replacements:
        content = re.sub(pattern, replacement, content)
        count = len(re.findall(pattern, original))
        print(f"  - Replaced '{pattern}' ({count} occurrences)")
    
    # Step 2: Update model imports
    print("\nStep 2: Updating model imports...")
    # Keep BuildLog, just change Build → Package
    content = re.sub(
        r'from apps\.flatpak\.models import Build, BuildLog',
        'from apps.flatpak.models import Package, Build, BuildLog',
        content
    )
    content = re.sub(
        r'from apps\.flatpak\.models import Build\b',
        'from apps.flatpak.models import Package',
        content
    )
    
    # Step 3: Update parameter names
    print("\nStep 3: Updating parameter names...")
    # be careful - log_build function still needs to work with Build history model
    content = re.sub(r'\bdef package_from_git_task\(build_id\):', 'def package_from_git_task(package_id):', content)
    content = re.sub(r'\bdef commit_package_task\(build_id\):', 'def commit_package_task(package_id):', content)
    content = re.sub(r'\bdef publish_package_task\(build_id\):', 'def publish_package_task(package_id):', content)
    content = re.sub(r'\bdef send_build_status_update\(build_id,', 'def send_build_status_update(package_id,', content)
    
    # Step 4: Update variable assignments and object retrieval
    print("\nStep 4: Updating variable assignments...")
    # Build.objects.get(id=build_id) → Package.objects.get(id=package_id)
    content = re.sub(r'Build\.objects\.get\(id=build_id\)', 'Package.objects.get(id=package_id)', content)
    content = re.sub(r'Build\.objects\.get\(id=package_id\)', 'Package.objects.get(id=package_id)', content)
    
    # Build.objects.filter → Package.objects.filter (except when querying Build history)
    content = re.sub(r'(\s+)pending_builds = Build\.objects\.filter\(', r'\1pending_packages = Package.objects.filter(', content)
    content = re.sub(r'(\s+)stale_builds = Build\.objects\.filter\(', r'\1stale_packages = Package.objects.filter(', content)
    
    # Update variable names in function bodies
    # build = None → package = None
    content = re.sub(r'(\s+)build = None\b', r'\1package = None', content)
    # build = Build.objects → package = Package.objects
    content = re.sub(r'(\s+)build = Build\.objects', r'\1package = Package.objects', content)
    # build = Package.objects → package = Package.objects (fix double replacement)
    content = re.sub(r'(\s+)build = Package\.objects', r'\1package = Package.objects', content)
    
    # Step 5: Update field access
    print("\nStep 5: Updating field references...")
    # build.app_id → package.package_id
    content = re.sub(r'\bbuild\.app_id\b', 'package.package_id', content)
    # build.build_id → package.build_number
    content = re.sub(r'\bbuild\.build_id\b', 'package.build_number', content)
    
    # Step 6: Update general build variable references
    print("\nStep 6: Updating variable references...")
    # This is tricky - we need to replace 'build' with 'package' but NOT in:
    # - log_build() function (which works with Build history)
    # - BuildLog references
    # - 'building' status
    # - Comments about building
    # - Directory names like 'build_'
    
    # Safe replacements - these should be specific enough
    replacements = [
        (r'\bif build:', 'if package:'),
        (r'\bif not build\.', 'if not package.'),
        (r'\bbuild\.status\b', 'package.status'),
        (r'\bbuild\.started_at\b', 'package.started_at'),
        (r'\bbuild\.completed_at\b', 'package.completed_at'),
        (r'\bbuild\.error_message\b', 'package.error_message'),
        (r'\bbuild\.save\(\)', 'package.save()'),
        (r'\bbuild\.git_repo_url\b', 'package.git_repo_url'),
        (r'\bbuild\.git_branch\b', 'package.git_branch'),
        (r'\bbuild\.repository\b', 'package.repository'),
        (r'\bbuild\.branch\b', 'package.branch'),
        (r'\bbuild\.arch\b', 'package.arch'),
        (r'\bbuild\.version\b', 'package.version'),
        (r'\bbuild\.source_commit\b', 'package.source_commit'),
        (r'\bbuild\.dependencies\b', 'package.dependencies'),
        (r'\bbuild\.commit_hash\b', 'package.commit_hash'),
        (r'\bbuild\.id\b', 'package.id'),
        (r'\bbuild\.pk\b', 'package.pk'),
        (r'\(build,', '(package,'),
        (r', build\)', ', package)'),
        # Loop variables
        (r'for build in pending_builds:', 'for package in pending_packages:'),
        (r'for build in stale_builds:', 'for package in stale_packages:'),
        # Count messages
        (r'pending_builds\.count\(\)', 'pending_packages.count()'),
        (r'pending_builds\b', 'pending_packages'),
        (r'stale_builds\.count\(\)', 'stale_packages.count()'),
        (r'stale_builds\b', 'stale_packages'),
    ]
    
    for pattern, replacement in replacements:
        old_content = content
        content = re.sub(pattern, replacement, content)
        if content != old_content:
            count = len(re.findall(pattern, old_content))
            print(f"  - Replaced '{pattern}' → '{replacement}' ({count} occurrences)")
    
    # Step 7: Update log messages
    print("\nStep 7: Updating log messages...")
    # "Starting git build for" → "Starting package build for"
    content = re.sub(r'Starting git build for', 'Starting package build for', content)
    # "Build failed" → "Package build failed"
    content = re.sub(r'"Build failed:', '"Package build failed:', content)
    # "Building ", "Committing ", "Publishing " are fine as-is
    
    # Step 8: Fix any double-replacements or issues
    print("\nStep 8: Fixing potential issues...")
    # log_package should be log_build (it logs to Build history)
    content = re.sub(r'\blog_package\(', 'log_build(', content)
    
    # Make sure we didn't break check_pending_builds/cleanup_stale_builds
    content = re.sub(r'def check_pending_packages\(\):', 'def check_pending_builds():', content)
    content = re.sub(r'def cleanup_stale_packages\(\):', 'def cleanup_stale_builds():', content)
    
    # Write the updated content
    with open(file_path, 'w') as f:
        f.write(content)
    
    print(f"\n✓ Successfully refactored {file_path}")
    
    # Count changes
    changes = len([i for i, (c1, c2) in enumerate(zip(original, content)) if c1 != c2])
    print(f"  Total characters changed: {changes}")
    

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python refactor_tasks.py <tasks.py file>")
        sys.exit(1)
    
    file_path = sys.argv[1]
    refactor_tasks_file(file_path)
