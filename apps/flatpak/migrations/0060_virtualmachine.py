import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('flatpak', '0059_alter_token_token'),
    ]

    operations = [
        migrations.CreateModel(
            name='VirtualMachine',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('username', models.CharField(default='system', help_text="Owning account: 'system' for qemu:///system VMs, else the Linux username (qemu:///session).", max_length=255)),
                ('name', models.CharField(max_length=255)),
                ('os_name', models.CharField(blank=True, default='', help_text='Selected OS, derived from libosinfo metadata in the domain XML.', max_length=255)),
                ('memory_mb', models.IntegerField(default=0)),
                ('disks', models.JSONField(default=list, help_text='[{target, source, capacity_bytes}, ...] — provisioned (virtual) size per disk.')),
                ('status', models.CharField(choices=[('running', 'Running'), ('stopped', 'Stopped')], default='stopped', max_length=10)),
                ('uptime_seconds', models.IntegerField(blank=True, help_text="Seconds since the VM's qemu process started; null when stopped.", null=True)),
                ('state_changed_at', models.DateTimeField(blank=True, help_text="When this VM's running/stopped status last changed, as first observed by the server.", null=True)),
                ('last_seen_at', models.DateTimeField(auto_now=True)),
                ('client', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='virtual_machines', to='flatpak.client')),
            ],
            options={
                'verbose_name': 'Virtual Machine',
                'verbose_name_plural': 'Virtual Machines',
                'ordering': ['username', 'name'],
                'unique_together': {('client', 'username', 'name')},
            },
        ),
    ]
