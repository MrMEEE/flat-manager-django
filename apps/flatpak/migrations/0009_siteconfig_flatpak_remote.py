from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("flatpak", "0008_buildlog_utf8mb4"),
    ]

    operations = [
        migrations.AddField(
            model_name="siteconfig",
            name="flatpak_remote_name",
            field=models.CharField(
                default="flathub",
                max_length=100,
                help_text="Name of the Flatpak remote used to install SDK/runtime dependencies (e.g. 'flathub')",
            ),
        ),
        migrations.AddField(
            model_name="siteconfig",
            name="flatpak_remote_url",
            field=models.URLField(
                default="https://dl.flathub.org/repo/flathub.flatpakrepo",
                max_length=500,
                help_text="URL of the Flatpak remote .flatpakrepo file. Used to register the remote automatically if it is not already present on the builder.",
            ),
        ),
    ]
