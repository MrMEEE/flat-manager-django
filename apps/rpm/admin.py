from django.contrib import admin
from .models import RpmDistribution, RpmPackage, RpmBuild, RpmBuildLog, RpmRepository


@admin.register(RpmDistribution)
class RpmDistributionAdmin(admin.ModelAdmin):
    list_display = ['name', 'display_name', 'arch', 'rhel_version', 'is_active', 'repos_synced_at']
    list_filter = ['is_active', 'rhel_version']
    search_fields = ['name', 'display_name']


@admin.register(RpmRepository)
class RpmRepositoryAdmin(admin.ModelAdmin):
    list_display = ['distribution', 'repo_id', 'name', 'source', 'enabled', 'last_synced']
    list_filter = ['distribution', 'source', 'enabled']
    search_fields = ['repo_id', 'name']
    list_select_related = ['distribution']


@admin.register(RpmPackage)
class RpmPackageAdmin(admin.ModelAdmin):
    list_display = ['name', 'git_repo_url', 'git_branch', 'spec_file', 'status', 'created_at']
    list_filter = ['status']
    search_fields = ['name', 'git_repo_url']
    readonly_fields = ['created_at', 'updated_at', 'status']
    filter_horizontal = ['distributions']


@admin.register(RpmBuild)
class RpmBuildAdmin(admin.ModelAdmin):
    list_display = ['package', 'distribution', 'build_number', 'status', 'started_at', 'completed_at']
    list_filter = ['status', 'distribution']
    search_fields = ['package__name']
    readonly_fields = ['started_at', 'completed_at', 'celery_task_id']


@admin.register(RpmBuildLog)
class RpmBuildLogAdmin(admin.ModelAdmin):
    list_display = ['build', 'level', 'timestamp', 'message']
    list_filter = ['level']
    readonly_fields = ['timestamp']
