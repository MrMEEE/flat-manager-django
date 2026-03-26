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
    def folder_name(self):
        """Filesystem-safe version of the repository name (spaces → hyphens)."""
        return self.name.replace(' ', '-')

    @property
    def repo_path(self):
        """Get the filesystem path for this repository."""
        return os.path.join(settings.REPOS_BASE_PATH, self.folder_name)

    def get_public_key_path(self):
        """Get the path where the public GPG key should be stored."""
        if self.gpg_key:
            return os.path.join(settings.REPOS_BASE_PATH, f"{self.folder_name}.gpg")
        return None
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
    upstream_version_script = models.TextField(
        blank=True,
        help_text=(
            "Optional script to determine the latest upstream version. "
            "Include a shebang line (#!/bin/bash or #!/usr/bin/env python3). "
            "Print the version to stdout. "
            "Falls back to git tag detection if empty or if the script fails."
        )
    )

    # Available version tracking (manifest/repo scan, without a full build)
    available_version = models.CharField(
        max_length=100, blank=True,
        help_text="Version detected from manifest/repo without a full build (auto-refreshed)"
    )
    available_version_checked_at = models.DateTimeField(
        null=True, blank=True,
        help_text="When the available version was last scanned"
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

    @property
    def available_version_is_newer(self):
        """Return True when available_version is strictly newer than the last built version."""
        if not self.available_version or not self.version:
            return False
        try:
            from packaging.version import Version
            return Version(self.available_version) > Version(self.version)
        except Exception:
            # Fall back to plain string inequality if either string is not PEP-440
            return self.available_version != self.version

    def clean(self):
        """Validate that repositories with parent repos cannot have packages."""
        from django.core.exceptions import ValidationError
        if self.repository and self.repository.parent_repos.exists():
            raise ValidationError(
                "Cannot create packages for repositories that have parent repositories. "
                "Packages should be created in parent repositories and flow down."
            )


class BuildStreamSource(models.Model):
    """
    A BuildStream project that produces one or more Flatpak refs (runtimes, SDKs, …).
    Unlike Package, there is no single application ID — the BST element can export
    many refs at once (e.g. freedesktop-sdk's flatpak-release-repo.bst).
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

    repository = models.ForeignKey(
        Repository, on_delete=models.CASCADE, related_name='bst_sources',
    )
    name = models.CharField(
        max_length=255,
        help_text="Display name for this BuildStream source (e.g. freedesktop-sdk)",
    )
    git_repo_url = models.URLField(
        help_text="Git repository containing the BuildStream project (must have a project.conf)",
    )
    git_branch = models.CharField(
        max_length=100, default='master',
        help_text="Git branch or tag to build from",
    )
    bst_element = models.CharField(
        max_length=255,
        help_text="BuildStream element to build (e.g. flatpak-release-repo.bst). "
                  "The element must produce an OSTree flatpak repo in its artifact.",
    )

    # Build state
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    build_number = models.IntegerField(default=1)
    version = models.CharField(max_length=255, blank=True)
    source_commit = models.CharField(max_length=255, blank=True)
    error_message = models.TextField(blank=True)

    # Metadata
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name='bst_sources',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'BuildStream Source'
        verbose_name_plural = 'BuildStream Sources'

    def __str__(self):
        return f"{self.name} ({self.bst_element})"


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
    
    package = models.ForeignKey(
        Package, on_delete=models.CASCADE, related_name='builds',
        null=True, blank=True,
    )
    bst_source = models.ForeignKey(
        'BuildStreamSource', on_delete=models.CASCADE, related_name='builds',
        null=True, blank=True,
    )
    build_number = models.IntegerField(help_text="Build attempt number (1, 2, 3...)")
    
    # Build results
    version = models.CharField(max_length=100, blank=True, help_text="Detected application version")
    source_commit = models.CharField(max_length=64, blank=True, help_text="Git commit hash that was built")
    commit_hash = models.CharField(max_length=64, blank=True, help_text="OSTree commit hash")
    dependencies = models.JSONField(default=dict, blank=True, help_text="Flatpak SDK/runtime dependencies")
    
    # Build state
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='building')
    error_message = models.TextField(blank=True)
    celery_task_id = models.CharField(max_length=255, blank=True, help_text="Celery task ID — used to revoke/terminate the task on cancel")
    
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
        if self.package_id:
            return f"{self.package.package_name} - Build #{self.build_number}"
        if self.bst_source_id:
            return f"{self.bst_source.name} (BST) - Build #{self.build_number}"
        return f"Build #{self.build_number}"


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
    available_version_check_interval_hours = models.PositiveIntegerField(
        default=6,
        help_text="How often (in hours) to scan git repos for an available version. Set to 0 to disable."
    )
    build_timeout_minutes = models.PositiveIntegerField(
        default=120,
        help_text="Maximum time (in minutes) allowed for a single flatpak-builder run. Increase for large packages."
    )
    stale_build_check_interval_seconds = models.PositiveIntegerField(
        default=30,
        help_text="How often (in seconds) to check for stuck builds. Set to 0 to disable. Default: 30."
    )
    stale_build_timeout_minutes = models.PositiveIntegerField(
        default=30,
        help_text="Minutes of log inactivity before an in-progress build is considered stuck and marked as failed."
    )
    promotion_retry_interval_minutes = models.PositiveIntegerField(
        default=1,
        help_text="How often (in minutes) to check for and retry pending promotions. Set to 0 to disable."
    )
    promotion_stale_timeout_minutes = models.PositiveIntegerField(
        default=10,
        help_text=(
            "Minutes after which a pending or in-progress promotion is considered stuck and marked as failed. "
            "Set to 0 to never expire."
        )
    )
    client_stale_hours = models.PositiveIntegerField(
        default=24,
        help_text="Hours without a client check-in before the client is shown as stale/red."
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


class BstPromotion(models.Model):
    """
    Tracks promotion of a published BST build to a child repository.
    Mirrors Promotion but for BuildStreamSource instead of Package.
    """
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('promoting', 'Promoting'),
        ('promoted', 'Promoted'),
        ('failed', 'Failed'),
    ]

    build = models.ForeignKey(Build, on_delete=models.CASCADE, related_name='bst_promotions')
    bst_source = models.ForeignKey(
        BuildStreamSource, on_delete=models.CASCADE, related_name='promotions'
    )
    target_repo = models.ForeignKey(
        Repository, on_delete=models.CASCADE, related_name='bst_promotions'
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    error_message = models.TextField(blank=True)
    promoted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='bst_promotions',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'BST Promotion'
        constraints = [
            models.UniqueConstraint(
                fields=['build', 'target_repo'], name='unique_bst_build_target_repo'
            ),
        ]

    def __str__(self):
        return f"{self.bst_source.name} → {self.target_repo.name} (Build #{self.build.build_number})"


class FlatpakRemote(models.Model):
    """
    A Flatpak remote used by the builder to install SDK and runtime dependencies.
    Multiple remotes can be configured; they are tried in priority order.
    """
    name = models.CharField(
        max_length=100,
        unique=True,
        help_text="Remote name as known to flatpak (e.g. 'flathub')"
    )
    url = models.URLField(
        max_length=500,
        help_text="URL of the .flatpakrepo file used to register the remote if not already present"
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Only active remotes are used during builds"
    )
    priority = models.PositiveIntegerField(
        default=0,
        help_text="Remotes with a lower number are tried first (0 = highest priority)"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['priority', 'name']
        verbose_name = 'Flatpak Remote'
        verbose_name_plural = 'Flatpak Remotes'

    def __str__(self):
        status = '' if self.is_active else ' (inactive)'
        return f"{self.name}{status}"


class Client(models.Model):
    """
    Represents a machine that consumes flatpak repositories managed by this server.
    Records are created/updated via the /api/client-checkin/ endpoint.
    """
    hostname = models.CharField(
        max_length=255,
        unique=True,
        help_text="Hostname of the client machine."
    )
    last_checkin = models.DateTimeField(
        null=True, blank=True,
        help_text="Timestamp of the most recent check-in from this client."
    )
    remotes = models.JSONField(
        default=list,
        help_text="All flatpak remotes on the client: [{name, url}, ...]."
    )
    managed_remotes = models.JSONField(
        default=list,
        help_text="Names of remotes that point to this server."
    )
    installed_count = models.IntegerField(default=0)
    foreign_count = models.IntegerField(
        default=0,
        help_text="Flatpaks installed from remotes NOT managed by this server."
    )
    outdated_count = models.IntegerField(
        default=0,
        help_text="Flatpaks that have an update available (any remote)."
    )
    installed_flatpaks = models.JSONField(
        default=list,
        help_text="All installed flatpaks: [{app_id, version, origin}, ...]."
    )
    foreign_flatpaks = models.JSONField(
        default=list,
        help_text="Flatpaks from non-managed remotes: [{app_id, version, origin}, ...]."
    )
    outdated_flatpaks = models.JSONField(
        default=list,
        help_text="Flatpaks with available updates: [{app_id, current_version, new_version, origin}, ...]."
    )
    user_flatpaks = models.JSONField(
        default=list,
        help_text="Per-user installed flatpaks: [{username, installed: [...], updates_available: [...]}]."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['hostname']
        verbose_name = 'Client'
        verbose_name_plural = 'Clients'

    def __str__(self):
        return self.hostname
