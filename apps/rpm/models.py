import os
from django.db import models
from django.conf import settings


class RpmDistribution(models.Model):
    """
    An RPM build target, auto-populated from Mock config files.
    Only RHEL releases are tracked.
    """
    name = models.CharField(
        max_length=100, unique=True,
        help_text="Mock config name (e.g. rhel-9-x86_64)"
    )
    display_name = models.CharField(
        max_length=200,
        help_text="Human-readable name (e.g. RHEL 9 (x86_64))"
    )
    mock_config = models.CharField(
        max_length=100,
        help_text="Config name passed to mock -r"
    )
    arch = models.CharField(max_length=50, help_text="Architecture (e.g. x86_64)")
    rhel_version = models.CharField(max_length=20, help_text="RHEL major version (e.g. 9)")
    is_active = models.BooleanField(default=True)
    repos_synced_at = models.DateTimeField(
        null=True, blank=True,
        help_text="When build repositories were last synced for this distribution",
    )

    class Meta:
        ordering = ['rhel_version', 'arch']
        verbose_name = 'RPM Distribution'
        verbose_name_plural = 'RPM Distributions'

    def __str__(self):
        return self.display_name

    @property
    def repo_path(self):
        return os.path.join(settings.RPM_REPO_BASE_PATH, self.name)


class RpmPackage(models.Model):
    """
    An RPM package definition backed by a git repository containing the SPEC
    file and its sources.
    """
    STATUS_CHOICES = [
        ('idle', 'Idle'),
        ('building', 'Building'),
        ('built', 'Built'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
    ]

    name = models.CharField(max_length=255, help_text="Human-readable package name")
    description = models.TextField(blank=True, default='', help_text="Optional notes about this package")
    git_repo_url = models.CharField(
        max_length=2000,
        help_text="Git repository URL containing the SPEC file and sources"
    )
    git_branch = models.CharField(
        max_length=200, default='main',
        help_text="Git branch to build from"
    )
    spec_file = models.CharField(
        max_length=500,
        help_text="Relative path to the .spec file within the repo (e.g. SPECS/mypackage.spec)"
    )
    allow_internet_access = models.BooleanField(
        default=False,
        help_text="Allow mock builds for this package to access the internet.",
    )
    distributions = models.ManyToManyField(
        RpmDistribution,
        blank=True,
        related_name='packages',
        help_text="Distributions to build for",
    )
    default_repos = models.ManyToManyField(
        'RpmRepository',
        blank=True,
        related_name='default_for_packages',
        help_text="Repositories pre-selected by default when building this package",
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='idle')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='rpm_packages',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # ── Version tracking ──────────────────────────────────────────────────────
    last_build_version = models.CharField(
        max_length=255, blank=True,
        help_text="Version from the most recent successful build (cached)"
    )
    available_version = models.CharField(
        max_length=100, blank=True,
        help_text="Version parsed from the spec file in the git repository"
    )
    available_version_checked_at = models.DateTimeField(
        null=True, blank=True,
        help_text="When the available version was last fetched from the repo"
    )
    spec_requires = models.JSONField(
        null=True, blank=True,
        help_text="Runtime and build requirements parsed from the spec file"
    )
    spec_requires_checked_at = models.DateTimeField(
        null=True, blank=True,
        help_text="When the spec requirements were last parsed"
    )

    # ── Upstream version tracking ─────────────────────────────────────────────
    upstream_url = models.URLField(
        blank=True,
        help_text="Upstream URL to watch for new version tags (e.g. https://github.com/user/repo)"
    )
    upstream_version = models.CharField(
        max_length=100, blank=True,
        help_text="Latest upstream version (auto-fetched from tags)"
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
            "Print the version to stdout."
        )
    )

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'RPM Package'
        verbose_name_plural = 'RPM Packages'

    def __str__(self):
        return self.name

    def get_status_color(self):
        return {
            'idle': 'secondary',
            'building': 'primary',
            'built': 'success',
            'failed': 'danger',
            'cancelled': 'warning',
        }.get(self.status, 'secondary')

    @property
    def available_version_is_newer(self):
        """True when available_version differs from last_build_version."""
        if not self.available_version or not self.last_build_version:
            return False
        try:
            from packaging.version import Version
            return Version(self.available_version) > Version(self.last_build_version)
        except Exception:
            return self.available_version != self.last_build_version

    @property
    def upstream_version_is_newer(self):
        """True when upstream_version differs from last_build_version."""
        if not self.upstream_version or not self.last_build_version:
            return False
        try:
            from packaging.version import Version
            return Version(self.upstream_version) > Version(self.last_build_version)
        except Exception:
            return self.upstream_version != self.last_build_version


class RpmBuild(models.Model):
    """A single build attempt of an RpmPackage against one RpmDistribution."""
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('building', 'Building'),
        ('built', 'Built'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
    ]

    package = models.ForeignKey(RpmPackage, on_delete=models.CASCADE, related_name='builds')
    distribution = models.ForeignKey(RpmDistribution, on_delete=models.CASCADE, related_name='builds')
    build_number = models.IntegerField(default=1)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    selected_repos = models.ManyToManyField(
        'RpmRepository',
        blank=True,
        related_name='builds',
        help_text='Repositories enabled for this specific build run',
    )
    version = models.CharField(max_length=255, blank=True, help_text="Version extracted from the SPEC file")
    source_commit = models.CharField(max_length=255, blank=True, help_text="Git commit that was built")
    rpm_files = models.JSONField(default=list, blank=True, help_text="Filenames of built RPMs")
    error_message = models.TextField(blank=True)
    celery_task_id = models.CharField(max_length=255, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-started_at']
        verbose_name = 'RPM Build'
        verbose_name_plural = 'RPM Builds'

    def __str__(self):
        return f"{self.package.name} #{self.build_number} ({self.distribution.name})"

    def get_status_color(self):
        return {
            'pending': 'secondary',
            'building': 'primary',
            'built': 'success',
            'failed': 'danger',
            'cancelled': 'warning',
        }.get(self.status, 'secondary')


class RpmBuildLog(models.Model):
    """A single log line from an RPM build."""
    build = models.ForeignKey(RpmBuild, on_delete=models.CASCADE, related_name='logs')
    message = models.TextField()
    level = models.CharField(max_length=20, default='info')
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['timestamp']
        verbose_name = 'RPM Build Log'
        verbose_name_plural = 'RPM Build Logs'

    def __str__(self):
        return f"[{self.level}] {self.message[:80]}"


# ---------------------------------------------------------------------------
# Satellite / Katello destinations
# ---------------------------------------------------------------------------

class SatelliteServer(models.Model):
    """
    A Satellite/Katello server.  A single service account is created per server
    (via the admin credentials entered once during setup) and its PAT is stored
    encrypted using the Django SECRET_KEY.
    """
    name = models.CharField(max_length=255, unique=True, help_text="Display name for this server")
    url = models.URLField(max_length=2000, help_text="Satellite/Katello base URL (e.g. https://satellite.example.com)")
    login = models.CharField(max_length=255, help_text="Service account login created on this server")
    token_encrypted = models.TextField(help_text="Fernet-encrypted Personal Access Token")
    ssl_verify = models.BooleanField(default=True, help_text="Verify the server's SSL certificate")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name='satellite_servers',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Satellite Server'
        verbose_name_plural = 'Satellite Servers'

    def __str__(self):
        return self.name

    @property
    def token(self) -> str:
        from apps.rpm.satellite import decrypt_token
        return decrypt_token(self.token_encrypted)


class SatelliteRepository(models.Model):
    """
    A specific Katello repository on a SatelliteServer that built RPMs can be
    pushed to.
    """
    server = models.ForeignKey(SatelliteServer, on_delete=models.CASCADE, related_name='repositories')
    organization = models.CharField(max_length=255)
    product = models.CharField(max_length=255)
    name = models.CharField(max_length=255)
    repository_id = models.IntegerField(help_text="Katello internal repository ID")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['server', 'organization', 'product', 'name']
        unique_together = [('server', 'repository_id')]
        verbose_name = 'Satellite Repository'
        verbose_name_plural = 'Satellite Repositories'

    def __str__(self):
        return f"{self.server.name} / {self.organization} / {self.product} / {self.name}"

    @property
    def display_label(self):
        return f"{self.organization} › {self.product} › {self.name}"


class RpmPackageDistributionDestination(models.Model):
    """
    Links an (RpmPackage, RpmDistribution) pair to a SatelliteRepository.
    After a successful build for that distribution the RPMs will be pushed
    to the linked repository.
    """
    package = models.ForeignKey(
        RpmPackage, on_delete=models.CASCADE, related_name='distribution_destinations',
    )
    distribution = models.ForeignKey(
        RpmDistribution, on_delete=models.CASCADE, related_name='destinations',
    )
    repository = models.ForeignKey(
        SatelliteRepository, on_delete=models.CASCADE, related_name='package_destinations',
    )

    class Meta:
        unique_together = [('package', 'distribution', 'repository')]
        verbose_name = 'Package Distribution Destination'
        verbose_name_plural = 'Package Distribution Destinations'

    def __str__(self):
        return f"{self.package.name} [{self.distribution.display_name}] → {self.repository}"

class RpmPackageSigningKey(models.Model):
    """
    Maps an (RpmPackage, RpmDistribution) pair to a GPG key.  After a
    successful build the RPMs and repomd.xml for that distribution will be
    signed with this key.
    """
    package = models.ForeignKey(
        RpmPackage, on_delete=models.CASCADE, related_name='signing_keys',
    )
    distribution = models.ForeignKey(
        RpmDistribution, on_delete=models.CASCADE, related_name='package_signing_keys',
    )
    signing_key = models.ForeignKey(
        'flatpak.GPGKey',
        on_delete=models.SET_NULL,
        null=True,
        related_name='rpm_package_signing_keys',
        help_text='GPG key used to sign built RPMs and repomd.xml',
    )

    class Meta:
        unique_together = [('package', 'distribution')]
        verbose_name = 'Package Signing Key'
        verbose_name_plural = 'Package Signing Keys'

    def __str__(self):
        return f"{self.package.name} [{self.distribution.display_name}] \u2192 {self.signing_key}"


class RpmRepository(models.Model):
    """
    A yum/dnf repository that can be included in mock builds for a given
    distribution.  Populated by the repo-sync task (subscription-manager or
    well-known RHEL patterns) and the built-in EPEL definition.

    Subscription-managed repos (source='subscription') have no baseurl/metalink
    stored here — they rely on the RHSM plugin inside the mock chroot.  Repos
    with an explicit URL (EPEL, manual) include the full address.
    """
    SOURCE_CHOICES = [
        ('subscription', 'RHSM Subscription'),
        ('epel', 'EPEL'),
        ('manual', 'Manual / Custom'),
    ]

    distribution = models.ForeignKey(
        RpmDistribution, on_delete=models.CASCADE, related_name='repositories',
    )
    repo_id = models.CharField(max_length=255, help_text="DNF/yum repo ID")
    name = models.CharField(max_length=500, help_text="Human-readable repository name")
    baseurl = models.TextField(blank=True, help_text="Base URL (blank for subscription repos)")
    mirrorlist = models.TextField(blank=True)
    metalink = models.TextField(blank=True)
    gpgcheck = models.BooleanField(default=True)
    enabled = models.BooleanField(
        default=False,
        help_text="Pre-selected by default when configuring a new build for this distribution",
    )
    source = models.CharField(max_length=50, choices=SOURCE_CHOICES, default='subscription')
    last_synced = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [('distribution', 'repo_id')]
        ordering = ['-enabled', 'source', 'name']
        verbose_name = 'RPM Build Repository'
        verbose_name_plural = 'RPM Build Repositories'

    def __str__(self):
        return f"{self.distribution.name} / {self.repo_id}"

    @property
    def has_url(self):
        return bool(self.baseurl or self.mirrorlist or self.metalink)

    def get_source_badge(self):
        return {
            'subscription': 'warning',
            'epel': 'info',
            'manual': 'secondary',
        }.get(self.source, 'secondary')
