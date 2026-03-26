from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('flatpak', '0022_package_build_type_bst_element'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # 1. Remove build_type and bst_element from Package
        migrations.RemoveField(
            model_name='package',
            name='build_type',
        ),
        migrations.RemoveField(
            model_name='package',
            name='bst_element',
        ),

        # 2. Create BuildStreamSource
        migrations.CreateModel(
            name='BuildStreamSource',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(help_text='Display name for this BuildStream source (e.g. freedesktop-sdk)', max_length=255)),
                ('git_repo_url', models.URLField(help_text='Git repository containing the BuildStream project (must have a project.conf)')),
                ('git_branch', models.CharField(default='master', help_text='Git branch or tag to build from', max_length=100)),
                ('bst_element', models.CharField(help_text='BuildStream element to build (e.g. flatpak-release-repo.bst). The element must produce an OSTree flatpak repo in its artifact.', max_length=255)),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('building', 'Building'), ('built', 'Built'), ('committing', 'Committing'), ('committed', 'Committed'), ('publishing', 'Publishing'), ('published', 'Published'), ('failed', 'Failed'), ('cancelled', 'Cancelled')], default='pending', max_length=20)),
                ('build_number', models.IntegerField(default=1)),
                ('version', models.CharField(blank=True, max_length=255)),
                ('source_commit', models.CharField(blank=True, max_length=255)),
                ('error_message', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('repository', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='bst_sources', to='flatpak.repository')),
                ('created_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='bst_sources', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'BuildStream Source',
                'verbose_name_plural': 'BuildStream Sources',
                'ordering': ['-created_at'],
            },
        ),

        # 3. Make Build.package nullable and add Build.bst_source FK
        migrations.AlterField(
            model_name='build',
            name='package',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='builds',
                to='flatpak.package',
            ),
        ),
        migrations.AddField(
            model_name='build',
            name='bst_source',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='builds',
                to='flatpak.buildstreamsource',
            ),
        ),
    ]
