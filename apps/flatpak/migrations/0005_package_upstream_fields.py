from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('flatpak', '0004_promotion_promotion_unique_build_target_repo'),
    ]

    operations = [
        migrations.AddField(
            model_name='package',
            name='upstream_url',
            field=models.URLField(
                blank=True,
                help_text='Upstream git repository URL to watch for new tags (e.g. https://github.com/user/repo)',
            ),
        ),
        migrations.AddField(
            model_name='package',
            name='upstream_version',
            field=models.CharField(
                blank=True,
                max_length=100,
                help_text='Latest upstream version tag (auto-fetched)',
            ),
        ),
        migrations.AddField(
            model_name='package',
            name='upstream_checked_at',
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text='When the upstream version was last checked',
            ),
        ),
    ]
