from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('rpm', '0016_rpmrepository_add_ubi_source'),
    ]

    operations = [
        migrations.AddField(
            model_name='rpmrepository',
            name='gpgkey',
            field=models.TextField(
                blank=True,
                help_text='GPG key URL(s) for this repository (space-separated if multiple), as provided by the repo metadata',
                default='',
            ),
            preserve_default=False,
        ),
    ]
