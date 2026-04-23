import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("rpm", "0006_refactor_signing_key_to_package"),
    ]

    operations = [
        migrations.AddField(
            model_name="rpmdistribution",
            name="repos_synced_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text="When build repositories were last synced for this distribution",
            ),
        ),
        migrations.CreateModel(
            name="RpmRepository",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "distribution",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="repositories",
                        to="rpm.rpmdistribution",
                    ),
                ),
                (
                    "repo_id",
                    models.CharField(
                        max_length=255,
                        help_text="DNF/yum repo ID",
                    ),
                ),
                (
                    "name",
                    models.CharField(
                        max_length=500,
                        help_text="Human-readable repository name",
                    ),
                ),
                (
                    "baseurl",
                    models.TextField(
                        blank=True,
                        help_text="Base URL (blank for subscription repos)",
                    ),
                ),
                (
                    "mirrorlist",
                    models.TextField(blank=True),
                ),
                (
                    "metalink",
                    models.TextField(blank=True),
                ),
                (
                    "gpgcheck",
                    models.BooleanField(default=True),
                ),
                (
                    "enabled",
                    models.BooleanField(
                        default=False,
                        help_text="Include this repository in mock builds for this distribution",
                    ),
                ),
                (
                    "source",
                    models.CharField(
                        choices=[
                            ("subscription", "RHSM Subscription"),
                            ("epel", "EPEL"),
                            ("manual", "Manual / Custom"),
                        ],
                        default="subscription",
                        max_length=50,
                    ),
                ),
                (
                    "last_synced",
                    models.DateTimeField(blank=True, null=True),
                ),
            ],
            options={
                "verbose_name": "RPM Build Repository",
                "verbose_name_plural": "RPM Build Repositories",
                "ordering": ["-enabled", "source", "name"],
                "unique_together": {("distribution", "repo_id")},
            },
        ),
    ]
