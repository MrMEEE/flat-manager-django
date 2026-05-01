from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('rpm', '0012_rpmpackage_cleanup_on_success'),
    ]

    operations = [
        migrations.AddField(
            model_name='rpmbuild',
            name='result_dir',
            field=models.CharField(
                blank=True,
                max_length=2000,
                help_text='Path to the mock build result directory on disk',
            ),
        ),
    ]
