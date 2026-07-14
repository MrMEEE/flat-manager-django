from django.db import migrations, models


def classify_existing_ubi_repos(apps, schema_editor):
    RpmRepository = apps.get_model('rpm', 'RpmRepository')
    for repo in RpmRepository.objects.all().iterator():
        baseurl = (repo.baseurl or '').lower()
        mirrorlist = (repo.mirrorlist or '').lower()
        metalink = (repo.metalink or '').lower()
        if 'cdn-ubi.redhat.com/' in baseurl or 'cdn-ubi.redhat.com/' in mirrorlist or 'cdn-ubi.redhat.com/' in metalink:
            if repo.source != 'ubi':
                repo.source = 'ubi'
                repo.save(update_fields=['source'])


class Migration(migrations.Migration):

    dependencies = [
        ('rpm', '0015_alter_rpmrepository_source'),
    ]

    operations = [
        migrations.AlterField(
            model_name='rpmrepository',
            name='source',
            field=models.CharField(choices=[('rhsm', 'RHSM Subscription'), ('ubi', 'UBI'), ('satellite', 'Satellite / Katello'), ('epel', 'EPEL'), ('third_party', 'Third Party / Custom')], default='rhsm', max_length=50),
        ),
        migrations.RunPython(classify_existing_ubi_repos, migrations.RunPython.noop),
    ]