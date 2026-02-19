#!/usr/bin/env python3
"""
Script to update template files to use 'package' instead of 'build'.
"""

import re
import os
from pathlib import Path

def update_template(file_path):
    """Update a template file."""
    with open(file_path, 'r') as f:
        content = f.read()
    
    original = content
    
    # Variable references
    replacements = [
        # Django template variables
        (r'\{\{\s*build\.', '{{ package.'),
        (r'\{\%\s*for\s+build\s+in\s+builds\s*\%\}', '{% for package in packages %}'),
        (r'{{ build }}', '{{ package }}'),
        (r'\{\%\s*url\s+["\']flatpak:build_', '{% url \'flatpak:package_'),
        
        # Form fields
        (r'name="build_', 'name="package_'),
        (r'id="id_build_', 'id="id_package_'),
        
        # URL parameters
        (r'build_id=', 'package_id='),
        (r'build\.id', 'package.id'),
        (r'build\.pk', 'package.pk'),
        
        # Field names
        (r'build\.app_id', 'package.package_id'),
        (r'build\.build_id', 'package.build_number'),
        
        # UI text
        (r'Build ID', 'Package ID'),
        (r'App ID', 'Package ID'),
        (r'Build #', 'Build Attempt #'),
        (r'>Build<', '>Package<'),
        (r'>Builds<', '>Packages<'),
        (r'Create Build', 'Create Package'),
        (r'Edit Build', 'Edit Package'),
        (r'Delete Build', 'Delete Package'),
        (r'Build Details', 'Package Details'),
        (r'Build List', 'Package List'),
        (r'All Builds', 'All Packages'),
        (r'New Build', 'New Package'),
        (r'Cancel Build', 'Cancel Package'),
        (r'Retry Build', 'Retry Package Build'),
        (r'Commit Build', 'Commit Package'),
        (r'Publish Build', 'Publish Package'),
        (r'build status', 'package status'),
        (r'Build Status', 'Package Status'),
        (r'Build created', 'Package created'),
        (r'Build updated', 'Package updated'),
        (r'Build deleted', 'Package deleted'),
    ]
    
    for pattern, replacement in replacements:
        content = re.sub(pattern, replacement, content)
    
    if content != original:
        with open(file_path, 'w') as f:
            f.write(content)
        return True
    return False

def main():
    templates_dir = Path('templates/flatpak')
    
    if not templates_dir.exists():
        print(f"Error: {templates_dir} not found")
        return
    
    updated = 0
    for file_path in templates_dir.glob('package_*.html'):
        if update_template(file_path):
            print(f"✓ Updated {file_path.name}")
            updated += 1
        else:
            print(f"- No changes in {file_path.name}")
    
    print(f"\nTotal files updated: {updated}")

if __name__ == '__main__':
    main()
