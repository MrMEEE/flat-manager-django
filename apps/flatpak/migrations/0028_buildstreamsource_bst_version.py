from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('flatpak', '0027_add_produced_refs_to_bst_source'),
    ]

    operations = [
        migrations.AddField(
            model_name='buildstreamsource',
            name='bst_version',
            field=models.CharField(
                choices=[('bst2', 'BuildStream 2'), ('bst1', 'BuildStream 1 (legacy)')],
                default='bst2',
                help_text='BuildStream major version. BST 1 and BST 2 have incompatible project.conf formats.',
                max_length=10,
            ),
        ),
    ]
