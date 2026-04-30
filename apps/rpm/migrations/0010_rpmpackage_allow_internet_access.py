from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('rpm', '0009_rpmpackage_default_repos'),
    ]

    operations = [
        migrations.AddField(
            model_name='rpmpackage',
            name='allow_internet_access',
            field=models.BooleanField(
                default=False,
                help_text='Allow mock builds for this package to access the internet.',
            ),
        ),
    ]