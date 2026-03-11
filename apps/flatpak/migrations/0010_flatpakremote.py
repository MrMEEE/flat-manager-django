from django.db import migrations, models


def migrate_remotes_from_siteconfig(apps, schema_editor):
    """Move flatpak_remote_name/url from SiteConfig into FlatpakRemote rows."""
    SiteConfig = apps.get_model('flatpak', 'SiteConfig')
    FlatpakRemote = apps.get_model('flatpak', 'FlatpakRemote')

    created_names = set()
    for cfg in SiteConfig.objects.all():
        name = (cfg.flatpak_remote_name or 'flathub').strip()
        url = (cfg.flatpak_remote_url or 'https://dl.flathub.org/repo/flathub.flatpakrepo').strip()
        if name and name not in created_names:
            FlatpakRemote.objects.get_or_create(name=name, defaults={'url': url, 'is_active': True, 'priority': 0})
            created_names.add(name)

    # Always ensure flathub exists as a sensible default
    if 'flathub' not in created_names:
        FlatpakRemote.objects.get_or_create(
            name='flathub',
            defaults={
                'url': 'https://dl.flathub.org/repo/flathub.flatpakrepo',
                'is_active': True,
                'priority': 0,
            }
        )


class Migration(migrations.Migration):

    dependencies = [
        ('flatpak', '0009_siteconfig_flatpak_remote'),
    ]

    operations = [
        # 1. Create the FlatpakRemote table
        migrations.CreateModel(
            name='FlatpakRemote',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(
                    max_length=100,
                    unique=True,
                    help_text="Remote name as known to flatpak (e.g. 'flathub')",
                )),
                ('url', models.URLField(
                    max_length=500,
                    help_text='URL of the .flatpakrepo file used to register the remote if not already present',
                )),
                ('is_active', models.BooleanField(
                    default=True,
                    help_text='Only active remotes are used during builds',
                )),
                ('priority', models.PositiveIntegerField(
                    default=0,
                    help_text='Remotes with a lower number are tried first (0 = highest priority)',
                )),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'verbose_name': 'Flatpak Remote',
                'verbose_name_plural': 'Flatpak Remotes',
                'ordering': ['priority', 'name'],
            },
        ),
        # 2. Migrate existing SiteConfig data
        migrations.RunPython(
            migrate_remotes_from_siteconfig,
            reverse_code=migrations.RunPython.noop,
        ),
        # 3. Remove old fields from SiteConfig
        migrations.RemoveField(model_name='siteconfig', name='flatpak_remote_name'),
        migrations.RemoveField(model_name='siteconfig', name='flatpak_remote_url'),
    ]
