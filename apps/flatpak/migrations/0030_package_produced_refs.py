from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('flatpak', '0029_siteconfig_bst1_venv_path'),
    ]

    operations = [
        migrations.AddField(
            model_name='package',
            name='produced_refs',
            field=models.TextField(blank=True, default='', help_text='Newline-separated OSTree refs produced by the last successful build.'),
        ),
    ]
