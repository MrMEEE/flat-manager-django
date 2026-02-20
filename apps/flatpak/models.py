from django.db import models
from django.conf import settings
import secrets
import os


class GPGKey(models.Model):
    """
    GPG key for signing repositories.
    """
    name = models.CharField(max_length=255, help_text="Key name/description")
    email = models.EmailField(help_text="Email associated with the key")
    key_id = models.CharField(max_length=16, unique=True, help_text="GPG key ID")
    fingerprint = models.CharField(max_length=40, unique=True, help_text="GPG key fingerprint")
    public_key = models.TextField(help_text="Public key (ASCII armored)")
    private_key = models.TextField(help_text="Private key (ASCII armored, encrypted)")
    passphrase_hint = models.CharField(max_length=255, blank=True, help_text="Hint for the passphrase")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='gpg_keys')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True)
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'GPG Key'
        verbose_name_plural = 'GPG Keys'
    
    def __str__(self):
        return f"{self.name} ({self.key_id})"


class Repository(models.Model):
    """
    Flatpak repository model.
    """
    name = models.CharField(max_length=255, unique=True)
    collection_id = models.CharField(max_length=255, default='', blank=True, help_text="Collection ID for the repository (e.g., org.example.Repo)")
    description = models.TextField(blank=True)
    gpg_key = models.ForeignKey(GPGKey, on_delete=models.SET_NULL, null=True, blank=True, related_name='repositories', help_text="GPG key for signing")
    parent_repos = models.ManyToManyField('self', symmetrical=False, blank=True, related_name='child_repos', help_text="Parent repositories in the lifecycle")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='repositories')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True)
    
    class Meta:
        ordering = ['-created_at']
        verbose_name_plural = 'Repositories'
    
    def __str__(self):
        return self.name
    
    @property
    def repo_path(self):
        """Get the filesystem path for this repository."""
        return os.path.join(settings.REPOS_BASE_PATH, self.name)
    
    def get_public_key_path(self):
        """Get the path where the public GPG key should be stored."""
        if self.gpg_key:
            return os.path.join(settings.REPOS_BASE_PATH, f"{self.name}.gpg")
        return None

    @property
    def repo_path(self):
        """Get the filesystem path for this repository."""
        return os.path.join(settings.REPOS_BASE_PATH, self.name)
    
    def get_public_key_path(self):
        """Get the path where the public GPG key should be stored."""
        if self.gpg_key:
            return os.path.join(settings.REPOS_BASE_PATH, f"{self.name}.gpg")
class RepositorySubset(models.Model):
    """
    Repository subset configuration for partial repository mirrors.
    """
    repository = models.ForeignKey(Repository, on_delete=models.CASCADE, related_name='subsets')
    name = models.CharField(max_length=255, help_text="Subset name")
    collection_id = models.CharField(max_length=255, help_text="Collection ID for this subset")
    base_url = models.URLField(blank=True, help_text="Base URL for this subset")
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['name']
        unique_together = [['repository', 'name']]
    
    def __str__(self):
        return f"{self.repository.name} - {self.name}"


class Package(models.Model):
    """
    Flatpak package configuration (formerly Build).
    Represents the package definition and current build state.
    """
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('building', 'Building'),
        ('built', 'Built'),
        ('committing', 'Committing'),
        ('committed', 'Committed'),
        ('publishing', 'Publishing'),
        ('published', 'Published'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
    ]
    
    repository = models.ForeignKey(Repository, on_delete=models.CASCADE, related_name='packages')
    package_id = models.CharField(max_length=255, help_text="Flatpak application ID (e.g., org.example.MyApp)")
    package_name = models.CharField(max_length=255, help_text="Human-readable package name (e.g., My Application)")
    
    # Build source - either git repo OR upload pre-built packages
    git_repo_url = models.URLField(blank=True, help_text="Git repository URL to build from")
    git_branch = models.CharField(max_length=100, blank=True, default='master', help_text="Git branch to build")
    
    # Build configuration
    version = models.CharField(max_length=255, blank=True, help_text="Version number (extracted from manifest if git-based)")
    branch = models.CharField(max_length=100, default='stable', help_text="Flatpak branch (stable/beta/etc)")
    arch = models.CharField(max_length=50, default='x86_64')
    installation_type = models.CharField(
        max_length=10,
        choices=[('system', 'System'), ('user', 'User')],
        default='user',
        help_text="Install dependencies as system or user"
    )
    
    # Upstream version tracking
    upstream_url = models.URLField(
        blank=True,
        help_text="Upstream git repository URL to watch for new tags (e.g. https://github.com/user/repo)"
    )
    upstream_version = models.CharField(
        max_length=100, blank=True,
        help_text="Latest upstream version tag (auto-fetched)"
    )
    upstream_checked_at = models.DateTimeField(
        null=True, blank=True,
        help_text="When the upstream version was last checked"
    )

    # Build results
    source_commit = models.CharField(max_length=255, blank=True, help_text="Git commit hash that was built")
    commit_hash = models.CharField(max_length=255, blank=True, help_text="OSTree commit hash")
    dependencies = models.JSONField(null=True, blank=True, help_text="Detected Flatpak dependencies")
    error_message = models.TextField(blank=True, help_text="Error message if build failed")
    
    # Current build state
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    build_number = models.IntegerField(default=1, help_text="Next build attempt number (increments on retry)")
    
    # Metadata
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='packages')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
        unique_together = [['repository', 'package_id', 'arch', 'branch', 'git_branch']]
    
    def __str__(self):
        return f"{self.package_name} ({self.package_id})"
    
    def clean(self):
        """Validate that repositories with parent repos cannot have packages."""
        from django.core.exceptions import ValidationError
        if self.repository and self.repository.parent_repos.exists():
            raise ValidationError(
                "Cannot create packages for repositories that have parent repositories. "
                "Packages should be created in parent repositories and flow down."
            )


class Build(models.Model):
    """
    Build history record - stores each build attempt.
    """
    STATUS_CHOICES = [
        ('building', 'Building'),
        ('built', 'Built'),
        ('committing', 'Committing'),
        ('committed', 'Committed'),
        ('publishing', 'Publishing'),
        ('published', 'Published'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
    ]
    
    package = models.ForeignKey(Package, on_delete=models.CASCADE, related_name='builds')
    build_number = models.IntegerField(help_text="Build attempt number (1, 2, 3...)")
    
    # Build results
    version = models.CharField(max_length=100, blank=True, help_text="Detected application version")
    source_commit = models.CharField(max_length=64, blank=True, help_text="Git commit hash that was built")
    commit_hash = models.CharField(max_length=64, blank=True, help_text="OSTree commit hash")
    dependencies = models.JSONField(default=dict, blank=True, help_text="Flatpak SDK/runtime dependencies")
    
    # Build state
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='building')
    error_message = models.TextField(blank=True)
    
    # Timestamps
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        ordering = ['-build_number']
        unique_together = [['package', 'build_number']]
        indexes = [
            models.Index(fields=['package', '-build_number']),
            models.Index(fields=['status']),
            models.Index(fields=['-started_at']),
        ]
    
    def __str__(self):
        return f"{self.package.package_name} - Build #{self.build_number}"


class BuildArtifact(models.Model):
    """
    Build artifacts (uploaded files).
    """
    build = models.ForeignKey(Build, on_delete=models.CASCADE, related_name='artifacts')
    filename = models.CharField(max_length=255)
    file_path = models.CharField(max_length=512)
    file_size = models.BigIntegerField()
    checksum = models.CharField(max_length=64)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"Build #{self.build.build_number} - {self.filename}"


class BuildLog(models.Model):
    """
    Build logs for tracking build progress.
    """
    build = models.ForeignKey(Build, on_delete=models.CASCADE, related_name='logs')
    message = models.TextField()
    level = models.CharField(max_length=20, default='info')
    timestamp = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['timestamp']
        indexes = [
            models.Index(fields=['build', 'timestamp']),
        ]
    
    def __str__(self):
        return f"Build #{self.build.build_number} - {self.level}"


class Token(models.Model):
    """
    Repository tokens for access control.
    """
    TOKEN_TYPES = [
        ('upload', 'Upload'),
        ('download', 'Download'),
        ('admin', 'Admin'),
    ]
    
    repository = models.ForeignKey(Repository, on_delete=models.CASCADE, related_name='tokens')
    name = models.CharField(max_length=255)
    token = models.CharField(max_length=64, unique=True)
    token_type = models.CharField(max_length=20, choices=TOKEN_TYPES)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.repository.name} - {self.name}"


class SiteConfig(models.Model):
    """
    Singleton model for site-wide configuration settings.
    Access via SiteConfig.get_solo().
    """
    failed_builds_to_keep = models.PositiveIntegerField(
        default=1,
        help_text="Number of failed builds to keep per package (oldest are deleted automatically)"
    )
    upstream_version_check_interval_hours = models.PositiveIntegerField(
        default=1,
        help_text="How often (in hours) to automatically check for new upstream versions. Set to 0 to disable."
    )

    class Meta:
        verbose_name = 'Site Configuration'

    def __str__(self):
        return 'Site Configuration'

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class Promotion(models.Model):
    """
    Tracks promotion of a published build to a child repository.
    Each record represents one build being pushed to one target repo.
    """
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('promoting', 'Promoting'),
        ('promoted', 'Promoted'),
        ('failed', 'Failed'),
    ]

    build = models.ForeignKey(Build, on_delete=models.CASCADE, related_name='promotions')
    package = models.ForeignKey(Package, on_delete=models.CASCADE, related_name='promotions')
    target_repo = models.ForeignKey(Repository, on_delete=models.CASCADE, related_name='promotions')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    error_message = models.TextField(blank=True)
    promoted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='promotions'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Promotion'
        constraints = [
            models.UniqueConstraint(fields=['build', 'target_repo'], name='unique_build_target_repo'),
        ]

    def __str__(self):
        return f"{self.package.package_name} → {self.target_repo.name} (Build #{self.build.build_number})"
