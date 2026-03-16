from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('flatpak', '0015_siteconfig_promotion_retry_interval'),
    ]

    operations = [
        migrations.AddField(
            model_name='package',
            name='upstream_version_script',
            field=models.TextField(
                blank=True,
                help_text=(
                    'Optional script to determine the latest upstream version. '
                    'Include a shebang line (#!/bin/bash or #!/usr/bin/env python3). '
                    'Print the version to stdout. '
                    'Falls back to git tag detection if empty or if the script fails.'
                ),
            ),
        ),
    ]
