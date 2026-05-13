from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0002_roles_ldap'),
    ]

    operations = [
        migrations.AddField(
            model_name='ldapsource',
            name='attr_username',
            field=models.CharField(
                default='sAMAccountName',
                max_length=64,
                help_text='LDAP attribute used as the login / username (e.g. sAMAccountName, uid, userPrincipalName)',
            ),
        ),
        migrations.AddField(
            model_name='ldapsource',
            name='attr_first_name',
            field=models.CharField(
                default='givenName',
                max_length=64,
                help_text='LDAP attribute mapped to first name (e.g. givenName)',
            ),
        ),
        migrations.AddField(
            model_name='ldapsource',
            name='attr_last_name',
            field=models.CharField(
                default='sn',
                max_length=64,
                help_text='LDAP attribute mapped to last name (e.g. sn)',
            ),
        ),
        migrations.AddField(
            model_name='ldapsource',
            name='attr_email',
            field=models.CharField(
                default='mail',
                max_length=64,
                help_text='LDAP attribute mapped to email address (e.g. mail)',
            ),
        ),
    ]
