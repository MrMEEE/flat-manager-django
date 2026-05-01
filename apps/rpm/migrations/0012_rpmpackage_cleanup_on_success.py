from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('rpm', '0011_alter_rpmrepository_enabled'),
    ]

    operations = [
        migrations.AddField(
            model_name='rpmpackage',
            name='cleanup_on_success',
            field=models.BooleanField(
                default=True,
                help_text='Remove the mock chroot after a successful build (saves disk space). Disable to inspect the chroot after a successful build.',
            ),
        ),
    ]
