from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('flatpak', '0013_siteconfig_stale_build_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='build',
            name='celery_task_id',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Celery task ID — used to revoke/terminate the task on cancel',
                max_length=255,
            ),
            preserve_default=False,
        ),
    ]
