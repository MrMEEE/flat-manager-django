from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('flatpak', '0050_package_version_scheme'),
    ]

    operations = [
        migrations.AddField(
            model_name='externalref',
            name='dependencies',
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text='Resolved dependency snapshot from upstream metadata (direct + transitive refs)',
            ),
        ),
    ]
