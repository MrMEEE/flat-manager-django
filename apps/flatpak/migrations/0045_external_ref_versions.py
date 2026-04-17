from django.db import migrations, models
import django.db.models.deletion


def backfill_external_ref_versions(apps, schema_editor):
    ExternalRef = apps.get_model('flatpak', 'ExternalRef')
    ExternalRefPromotion = apps.get_model('flatpak', 'ExternalRefPromotion')
    ExternalRefVersion = apps.get_model('flatpak', 'ExternalRefVersion')

    version_by_ext = {}

    for ext in ExternalRef.objects.exclude(commit_hash='').iterator():
        if ext.status == 'published':
            ver_status = 'published'
        elif ext.status in ('pulling', 'pulled', 'publishing'):
            ver_status = 'pulled'
        else:
            ver_status = 'failed'

        pulled_at = ext.last_pulled_at or ext.updated_at or ext.created_at
        source_published_at = ext.updated_at if ext.status == 'published' else None

        version, _ = ExternalRefVersion.objects.get_or_create(
            external_ref_id=ext.id,
            commit_hash=ext.commit_hash,
            defaults={
                'ref': ext.ref,
                'upstream_commit': ext.upstream_commit or '',
                'pulled_at': pulled_at,
                'source_published_at': source_published_at,
                'status': ver_status,
                'error_message': ext.error_message or '',
            },
        )
        version_by_ext.setdefault(ext.id, []).append(version)

    # Populate promotion.version pointers based on nearest available version.
    for promo in (
        ExternalRefPromotion.objects
        .filter(external_ref_version__isnull=True)
        .order_by('created_at')
        .iterator()
    ):
        versions = version_by_ext.get(promo.external_ref_id)
        if not versions:
            continue

        versions = sorted(
            versions,
            key=lambda v: (
                v.pulled_at is not None,
                v.pulled_at,
                v.id,
            ),
            reverse=True,
        )

        pivot = promo.completed_at or promo.created_at
        chosen = None
        if pivot is not None:
            for ver in versions:
                if ver.pulled_at and ver.pulled_at <= pivot:
                    chosen = ver
                    break
        if chosen is None:
            chosen = versions[0]

        promo.external_ref_version_id = chosen.id
        promo.save(update_fields=['external_ref_version'])


def noop_reverse(apps, schema_editor):
    # Intentionally keep version history on reverse.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('flatpak', '0044_client_machine_type'),
    ]

    operations = [
        migrations.CreateModel(
            name='ExternalRefVersion',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('ref', models.CharField(help_text='Ref string snapshot used for this version (e.g. runtime/org.kde.Platform/x86_64/49)', max_length=500)),
                ('commit_hash', models.CharField(help_text='Immutable commit hash identifying this external ref version', max_length=64)),
                ('upstream_commit', models.CharField(blank=True, help_text='Upstream commit observed when this version was pulled', max_length=64)),
                ('pulled_at', models.DateTimeField(auto_now_add=True)),
                ('source_published_at', models.DateTimeField(blank=True, help_text='When this version was published to external_ref.repository', null=True)),
                ('status', models.CharField(choices=[('pulled', 'Pulled'), ('published', 'Published to Source Repo'), ('failed', 'Failed')], default='pulled', max_length=20)),
                ('error_message', models.TextField(blank=True)),
                ('external_ref', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='versions', to='flatpak.externalref')),
            ],
            options={
                'ordering': ['-pulled_at'],
            },
        ),
        migrations.AddField(
            model_name='externalrefpromotion',
            name='external_ref_version',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='promotions', to='flatpak.externalrefversion'),
        ),
        migrations.AddConstraint(
            model_name='externalrefversion',
            constraint=models.UniqueConstraint(fields=('external_ref', 'commit_hash'), name='unique_external_ref_commit_hash'),
        ),
        migrations.RunPython(backfill_external_ref_versions, noop_reverse),
        migrations.RemoveConstraint(
            model_name='externalrefpromotion',
            name='unique_external_ref_target_repo',
        ),
        migrations.AddConstraint(
            model_name='externalrefpromotion',
            constraint=models.UniqueConstraint(fields=('external_ref_version', 'target_repo'), name='unique_external_ref_version_target_repo'),
        ),
    ]
