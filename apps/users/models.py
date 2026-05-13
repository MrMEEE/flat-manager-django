import base64
import hashlib

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models


# ---------------------------------------------------------------------------
# Role constants
# ---------------------------------------------------------------------------

ROLE_ADMIN       = 'admin'
ROLE_SUPERUSER   = 'superuser'
ROLE_BUILD_ADMIN = 'build_admin'
ROLE_REPO_ADMIN  = 'repo_admin'
ROLE_VIEW        = 'view'

ROLE_CHOICES = [
    (ROLE_ADMIN,       'Admin'),
    (ROLE_SUPERUSER,   'Superuser'),
    (ROLE_BUILD_ADMIN, 'Build Admin'),
    (ROLE_REPO_ADMIN,  'Repo Admin'),
    (ROLE_VIEW,        'View'),
]

# Roles that grant write access globally (no org restriction needed)
GLOBAL_WRITE_ROLES = {ROLE_ADMIN, ROLE_SUPERUSER}


# ---------------------------------------------------------------------------
# Encryption helper (reuses the same Fernet key as satellite.py)
# ---------------------------------------------------------------------------

def _fernet():
    from cryptography.fernet import Fernet
    key_bytes = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key_bytes))


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode()).decode()


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------

class User(AbstractUser):
    """
    Custom User model for flat-manager.
    Extends Django's AbstractUser with additional fields.
    """
    email = models.EmailField(unique=True)
    # is_local=True → only Django password auth is tried (never LDAP).
    # Set automatically for manually created users; cleared for LDAP-provisioned users.
    is_local = models.BooleanField(
        default=True,
        help_text="Local account: uses Django password auth only. LDAP-provisioned users have this unset.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'User'
        verbose_name_plural = 'Users'

    def __str__(self):
        return self.username

    # ------------------------------------------------------------------
    # Role helpers
    # ------------------------------------------------------------------

    def get_roles(self, organisation=None):
        """Return a set of role strings for this user (optionally filtered by org)."""
        qs = self.roles.all()
        if organisation is not None:
            qs = qs.filter(organisation=organisation)
        return {r.role for r in qs}

    def has_role(self, *roles, organisation=None):
        """Return True if the user has any of the given roles globally or in the org."""
        user_roles = self.roles.filter(role__in=roles)
        if organisation is not None:
            user_roles = user_roles.filter(
                models.Q(organisation=organisation) | models.Q(organisation__isnull=True)
            )
        return user_roles.exists()

    def is_admin(self):
        return self.is_superuser or self.roles.filter(role=ROLE_ADMIN).exists()

    def can_manage_users(self):
        """Admin role only — Superuser cannot manage users."""
        return self.is_superuser or self.roles.filter(role=ROLE_ADMIN).exists()

    def can_build(self, organisation=None):
        return self.has_role(ROLE_ADMIN, ROLE_SUPERUSER, ROLE_BUILD_ADMIN, organisation=organisation)

    def can_repo_admin(self, organisation=None):
        return self.has_role(ROLE_ADMIN, ROLE_SUPERUSER, ROLE_REPO_ADMIN, organisation=organisation)

    def can_write(self, organisation=None):
        return self.has_role(ROLE_ADMIN, ROLE_SUPERUSER, ROLE_BUILD_ADMIN, ROLE_REPO_ADMIN, organisation=organisation)


# ---------------------------------------------------------------------------
# UserRole  (user ↔ role ↔ optional organisation)
# ---------------------------------------------------------------------------

class UserRole(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='roles')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    # NULL organisation = global scope
    organisation = models.ForeignKey(
        'flatpak.Organisation',
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='user_roles',
        help_text="Leave blank for a global role. Set to restrict to a specific organisation.",
    )

    class Meta:
        unique_together = [['user', 'role', 'organisation']]
        ordering = ['user', 'role']
        verbose_name = 'User Role'
        verbose_name_plural = 'User Roles'

    def __str__(self):
        org_str = f' [{self.organisation}]' if self.organisation else ' [global]'
        return f'{self.user.username} → {self.get_role_display()}{org_str}'


# ---------------------------------------------------------------------------
# LDAPSource
# ---------------------------------------------------------------------------

class LDAPSource(models.Model):
    PROTOCOL_LDAP  = 'ldap'
    PROTOCOL_LDAPS = 'ldaps'
    PROTOCOL_CHOICES = [
        (PROTOCOL_LDAP,  'LDAP (plaintext / STARTTLS)'),
        (PROTOCOL_LDAPS, 'LDAPS (TLS from the start)'),
    ]

    SERVER_TYPE_AD    = 'ad'
    SERVER_TYPE_IPA   = 'ipa'
    SERVER_TYPE_POSIX = 'posix'
    SERVER_TYPE_CHOICES = [
        (SERVER_TYPE_AD,    'Active Directory'),
        (SERVER_TYPE_IPA,   'FreeIPA'),
        (SERVER_TYPE_POSIX, 'POSIX / OpenLDAP'),
    ]

    GROUP_MEMBER_POSIX = 'posix'
    GROUP_MEMBER_AD    = 'ad'
    GROUP_MEMBER_CHOICES = [
        (GROUP_MEMBER_POSIX, 'POSIX (memberUid)'),
        (GROUP_MEMBER_AD,    'Active Directory (member / memberOf)'),
    ]

    # ── Server ──────────────────────────────────────────────────────────────
    name             = models.CharField(max_length=255, unique=True, help_text="Human-readable name for this LDAP source")
    hostname         = models.CharField(max_length=255, help_text="LDAP server hostname or IP")
    port             = models.PositiveIntegerField(default=389, help_text="TCP port (389 for LDAP, 636 for LDAPS)")
    protocol         = models.CharField(max_length=5, choices=PROTOCOL_CHOICES, default=PROTOCOL_LDAP)
    verify_certs     = models.BooleanField(default=True, help_text="Verify TLS certificate (disable only for testing)")
    server_type      = models.CharField(max_length=10, choices=SERVER_TYPE_CHOICES, default=SERVER_TYPE_AD)

    # ── Bind / Search ───────────────────────────────────────────────────────
    bind_dn          = models.CharField(max_length=512, help_text="Service account DN (e.g. CN=svc,DC=example,DC=com)")
    bind_password_encrypted = models.TextField(blank=True, help_text="Fernet-encrypted bind password")
    base_dn          = models.CharField(max_length=512, help_text="User search base DN")
    group_base_dn    = models.CharField(max_length=512, blank=True, help_text="Group search base DN (leave blank to use base_dn)")
    group_membership = models.CharField(max_length=10, choices=GROUP_MEMBER_CHOICES, default=GROUP_MEMBER_AD)
    ldap_filter      = models.CharField(max_length=512, blank=True, default='(objectClass=person)',
                                        help_text="Extra LDAP filter applied when searching for the user")

    # ── Attribute mapping ────────────────────────────────────────────────────
    attr_username    = models.CharField(
        max_length=64, default='sAMAccountName',
        help_text="LDAP attribute used as the login / username (e.g. sAMAccountName, uid, userPrincipalName)")
    attr_first_name  = models.CharField(
        max_length=64, default='givenName',
        help_text="LDAP attribute mapped to first name (e.g. givenName)")
    attr_last_name   = models.CharField(
        max_length=64, default='sn',
        help_text="LDAP attribute mapped to last name (e.g. sn)")
    attr_email       = models.CharField(
        max_length=64, default='mail',
        help_text="LDAP attribute mapped to email address (e.g. mail)")

    is_active        = models.BooleanField(default=True, help_text="Disable to skip this source during login")
    created_at       = models.DateTimeField(auto_now_add=True)
    updated_at       = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'LDAP Source'
        verbose_name_plural = 'LDAP Sources'

    def __str__(self):
        return f'{self.name} ({self.hostname})'

    # ── Password helpers ────────────────────────────────────────────────────

    def set_bind_password(self, plaintext: str):
        self.bind_password_encrypted = encrypt_secret(plaintext)

    def get_bind_password(self) -> str:
        if not self.bind_password_encrypted:
            return ''
        return decrypt_secret(self.bind_password_encrypted)

    @property
    def ldap_url(self) -> str:
        return f'{self.protocol}://{self.hostname}:{self.port}'


# ---------------------------------------------------------------------------
# LDAPGroupMapping
# ---------------------------------------------------------------------------

class LDAPGroupMapping(models.Model):
    """Map an LDAP group DN → flat-manager role (optionally scoped to an org)."""
    source       = models.ForeignKey(LDAPSource, on_delete=models.CASCADE, related_name='group_mappings')
    ldap_group_dn = models.CharField(max_length=512, help_text="Full DN of the LDAP group")
    role         = models.CharField(max_length=20, choices=ROLE_CHOICES, help_text="Role to grant")
    organisation = models.ForeignKey(
        'flatpak.Organisation',
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='ldap_group_mappings',
        help_text="Organisation scope (blank = global)",
    )

    class Meta:
        unique_together = [['source', 'ldap_group_dn', 'role', 'organisation']]
        ordering = ['source', 'ldap_group_dn']
        verbose_name = 'LDAP Group Mapping'
        verbose_name_plural = 'LDAP Group Mappings'

    def __str__(self):
        org_str = f' [{self.organisation}]' if self.organisation else ' [global]'
        return f'{self.source.name}: {self.ldap_group_dn} → {self.get_role_display()}{org_str}'


# ---------------------------------------------------------------------------
# UserProfile  (unchanged)
# ---------------------------------------------------------------------------

class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    bio = models.TextField(blank=True)
    avatar = models.ImageField(upload_to='avatars/', blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True)
    organization = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return f"{self.user.username}'s profile"


# ---------------------------------------------------------------------------
# APIToken  (unchanged)
# ---------------------------------------------------------------------------

class APIToken(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='api_tokens')
    name = models.CharField(max_length=100, help_text="Token name/description")
    token = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.username} - {self.name}"
