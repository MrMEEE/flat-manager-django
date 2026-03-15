from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('flatpak', '0014_build_celery_task_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='siteconfig',
            name='promotion_retry_interval_minutes',
            field=models.PositiveIntegerField(
                default=1,
                help_text='How often (in minutes) to check for and retry pending promotions. Set to 0 to disable.',
            ),
        ),
    ]
