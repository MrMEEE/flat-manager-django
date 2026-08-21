from django.db import migrations


LEGACY_ROLE_TO_GROUP = {
    'admin': 'Admin',
    'superuser': 'Superuser',
    'build_admin': 'Build Admin',
    'repo_admin': 'Repo Admin',
    'view': 'View',
}


PREDEFINED_GROUPS = {
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
            ('rpms', 'read', True),
            ('rpms', 'build', True),
            ('repositories', 'read', True),
            ('flatpaks', 'read', True),
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
            ('clients', 'read', True),
            ('config', 'read', True),
        ],
    },
}


def migrate_legacy_roles_to_predefined_groups(apps, schema_editor):
    PermissionGroup = apps.get_model('users', 'PermissionGroup')
    PermissionGroupPermission = apps.get_model('users', 'PermissionGroupPermission')
    User = apps.get_model('users', 'User')
    UserRole = apps.get_model('users', 'UserRole')

    for name, metadata in PREDEFINED_GROUPS.items():
        group, _ = PermissionGroup.objects.get_or_create(name=name, organisation=None)
        group.description = metadata['description']
        group.is_predefined = True
        group.save(update_fields=['description', 'is_predefined'])

        existing = set(
            PermissionGroupPermission.objects.filter(group=group).values_list('resource', 'action')
        )
        for resource, action, granted in metadata['permissions']:
            if (resource, action) not in existing:
                PermissionGroupPermission.objects.create(
                    group=group,
                    organisation=None,
                    resource=resource,
                    action=action,
                    granted=granted,
                )

    for user in User.objects.all():
        target_names = set()
        for role in UserRole.objects.filter(user=user):
            target_name = LEGACY_ROLE_TO_GROUP.get(role.role)
            if target_name:
                target_names.add(target_name)

        if user.is_superuser and 'Superuser' not in target_names:
            target_names.add('Superuser')
        elif user.is_staff and 'Admin' not in target_names:
            target_names.add('Admin')

        for group_name in target_names:
            group = PermissionGroup.objects.filter(name=group_name, organisation=None).first()
            if group is not None:
                group.users.add(user)


def reverse_migrate_legacy_roles_to_predefined_groups(apps, schema_editor):
    PermissionGroup = apps.get_model('users', 'PermissionGroup')
    User = apps.get_model('users', 'User')

    for user in User.objects.all():
        for group in PermissionGroup.objects.filter(users=user, organisation=None):
            group.users.remove(user)


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0008_permissiongroup_is_predefined'),
    ]

    operations = [
        migrations.RunPython(
            migrate_legacy_roles_to_predefined_groups,
            reverse_code=reverse_migrate_legacy_roles_to_predefined_groups,
        ),
    ]
