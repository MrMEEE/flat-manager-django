from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('flatpak', '0049_client_tuxmigrate_facts'),
    ]

    operations = [
        migrations.AddField(
            model_name='package',
            name='version_scheme',
            field=models.CharField(
                blank=True,
                choices=[('', 'None / default'), ('odd-minor-is-unstable', 'Odd minor is unstable')],
                default='',
                help_text="Version scheme extracted from the Flatpak manifest's x-checker-data (auto-populated)",
                max_length=50,
            ),
        ),
        migrations.AddField(
            model_name='package',
            name='upstream_unstable_version',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Latest unstable upstream version per version_scheme (only set when newer than the stable upstream version; auto-populated)',
                max_length=100,
            ),
        ),
    ]
