from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('flatpak', '0017_rename_repo_dirs_spaces_to_hyphens'),
    ]

    operations = [
        migrations.AddField(
            model_name='siteconfig',
            name='promotion_stale_timeout_minutes',
            field=models.PositiveIntegerField(
                default=10,
                help_text=(
                    'Minutes after which a pending or in-progress promotion is '
                    'considered stuck and marked as failed. Set to 0 to never expire.'
                ),
            ),
        ),
    ]
