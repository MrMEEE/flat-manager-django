from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('rpm', '0007_add_rpm_repository'),
    ]

    operations = [
        migrations.AddField(
            model_name='rpmbuild',
            name='selected_repos',
            field=models.ManyToManyField(
                blank=True,
                help_text='Repositories enabled for this specific build run',
                related_name='builds',
                to='rpm.rpmrepository',
            ),
        ),
    ]
