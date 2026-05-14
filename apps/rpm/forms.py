from django import forms
from .models import RpmPackage, RpmDistribution


class RpmPackageForm(forms.ModelForm):
    distributions = forms.ModelMultipleChoiceField(
        queryset=RpmDistribution.objects.filter(is_active=True, arch='x86_64'),
        widget=forms.CheckboxSelectMultiple,
        required=True,
        label="Build targets",
        help_text="Select one or more RHEL distributions to build for.",
    )

    class Meta:
        model = RpmPackage
        fields = ['name', 'description', 'git_repo_url', 'git_branch', 'spec_file',
                  'allow_internet_access', 'cleanup_on_success',
                  'upstream_url', 'upstream_version_script', 'distributions',
                  'organisations']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Optional notes about this package…'}),
            'git_repo_url': forms.URLInput(attrs={'placeholder': 'https://github.com/yourorg/mypackage.git'}),
            'git_branch': forms.TextInput(attrs={'placeholder': 'main'}),
            'spec_file': forms.TextInput(attrs={'placeholder': 'SPECS/mypackage.spec'}),
            'upstream_url': forms.URLInput(attrs={'placeholder': 'https://github.com/owner/repo'}),
            'upstream_version_script': forms.Textarea(attrs={
                'rows': 6,
                'class': 'font-monospace',
                'placeholder': (
                    '#!/bin/bash\n'
                    '# Print the latest version to stdout\n'
                    'curl -s https://api.github.com/repos/owner/repo/releases/latest \\\n'
                    "  | grep '\"tag_name\"' | cut -d'\"' -f4 | sed 's/^v//'"
                ),
            }),
            'organisations': forms.SelectMultiple(attrs={'class': 'form-select', 'size': '5'}),
        }
        help_texts = {
            'git_repo_url': 'URL of the git repository containing the SPEC file and sources.',
            'git_branch': 'Branch to clone when building.',
            'spec_file': 'Relative path to the .spec file within the repository.',
            'allow_internet_access': 'Disabled by default. Enable only for packages that must fetch dependencies during the mock build.',
            'cleanup_on_success': 'Enabled by default. Disable to keep the mock chroot around after a successful build for inspection.',
        }
