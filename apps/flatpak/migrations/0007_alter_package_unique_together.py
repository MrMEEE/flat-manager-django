from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('flatpak', '0006_siteconfig_upstream_interval'),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name='package',
            unique_together={('repository', 'package_id', 'arch', 'branch', 'git_branch')},
        ),
    ]
