from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('rpm', '0008_rpmbuild_selected_repos'),
    ]

    operations = [
        migrations.AddField(
            model_name='rpmpackage',
            name='default_repos',
            field=models.ManyToManyField(
                blank=True,
                help_text='Repositories pre-selected by default when building this package',
                related_name='default_for_packages',
                to='rpm.rpmrepository',
            ),
        ),
    ]
