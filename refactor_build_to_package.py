#!/usr/bin/env python3
"""
Script to refactor Build → Package throughout the codebase.
"""
import re
import sys
from pathlib import Path

def refactor_file(filepath):
    """Refactor a single file."""
    print(f"Processing: {filepath}")
    
    with open(filepath, 'r') as f:
        content = f.read()
    
    original = content
    
    # Model and class name replacements
    content = re.sub(r'\bfrom \.models import ([^Build]*)Build([^A-Za-z])', r'from .models import \1Package\2', content)
    content = re.sub(r'\bimport Build\b', r'import Package', content)
    
    # View class names
    content = re.sub(r'\bBuildListView\b', 'PackageListView', content)
    content = re.sub(r'\bBuildDetailView\b', 'PackageDetailView', content)
    content = re.sub(r'\bBuildCreateView\b', 'PackageCreateView', content)
    content = re.sub(r'\bBuildUpdateView\b', 'PackageUpdateView', content)
    content = re.sub(r'\bBuildDeleteView\b', 'PackageDeleteView', content)
    content = re.sub(r'\bBuildRetryView\b', 'PackageRetryView', content)
    content = re.sub(r'\bBuildCommitView\b', 'PackageCommitView', content)
    content = re.sub(r'\bBuildPublishView\b', 'PackagePublishView', content)
    
    # Model references (be careful not to touch BuildLog, BuildArtifact)
    content = re.sub(r'\bBuild\.objects\b', 'Package.objects', content)
    content = re.sub(r'<Build,', '<Package,', content)
    content = re.sub(r'\(Build,', '(Package,', content)
    
    # Variable names
    content = re.sub(r'\bbuild_id\b', 'package_id', content)
    content = re.sub(r'\bapp_id\b', 'package_id', content)
    content = re.sub(r'\bbuild\s*=\s*Build\b', 'package = Package', content)
    content = re.sub(r'\bget_object_or_404\(Build,', 'get_object_or_404(Package,', content)
    
    # URL names (but preserve API paths for now)
    content = re.sub(r"'flatpak:build_", "'flatpak:package_", content)
    content = re.sub(r'"flatpak:build_', '"flatpak:package_', content)
    
    # Template names
    content = re.sub(r"'flatpak/build_", "'flatpak/package_", content)
    content = re.sub(r'"flatpak/build_', '"flatpak/package_', content)
    
    # Context variables (but not build_number)
    content = re.sub(r"'build':", "'package':", content)
    content = re.sub(r'"build":', '"package":', content)
    content = re.sub(r"context\['build'\]", "context['package']", content)
    
    # Messages
    content = re.sub(r'\bBuild\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+(created|updated|deleted)', r'Package \1 \2', content)
    
    if content != original:
        with open(filepath, 'w') as f:
            f.write(content)
        print(f"  ✓ Updated {filepath}")
        return True
    else:
        print(f"  - No changes needed for {filepath}")
        return False

def main():
    base_path = Path(__file__).parent
    
    files_to_process = [
        'apps/flatpak/views.py',
        'apps/flatpak/urls.py',
    ]
    
    updated_count = 0
    for file_path in files_to_process:
        full_path = base_path / file_path
        if full_path.exists():
            if refactor_file(full_path):
                updated_count += 1
        else:
            print(f"Warning: {full_path} not found")
    
    print(f"\nRefactored {updated_count} files")

if __name__ == '__main__':
    main()
