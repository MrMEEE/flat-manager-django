from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('flatpak', '0002_package_commit_hash_package_dependencies_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='SiteConfig',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('failed_builds_to_keep', models.PositiveIntegerField(
                    default=1,
                    help_text='Number of failed builds to keep per package (oldest are deleted automatically)'
                )),
            ],
            options={
                'verbose_name': 'Site Configuration',
            },
        ),
    ]
