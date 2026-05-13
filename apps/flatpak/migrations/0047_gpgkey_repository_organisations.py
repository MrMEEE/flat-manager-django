from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('flatpak', '0046_client_commit_outdated_count'),
    ]

    operations = [
        migrations.AddField(
            model_name='gpgkey',
            name='organisations',
            field=models.ManyToManyField(
                blank=True,
                related_name='gpg_keys',
                to='flatpak.organisation',
            ),
        ),
        migrations.AddField(
            model_name='repository',
            name='organisations',
            field=models.ManyToManyField(
                blank=True,
                related_name='repositories',
                to='flatpak.organisation',
            ),
        ),
    ]
