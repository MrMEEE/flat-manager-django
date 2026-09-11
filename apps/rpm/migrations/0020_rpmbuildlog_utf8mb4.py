from django.db import migrations


def convert_rpmbuildlog_to_utf8mb4(apps, schema_editor):
    """ALTER TABLE only on MySQL/MariaDB -- no-op on SQLite and others."""
    if schema_editor.connection.vendor == "mysql":
        schema_editor.execute(
            "ALTER TABLE rpm_rpmbuildlog "
            "CONVERT TO CHARACTER SET utf8mb4 "
            "COLLATE utf8mb4_unicode_ci;"
        )


class Migration(migrations.Migration):
    """Convert RPM build logs to utf8mb4 so Unicode build output is preserved."""

    atomic = False

    dependencies = [
        ("rpm", "0019_alter_rpmrepository_baseurl"),
    ]

    operations = [
        migrations.RunPython(
            convert_rpmbuildlog_to_utf8mb4,
            reverse_code=migrations.RunPython.noop,
        ),
    ]