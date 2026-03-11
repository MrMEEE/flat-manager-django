from django.db import migrations


def convert_buildlog_to_utf8mb4(apps, schema_editor):
    """ALTER TABLE only on MySQL/MariaDB — no-op on SQLite and others."""
    if schema_editor.connection.vendor == "mysql":
        schema_editor.execute(
            "ALTER TABLE flatpak_buildlog "
            "CONVERT TO CHARACTER SET utf8mb4 "
            "COLLATE utf8mb4_unicode_ci;"
        )


class Migration(migrations.Migration):
    """Convert flatpak_buildlog.message (and the whole table) to utf8mb4.

    Without utf8mb4 the column rejects Unicode characters outside the ASCII
    range when the database / table was created with latin1 or the MySQL-legacy
    'utf8' (utf8mb3) charset.  Build-tool output routinely contains symbols
    such as ✗ (U+2717) that trigger an 'Incorrect string value' error on INSERT.

    This migration is a no-op on non-MySQL backends.
    """

    dependencies = [
        ("flatpak", "0007_alter_package_unique_together"),
    ]

    operations = [
        migrations.RunPython(
            convert_buildlog_to_utf8mb4,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
