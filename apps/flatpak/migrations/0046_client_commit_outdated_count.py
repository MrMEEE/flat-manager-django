from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('flatpak', '0045_external_ref_versions'),
    ]

    operations = [
        migrations.AddField(
            model_name='client',
            name='commit_outdated_count',
            field=models.IntegerField(
                default=0,
                help_text="Managed flatpaks whose deployed commit differs from the server's published commit.",
            ),
        ),
    ]
