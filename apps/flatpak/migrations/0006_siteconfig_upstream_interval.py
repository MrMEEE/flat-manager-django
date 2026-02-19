from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('flatpak', '0005_package_upstream_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='siteconfig',
            name='upstream_version_check_interval_hours',
            field=models.PositiveIntegerField(
                default=1,
                help_text='How often (in hours) to automatically check for new upstream versions. Set to 0 to disable.',
            ),
        ),
    ]
