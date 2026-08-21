import base64
import hashlib

from django.conf import settings
from django.contrib.auth.base_user import BaseUserManager
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

# Canonical permission vocabulary for explicit resource/action authorization.
RESOURCE_REPOSITORIES = 'repositories'
RESOURCE_GPG_KEYS = 'gpg_keys'
RESOURCE_FLATPAKS = 'flatpaks'
RESOURCE_RPMS = 'rpms'
RESOURCE_BUILDS = 'builds'
RESOURCE_BUILDSTREAMS = 'buildstreams'
RESOURCE_EXTERNALS = 'externals'
RESOURCE_DEPENDENCIES = 'dependencies'
RESOURCE_CLIENTS = 'clients'
RESOURCE_USERS = 'users'
RESOURCE_CONFIG = 'config'
RESOURCE_API = 'api'

RESOURCE_CHOICES = [
    (RESOURCE_REPOSITORIES, 'Repositories'),
    (RESOURCE_GPG_KEYS, 'GPG Keys'),
    (RESOURCE_FLATPAKS, 'Flatpaks'),
    (RESOURCE_RPMS, 'RPMs'),
    (RESOURCE_BUILDS, 'Builds'),
    (RESOURCE_BUILDSTREAMS, 'Build Streams'),
    (RESOURCE_EXTERNALS, 'Externals'),
    (RESOURCE_DEPENDENCIES, 'Dependencies'),
    (RESOURCE_CLIENTS, 'Clients'),
    (RESOURCE_USERS, 'Users'),
    (RESOURCE_CONFIG, 'Config'),
    (RESOURCE_API, 'API'),
]

ACTION_READ = 'read'
ACTION_CREATE = 'create'
ACTION_UPDATE = 'update'
ACTION_DELETE = 'delete'
ACTION_BUILD = 'build'
ACTION_PUBLISH = 'publish'
ACTION_SYNC = 'sync'
ACTION_MANAGE_USERS = 'manage_users'
ACTION_ADMIN = 'admin'
ACTION_ALL = 'all'

ACTION_CHOICES = [
    (ACTION_READ, 'Read'),
    (ACTION_CREATE, 'Create'),
    (ACTION_UPDATE, 'Update'),
    (ACTION_DELETE, 'Delete'),
    (ACTION_BUILD, 'Build'),
    (ACTION_PUBLISH, 'Publish'),
    (ACTION_SYNC, 'Sync'),
    (ACTION_MANAGE_USERS, 'Manage Users'),
    (ACTION_ADMIN, 'Admin'),
    (ACTION_ALL, 'All'),
]

RESOURCE_ACTIONS = {
    RESOURCE_REPOSITORIES: [(ACTION_READ, 'Read'), (ACTION_CREATE, 'Create'), (ACTION_UPDATE, 'Update'), (ACTION_DELETE, 'Delete'), (ACTION_SYNC, 'Sync'), (ACTION_ALL, 'All')],
    RESOURCE_GPG_KEYS: [(ACTION_READ, 'Read'), (ACTION_CREATE, 'Create'), (ACTION_UPDATE, 'Update'), (ACTION_DELETE, 'Delete'), (ACTION_ALL, 'All')],
    RESOURCE_FLATPAKS: [(ACTION_READ, 'Read'), (ACTION_CREATE, 'Create'), (ACTION_UPDATE, 'Update'), (ACTION_DELETE, 'Delete'), (ACTION_PUBLISH, 'Publish'), (ACTION_ALL, 'All')],
    RESOURCE_RPMS: [(ACTION_READ, 'Read'), (ACTION_CREATE, 'Create'), (ACTION_UPDATE, 'Update'), (ACTION_DELETE, 'Delete'), (ACTION_BUILD, 'Build'), (ACTION_ALL, 'All')],
    RESOURCE_BUILDS: [(ACTION_READ, 'Read'), (ACTION_BUILD, 'Build'), (ACTION_PUBLISH, 'Publish'), (ACTION_ALL, 'All')],
    RESOURCE_BUILDSTREAMS: [(ACTION_READ, 'Read'), (ACTION_CREATE, 'Create'), (ACTION_UPDATE, 'Update'), (ACTION_DELETE, 'Delete'), (ACTION_BUILD, 'Build'), (ACTION_PUBLISH, 'Publish'), (ACTION_ALL, 'All')],
    RESOURCE_EXTERNALS: [(ACTION_READ, 'Read'), (ACTION_CREATE, 'Create'), (ACTION_UPDATE, 'Update'), (ACTION_DELETE, 'Delete'), (ACTION_PUBLISH, 'Publish'), (ACTION_SYNC, 'Sync'), (ACTION_ALL, 'All')],
    RESOURCE_DEPENDENCIES: [(ACTION_READ, 'Read'), (ACTION_CREATE, 'Create'), (ACTION_UPDATE, 'Update'), (ACTION_DELETE, 'Delete'), (ACTION_SYNC, 'Sync'), (ACTION_ALL, 'All')],
    RESOURCE_CLIENTS: [(ACTION_READ, 'Read'), (ACTION_UPDATE, 'Update'), (ACTION_DELETE, 'Delete'), (ACTION_ALL, 'All')],
    RESOURCE_USERS: [(ACTION_READ, 'Read'), (ACTION_CREATE, 'Create'), (ACTION_UPDATE, 'Update'), (ACTION_DELETE, 'Delete'), (ACTION_MANAGE_USERS, 'Manage Users'), (ACTION_ALL, 'All')],
    RESOURCE_CONFIG: [(ACTION_READ, 'Read'), (ACTION_UPDATE, 'Update'), (ACTION_ALL, 'All')],
    RESOURCE_API: [(ACTION_READ, 'Read'), (ACTION_ADMIN, 'Admin'), (ACTION_ALL, 'All')],
}


def get_action_choices_for_resource(resource):
    return RESOURCE_ACTIONS.get(resource, [(ACTION_READ, 'Read'), (ACTION_ALL, 'All')])


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
# User manager
# ---------------------------------------------------------------------------

class UserManager(BaseUserManager):
    def create_user(self, username, email=None, password=None, **extra_fields):
        if not username:
            raise ValueError('The username field must be set')
        email = self.normalize_email(email)
        user = self.model(username=username, email=email, **extra_fields)
        user.set_password(password)
        user.is_local = extra_fields.get('is_local', True)
        user.save(using=self._db)
        return user

    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_local', True)

        if not extra_fields.get('is_staff'):
            raise ValueError('Superuser must have is_staff=True.')
        if not extra_fields.get('is_superuser'):
            raise ValueError('Superuser must have is_superuser=True.')

        user = self.create_user(username=username, email=email, password=password, **extra_fields)

        PermissionGroup.ensure_predefined_groups()
        admin_group = PermissionGroup.objects.filter(name='Admin', organisation__isnull=True).first()
        if admin_group is not None:
            admin_group.users.add(user)

        return user


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------

class User(AbstractUser):
    """
    Custom User model for flat-manager.
    Extends Django's AbstractUser with additional fields.
    """
    email = models.EmailField(blank=True)
    objects = UserManager()

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
        """Return True if the user has any of the given roles globally or in the org.

        Legacy compatibility helper retained only for migration and LDAP bridging.
        It is not used by the canonical authorization model.
        """
        user_roles = self.roles.filter(role__in=roles)
        if organisation is not None:
            user_roles = user_roles.filter(
                models.Q(organisation=organisation) | models.Q(organisation__isnull=True)
            )
        return user_roles.exists()

    def is_admin(self):
        return self.is_superuser or self.has_permission('users', 'manage_users')

    def can_manage_users(self):
        """Permission-group-based user management check only."""
        return self.has_permission('users', 'manage_users')

    def can_view_resource(self, resource, organisation=None):
        return self.has_permission(resource, ACTION_READ, organisation=organisation)

    def can_create_resource(self, resource, organisation=None):
        return self.has_permission(resource, ACTION_CREATE, organisation=organisation)

    def can_update_resource(self, resource, organisation=None):
        return self.has_permission(resource, ACTION_UPDATE, organisation=organisation)

    def can_delete_resource(self, resource, organisation=None):
        return self.has_permission(resource, ACTION_DELETE, organisation=organisation)

    def can_build_resource(self, resource, organisation=None):
        return self.has_permission(resource, ACTION_BUILD, organisation=organisation)

    def can_publish_resource(self, resource, organisation=None):
        return self.has_permission(resource, ACTION_PUBLISH, organisation=organisation)

    def can_sync_resource(self, resource, organisation=None):
        return self.has_permission(resource, ACTION_SYNC, organisation=organisation)

    def can_view_repositories(self, organisation=None):
        return self.can_view_resource(RESOURCE_REPOSITORIES, organisation=organisation)

    def can_create_repositories(self, organisation=None):
        return self.can_create_resource(RESOURCE_REPOSITORIES, organisation=organisation)

    def can_update_repositories(self, organisation=None):
        return self.can_update_resource(RESOURCE_REPOSITORIES, organisation=organisation)

    def can_delete_repositories(self, organisation=None):
        return self.can_delete_resource(RESOURCE_REPOSITORIES, organisation=organisation)

    def can_view_gpg_keys(self, organisation=None):
        return self.can_view_resource(RESOURCE_GPG_KEYS, organisation=organisation)

    def can_create_gpg_keys(self, organisation=None):
        return self.can_create_resource(RESOURCE_GPG_KEYS, organisation=organisation)

    def can_update_gpg_keys(self, organisation=None):
        return self.can_update_resource(RESOURCE_GPG_KEYS, organisation=organisation)

    def can_delete_gpg_keys(self, organisation=None):
        return self.can_delete_resource(RESOURCE_GPG_KEYS, organisation=organisation)

    def can_view_flatpaks(self, organisation=None):
        return self.can_view_resource(RESOURCE_FLATPAKS, organisation=organisation)

    def can_create_flatpaks(self, organisation=None):
        return self.can_create_resource(RESOURCE_FLATPAKS, organisation=organisation)

    def can_update_flatpaks(self, organisation=None):
        return self.can_update_resource(RESOURCE_FLATPAKS, organisation=organisation)

    def can_delete_flatpaks(self, organisation=None):
        return self.can_delete_resource(RESOURCE_FLATPAKS, organisation=organisation)

    def can_build_flatpaks(self, organisation=None):
        return self.can_build_resource(RESOURCE_FLATPAKS, organisation=organisation)

    def can_publish_flatpaks(self, organisation=None):
        return self.can_publish_resource(RESOURCE_FLATPAKS, organisation=organisation)

    def can_view_rpms(self, organisation=None):
        return self.can_view_resource(RESOURCE_RPMS, organisation=organisation)

    def can_view_builds(self, organisation=None):
        return self.can_view_resource(RESOURCE_BUILDS, organisation=organisation)

    def can_build_builds(self, organisation=None):
        return self.can_build_resource(RESOURCE_BUILDS, organisation=organisation)

    def can_publish_builds(self, organisation=None):
        return self.can_publish_resource(RESOURCE_BUILDS, organisation=organisation)

    def can_view_buildstreams(self, organisation=None):
        return self.can_view_resource(RESOURCE_BUILDSTREAMS, organisation=organisation)

    def can_create_buildstreams(self, organisation=None):
        return self.can_create_resource(RESOURCE_BUILDSTREAMS, organisation=organisation)

    def can_update_buildstreams(self, organisation=None):
        return self.can_update_resource(RESOURCE_BUILDSTREAMS, organisation=organisation)

    def can_delete_buildstreams(self, organisation=None):
        return self.can_delete_resource(RESOURCE_BUILDSTREAMS, organisation=organisation)

    def can_build_buildstreams(self, organisation=None):
        return self.can_build_resource(RESOURCE_BUILDSTREAMS, organisation=organisation)

    def can_publish_buildstreams(self, organisation=None):
        return self.can_publish_resource(RESOURCE_BUILDSTREAMS, organisation=organisation)

    def can_view_externals(self, organisation=None):
        return self.can_view_resource(RESOURCE_EXTERNALS, organisation=organisation)

    def can_create_externals(self, organisation=None):
        return self.can_create_resource(RESOURCE_EXTERNALS, organisation=organisation)

    def can_update_externals(self, organisation=None):
        return self.can_update_resource(RESOURCE_EXTERNALS, organisation=organisation)

    def can_delete_externals(self, organisation=None):
        return self.can_delete_resource(RESOURCE_EXTERNALS, organisation=organisation)

    def can_publish_externals(self, organisation=None):
        return self.can_publish_resource(RESOURCE_EXTERNALS, organisation=organisation)

    def can_sync_externals(self, organisation=None):
        return self.can_sync_resource(RESOURCE_EXTERNALS, organisation=organisation)

    def can_view_clients(self, organisation=None):
        return self.can_view_resource(RESOURCE_CLIENTS, organisation=organisation)

    def can_update_clients(self, organisation=None):
        return self.can_update_resource(RESOURCE_CLIENTS, organisation=organisation)

    def can_delete_clients(self, organisation=None):
        return self.can_delete_resource(RESOURCE_CLIENTS, organisation=organisation)

    def can_view_config(self, organisation=None):
        return self.can_view_resource(RESOURCE_CONFIG, organisation=organisation)

    def can_update_config(self, organisation=None):
        return self.can_update_resource(RESOURCE_CONFIG, organisation=organisation)

    def can_view_api(self, organisation=None):
        return self.can_view_resource(RESOURCE_API, organisation=organisation)

    def can_build(self, organisation=None):
        return self.has_permission('builds', 'build', organisation=organisation) or self.has_permission('rpms', 'build', organisation=organisation)

    def can_repo_admin(self, organisation=None):
        return self.has_permission('repositories', 'admin', organisation=organisation) or self.has_permission('repositories', 'update', organisation=organisation) or self.has_permission('repositories', 'delete', organisation=organisation)

    def can_write(self, organisation=None):
        return (
            self.has_permission('repositories', 'create', organisation=organisation)
            or self.has_permission('repositories', 'update', organisation=organisation)
            or self.has_permission('repositories', 'delete', organisation=organisation)
            or self.has_permission('gpg_keys', 'create', organisation=organisation)
            or self.has_permission('gpg_keys', 'update', organisation=organisation)
            or self.has_permission('gpg_keys', 'delete', organisation=organisation)
            or self.has_permission('flatpaks', 'create', organisation=organisation)
            or self.has_permission('flatpaks', 'update', organisation=organisation)
            or self.has_permission('flatpaks', 'delete', organisation=organisation)
            or self.has_permission('config', 'update', organisation=organisation)
        )

    def has_permission(self, resource, action, organisation=None):
        """Return True if the user has an explicit grant or a group grant.

        Legacy roles are intentionally not used here; they are a migration-only bridge.
        """
        if self.is_superuser:
            return True

        action_filter = models.Q(action=action)
        if action != ACTION_ALL:
            action_filter |= models.Q(action=ACTION_ALL)

        qs = self.permission_grants.filter(resource=resource, granted=True).filter(action_filter)
        if organisation is not None:
            qs = qs.filter(models.Q(organisation=organisation) | models.Q(organisation__isnull=True))
        else:
            qs = qs.filter(organisation__isnull=True)
        if qs.exists():
            return True

        group_qs = PermissionGroupPermission.objects.filter(
            group__users=self,
            resource=resource,
            granted=True,
        ).filter(action_filter)
        if organisation is not None:
            group_qs = group_qs.filter(models.Q(organisation=organisation) | models.Q(organisation__isnull=True))
        else:
            group_qs = group_qs.filter(organisation__isnull=True)
        if group_qs.exists():
            return True

        return False


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


def _legacy_role_to_predefined_group_name(role_name: str):
    mapping = {
        'admin': 'Admin',
        'superuser': 'Superuser',
        'build_admin': 'Build Admin',
        'repo_admin': 'Repo Admin',
        'view': 'View',
    }
    return mapping.get(role_name)


class PermissionGrant(models.Model):
    """Explicit resource/action grant, optionally scoped to a single organisation."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='permission_grants')
    organisation = models.ForeignKey(
        'flatpak.Organisation',
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='permission_grants',
        help_text="Leave blank for a global permission. Set to restrict to a specific organisation.",
    )
    resource = models.CharField(max_length=50, choices=RESOURCE_CHOICES)
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    granted = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [['user', 'organisation', 'resource', 'action']]
        ordering = ['user', 'resource', 'action']
        verbose_name = 'Permission Grant'
        verbose_name_plural = 'Permission Grants'

    def __str__(self):
        scope = f' [{self.organisation}]' if self.organisation else ' [global]'
        return f'{self.user.username} → {self.resource}:{self.action}{scope}'


class PermissionGroup(models.Model):
    """Reusable collection of permission grants that can be assigned to users."""
    PREDEFINED_NAMES = {
        'Admin': {
            'description': 'Full access to the platform including user management.',
            'permissions': [
                ('repositories', 'read', True),
                ('repositories', 'create', True),
                ('repositories', 'update', True),
                ('repositories', 'delete', True),
                ('gpg_keys', 'read', True),
                ('gpg_keys', 'create', True),
                ('gpg_keys', 'update', True),
                ('gpg_keys', 'delete', True),
                ('flatpaks', 'read', True),
                ('flatpaks', 'create', True),
                ('flatpaks', 'update', True),
                ('flatpaks', 'delete', True),
                ('rpms', 'read', True),
                ('rpms', 'build', True),
                ('builds', 'read', True),
                ('builds', 'build', True),
                ('buildstreams', 'read', True),
                ('buildstreams', 'create', True),
                ('buildstreams', 'update', True),
                ('buildstreams', 'delete', True),
                ('buildstreams', 'build', True),
                ('buildstreams', 'publish', True),
                ('externals', 'read', True),
                ('externals', 'create', True),
                ('externals', 'update', True),
                ('externals', 'delete', True),
                ('externals', 'publish', True),
                ('externals', 'sync', True),
                ('dependencies', 'read', True),
                ('dependencies', 'create', True),
                ('dependencies', 'update', True),
                ('dependencies', 'delete', True),
                ('dependencies', 'sync', True),
                ('clients', 'read', True),
                ('users', 'read', True),
                ('users', 'manage_users', True),
                ('config', 'read', True),
                ('config', 'update', True),
                ('api', 'read', True),
            ],
        },
        'Superuser': {
            'description': 'Full access in all areas except managing users.',
            'permissions': [
                ('repositories', 'read', True),
                ('repositories', 'create', True),
                ('repositories', 'update', True),
                ('repositories', 'delete', True),
                ('gpg_keys', 'read', True),
                ('gpg_keys', 'create', True),
                ('gpg_keys', 'update', True),
                ('gpg_keys', 'delete', True),
                ('flatpaks', 'read', True),
                ('flatpaks', 'create', True),
                ('flatpaks', 'update', True),
                ('flatpaks', 'delete', True),
                ('rpms', 'read', True),
                ('rpms', 'build', True),
                ('builds', 'read', True),
                ('builds', 'build', True),
                ('buildstreams', 'read', True),
                ('buildstreams', 'create', True),
                ('buildstreams', 'update', True),
                ('buildstreams', 'delete', True),
                ('buildstreams', 'build', True),
                ('buildstreams', 'publish', True),
                ('externals', 'read', True),
                ('externals', 'create', True),
                ('externals', 'update', True),
                ('externals', 'delete', True),
                ('externals', 'publish', True),
                ('externals', 'sync', True),
                ('dependencies', 'read', True),
                ('dependencies', 'create', True),
                ('dependencies', 'update', True),
                ('dependencies', 'delete', True),
                ('dependencies', 'sync', True),
                ('clients', 'read', True),
                ('config', 'read', True),
                ('config', 'update', True),
                ('api', 'read', True),
            ],
        },
        'Build Admin': {
            'description': 'Can build and manage package operations.',
            'permissions': [
                ('builds', 'read', True),
                ('builds', 'build', True),
                ('buildstreams', 'read', True),
                ('buildstreams', 'build', True),
                ('rpms', 'read', True),
                ('rpms', 'build', True),
                ('repositories', 'read', True),
                ('flatpaks', 'read', True),
                ('externals', 'read', True),
                ('dependencies', 'read', True),
                ('dependencies', 'sync', True),
            ],
        },
        'Repo Admin': {
            'description': 'Can manage repositories and publish content.',
            'permissions': [
                ('repositories', 'read', True),
                ('repositories', 'create', True),
                ('repositories', 'update', True),
                ('repositories', 'delete', True),
                ('gpg_keys', 'read', True),
                ('gpg_keys', 'create', True),
                ('gpg_keys', 'update', True),
                ('gpg_keys', 'delete', True),
                ('flatpaks', 'read', True),
                ('flatpaks', 'publish', True),
                ('externals', 'read', True),
                ('externals', 'publish', True),
                ('dependencies', 'read', True),
                ('dependencies', 'sync', True),
            ],
        },
        'View': {
            'description': 'Read-only access across common resources.',
            'permissions': [
                ('repositories', 'read', True),
                ('gpg_keys', 'read', True),
                ('flatpaks', 'read', True),
                ('rpms', 'read', True),
                ('builds', 'read', True),
                ('buildstreams', 'read', True),
                ('externals', 'read', True),
                ('dependencies', 'read', True),
                ('clients', 'read', True),
                ('config', 'read', True),
            ],
        },
    }

    name = models.CharField(max_length=100, help_text="Name of the permission group")
    description = models.TextField(blank=True, help_text="Optional description for the group")
    organisation = models.ForeignKey(
        'flatpak.Organisation',
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='permission_groups',
        help_text="Leave blank for a global group. Set to restrict the group to a specific organisation.",
    )
    users = models.ManyToManyField(User, related_name='permission_groups', blank=True)
    is_predefined = models.BooleanField(default=False, help_text="System-managed predefined group")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['organisation__name', 'name']
        verbose_name = 'Permission Group'
        verbose_name_plural = 'Permission Groups'

    def __str__(self):
        scope = f' [{self.organisation}]' if self.organisation else ' [global]'
        return f'{self.name}{scope}'

    @classmethod
    def sync_predefined_group_assignments(cls):
        cls.ensure_predefined_groups()
        for user in User.objects.all():
            assigned_names = set(user.permission_groups.filter(organisation__isnull=True).values_list('name', flat=True))
            for role in user.roles.all():
                target_name = _legacy_role_to_predefined_group_name(role.role)
                if not target_name:
                    continue
                group = cls.objects.filter(name=target_name, organisation__isnull=True).first()
                if group is not None:
                    user.permission_groups.add(group)
                    assigned_names.add(group.name)
            for group in user.permission_groups.filter(organisation__isnull=True):
                if group.name in {'Admin', 'Superuser', 'Build Admin', 'Repo Admin', 'View'}:
                    if group.name == 'Superuser' and user.is_superuser:
                        continue
                    if group.name == 'Admin' and user.is_staff and user.is_superuser:
                        continue

    @classmethod
    def ensure_predefined_groups(cls):
        groups = []
        for name, details in cls.PREDEFINED_NAMES.items():
            group, created = cls.objects.get_or_create(name=name, organisation=None)
            group.description = details['description']
            group.is_predefined = True
            group.save(update_fields=['description', 'is_predefined'])
            existing = set(group.permissions.values_list('resource', 'action'))
            for resource, action, granted in details['permissions']:
                if (resource, action) not in existing:
                    PermissionGroupPermission.objects.create(
                        group=group,
                        organisation=None,
                        resource=resource,
                        action=action,
                        granted=granted,
                    )
            groups.append(group)
        return groups

    def is_editable(self):
        return not self.is_predefined

    def can_edit(self):
        return self.is_editable()

    def can_delete(self):
        return self.is_editable()


class PermissionGroupPermission(models.Model):
    """Permission grant owned by a group rather than an individual user."""
    group = models.ForeignKey(PermissionGroup, on_delete=models.CASCADE, related_name='permissions')
    organisation = models.ForeignKey(
        'flatpak.Organisation',
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='group_permission_grants',
        help_text="Leave blank for a global permission. Set to restrict to a specific organisation.",
    )
    resource = models.CharField(max_length=50, choices=RESOURCE_CHOICES)
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    granted = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [['group', 'organisation', 'resource', 'action']]
        ordering = ['group', 'resource', 'action']
        verbose_name = 'Permission Group Permission'
        verbose_name_plural = 'Permission Group Permissions'

    def __str__(self):
        scope = f' [{self.organisation}]' if self.organisation else ' [global]'
        return f'{self.group.name} → {self.resource}:{self.action}{scope}'


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
