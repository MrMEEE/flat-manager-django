from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('flatpak', '0025_bst_promotion'),
    ]

    operations = [
        migrations.AddField(
            model_name='siteconfig',
            name='bst_checkout_timeout_minutes',
            field=models.PositiveIntegerField(
                default=30,
                help_text="Maximum time (in minutes) allowed for 'bst artifact checkout' and the subsequent 'flatpak build-commit-from' import. Increase for large SDKs.",
            ),
        ),
    ]
