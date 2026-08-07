from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('flatpak', '0052_alter_package_upstream_unstable_version_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='client',
            name='bios_version',
            field=models.CharField(
                blank=True,
                default='',
                help_text="BIOS/firmware version reported by the client machine (e.g. '01.04.03').",
                max_length=100,
            ),
        ),
    ]
