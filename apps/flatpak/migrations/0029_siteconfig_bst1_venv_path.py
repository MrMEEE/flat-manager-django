from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('flatpak', '0028_buildstreamsource_bst_version'),
    ]

    operations = [
        migrations.AddField(
            model_name='siteconfig',
            name='bst1_venv_path',
            field=models.CharField(
                blank=True,
                default='/opt/flat-manager/bst1-venv',
                help_text=(
                    "Path to the virtualenv root that has BuildStream 1 installed "
                    "(e.g. /opt/flat-manager/bst1-venv). "
                    "BST 2 is available as 'bst' in the primary virtualenv. "
                    "Leave empty to fall back to the value in settings.py."
                ),
                max_length=500,
            ),
        ),
    ]
