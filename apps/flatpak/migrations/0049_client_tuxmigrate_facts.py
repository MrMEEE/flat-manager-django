from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('flatpak', '0048_package_upstream_release_date_and_first_seen'),
    ]

    operations = [
        migrations.AddField(
            model_name='client',
            name='tuxmigrate_facts',
            field=models.JSONField(
                default=list,
                help_text=(
                    'TuxMigrate ansible facts from /etc/ansible/facts.d/: '
                    '[{version, version_major, version_minor, version_patch, order, '
                    'playbook, applied, applied_date}, ...].'
                ),
            ),
        ),
        migrations.AddField(
            model_name='client',
            name='tuxmigrate_latest_version',
            field=models.CharField(
                blank=True,
                default='',
                max_length=32,
                help_text="Highest TuxMigrate version applied on this client, e.g. '1.0.44'.",
            ),
        ),
    ]
