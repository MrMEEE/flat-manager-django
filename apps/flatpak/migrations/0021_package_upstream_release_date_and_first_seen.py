# Generated manually on 2026-05-18

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("flatpak", "0020_client_and_stale_hours"),
    ]

    operations = [
        migrations.AddField(
            model_name="package",
            name="upstream_release_date",
            field=models.DateField(
                blank=True,
                null=True,
                help_text="Release date of the current upstream version (from git tag date or version script)",
            ),
        ),
        migrations.AddField(
            model_name="package",
            name="upstream_version_first_seen_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text="When the current upstream_version was first detected by this system",
            ),
        ),
    ]
