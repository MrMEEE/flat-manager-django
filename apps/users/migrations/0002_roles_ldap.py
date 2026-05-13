"""
Migration: add is_local to User, drop is_repo_admin / is_build_admin,
add UserRole, LDAPSource, LDAPGroupMapping.

Data migration converts existing flag-based permissions to the new UserRole
model before the old columns are removed.
"""
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def migrate_old_permissions(apps, schema_editor):
    """Convert is_repo_admin / is_build_admin flags to UserRole entries."""
    User = apps.get_model('users', 'User')
    UserRole = apps.get_model('users', 'UserRole')

    for user in User.objects.all():
        # All existing users are local (password-auth)
        user.is_local = True
        user.save(update_fields=['is_local'])

        # Django superuser / staff → admin role
        if user.is_superuser or user.is_staff:
            UserRole.objects.get_or_create(user=user, role='admin', organisation=None)
        else:
            if getattr(user, 'is_repo_admin', False):
                UserRole.objects.get_or_create(user=user, role='repo_admin', organisation=None)
            if getattr(user, 'is_build_admin', False):
                UserRole.objects.get_or_create(user=user, role='build_admin', organisation=None)


def reverse_migrate(apps, schema_editor):
    """Reverse: restore is_repo_admin / is_build_admin from UserRole entries."""
    User = apps.get_model('users', 'User')
    UserRole = apps.get_model('users', 'UserRole')

    for role in UserRole.objects.all():
        user = role.user
        if role.role in ('admin', 'superuser'):
            user.is_repo_admin = True
            user.is_build_admin = True
        elif role.role == 'repo_admin':
            user.is_repo_admin = True
        elif role.role == 'build_admin':
            user.is_build_admin = True
        user.save(update_fields=['is_repo_admin', 'is_build_admin'])


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0001_initial'),
        ('flatpak', '0046_client_commit_outdated_count'),
    ]

    operations = [
        # ── 1. Add is_local to User ──────────────────────────────────────────
        migrations.AddField(
            model_name='user',
            name='is_local',
            field=models.BooleanField(
                default=True,
                help_text=(
                    "Local account: uses Django password auth only. "
                    "LDAP-provisioned users have this unset."
                ),
            ),
        ),

        # ── 2. Create LDAPSource ─────────────────────────────────────────────
        migrations.CreateModel(
            name='LDAPSource',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255, unique=True, help_text='Human-readable name for this LDAP source')),
                ('hostname', models.CharField(max_length=255, help_text='LDAP server hostname or IP')),
                ('port', models.PositiveIntegerField(default=389, help_text='TCP port (389 for LDAP, 636 for LDAPS)')),
                ('protocol', models.CharField(
                    max_length=5,
                    choices=[('ldap', 'LDAP (plaintext / STARTTLS)'), ('ldaps', 'LDAPS (TLS from the start)')],
                    default='ldap',
                )),
                ('verify_certs', models.BooleanField(default=True, help_text='Verify TLS certificate (disable only for testing)')),
                ('server_type', models.CharField(
                    max_length=10,
                    choices=[('ad', 'Active Directory'), ('ipa', 'FreeIPA'), ('posix', 'POSIX / OpenLDAP')],
                    default='ad',
                )),
                ('bind_dn', models.CharField(max_length=512, help_text='Service account DN')),
                ('bind_password_encrypted', models.TextField(blank=True, help_text='Fernet-encrypted bind password')),
                ('base_dn', models.CharField(max_length=512, help_text='User search base DN')),
                ('group_base_dn', models.CharField(max_length=512, blank=True, help_text='Group search base DN')),
                ('group_membership', models.CharField(
                    max_length=10,
                    choices=[('posix', 'POSIX (memberUid)'), ('ad', 'Active Directory (member / memberOf)')],
                    default='ad',
                )),
                ('ldap_filter', models.CharField(
                    max_length=512, blank=True, default='(objectClass=person)',
                    help_text='Extra LDAP filter applied when searching for the user',
                )),
                ('is_active', models.BooleanField(default=True, help_text='Disable to skip this source during login')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'LDAP Source',
                'verbose_name_plural': 'LDAP Sources',
                'ordering': ['name'],
            },
        ),

        # ── 3. Create UserRole ───────────────────────────────────────────────
        migrations.CreateModel(
            name='UserRole',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('role', models.CharField(
                    max_length=20,
                    choices=[
                        ('admin', 'Admin'),
                        ('superuser', 'Superuser'),
                        ('build_admin', 'Build Admin'),
                        ('repo_admin', 'Repo Admin'),
                        ('view', 'View'),
                    ],
                )),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='roles',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('organisation', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='user_roles',
                    to='flatpak.organisation',
                    help_text='Organisation scope (blank = global)',
                )),
            ],
            options={
                'verbose_name': 'User Role',
                'verbose_name_plural': 'User Roles',
                'ordering': ['user', 'role'],
                'unique_together': {('user', 'role', 'organisation')},
            },
        ),

        # ── 4. Create LDAPGroupMapping ───────────────────────────────────────
        migrations.CreateModel(
            name='LDAPGroupMapping',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('ldap_group_dn', models.CharField(max_length=512, help_text='Full DN of the LDAP group')),
                ('role', models.CharField(
                    max_length=20,
                    choices=[
                        ('admin', 'Admin'),
                        ('superuser', 'Superuser'),
                        ('build_admin', 'Build Admin'),
                        ('repo_admin', 'Repo Admin'),
                        ('view', 'View'),
                    ],
                    help_text='Role to grant',
                )),
                ('source', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='group_mappings',
                    to='users.ldapsource',
                )),
                ('organisation', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='ldap_group_mappings',
                    to='flatpak.organisation',
                    help_text='Organisation scope (blank = global)',
                )),
            ],
            options={
                'verbose_name': 'LDAP Group Mapping',
                'verbose_name_plural': 'LDAP Group Mappings',
                'ordering': ['source', 'ldap_group_dn'],
                'unique_together': {('source', 'ldap_group_dn', 'role', 'organisation')},
            },
        ),

        # ── 5. Data migration: is_local + role conversion ────────────────────
        migrations.RunPython(migrate_old_permissions, reverse_code=reverse_migrate),

        # ── 6. Remove old permission flags from User ─────────────────────────
        migrations.RemoveField(model_name='user', name='is_repo_admin'),
        migrations.RemoveField(model_name='user', name='is_build_admin'),
    ]
