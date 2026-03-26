from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('flatpak', '0021_client_user_flatpaks'),
    ]

    operations = [
        migrations.AddField(
            model_name='package',
            name='build_type',
            field=models.CharField(
                choices=[('flatpak', 'Flatpak'), ('buildstream', 'BuildStream')],
                default='flatpak',
                help_text='Build system to use: Flatpak (flatpak-builder) or BuildStream (bst)',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='package',
            name='bst_element',
            field=models.CharField(
                blank=True,
                default='',
                help_text='BuildStream element to build (e.g. flatpak-release-repo.bst). '
                          'The element must produce an OSTree flatpak repo in its artifact.',
                max_length=255,
            ),
            preserve_default=False,
        ),
    ]
