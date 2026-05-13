from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('rpm', '0013_rpmbuild_result_dir'),
        ('flatpak', '0047_gpgkey_repository_organisations'),
    ]

    operations = [
        migrations.AddField(
            model_name='rpmpackage',
            name='organisations',
            field=models.ManyToManyField(
                blank=True,
                related_name='rpm_packages',
                to='flatpak.organisation',
            ),
        ),
    ]
