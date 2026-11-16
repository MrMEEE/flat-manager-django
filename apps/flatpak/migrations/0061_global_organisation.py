from django.db import migrations


def create_global_organisation(apps, schema_editor):
    Organisation = apps.get_model('flatpak', 'Organisation')
    Organisation.objects.get_or_create(
        name='Global',
        defaults={
            'responsible_name': 'Flat Manager',
            'responsible_email': 'global@localhost',
            'description': 'Shared ownership for resources without an assigned organisation.',
        },
    )


class Migration(migrations.Migration):

    dependencies = [
        ('flatpak', '0060_virtualmachine'),
    ]

    operations = [
        migrations.RunPython(create_global_organisation, migrations.RunPython.noop),
    ]