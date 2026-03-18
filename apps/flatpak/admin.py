from django.contrib import admin
from .models import GPGKey, Repository, RepositorySubset, Package, Build, BuildArtifact, BuildLog, Token, SiteConfig, FlatpakRemote


@admin.register(GPGKey)
class GPGKeyAdmin(admin.ModelAdmin):
    list_display = ['name', 'email', 'key_id', 'fingerprint', 'is_active', 'created_by', 'created_at']
    list_filter = ['is_active', 'created_at']
    search_fields = ['name', 'email', 'key_id', 'fingerprint']
    readonly_fields = ['created_at', 'updated_at']
    exclude = ['private_key']  # Don't show private key in admin


@admin.register(Repository)
class RepositoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'collection_id', 'gpg_key', 'is_active', 'created_by', 'created_at']
    list_filter = ['is_active', 'created_at', 'gpg_key']
    search_fields = ['name', 'collection_id', 'description']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(RepositorySubset)
class RepositorySubsetAdmin(admin.ModelAdmin):
    list_display = ['repository', 'name', 'collection_id', 'base_url', 'created_at']
    list_filter = ['repository', 'created_at']
    search_fields = ['name', 'collection_id']
    readonly_fields = ['created_at']


@admin.register(Package)
class PackageAdmin(admin.ModelAdmin):
    list_display = ['package_name', 'package_id', 'repository', 'status', 'build_number', 'created_by', 'created_at']
    list_filter = ['status', 'repository', 'created_at']
    search_fields = ['package_id', 'package_name']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(Build)
class BuildAdmin(admin.ModelAdmin):
    list_display = ['package', 'build_number', 'version', 'status', 'started_at']
    list_filter = ['status', 'started_at']
    search_fields = ['package__package_id', 'package__package_name', 'version']
    readonly_fields = ['started_at', 'completed_at', 'published_at']


@admin.register(BuildArtifact)
class BuildArtifactAdmin(admin.ModelAdmin):
    list_display = ['build', 'filename', 'file_size', 'uploaded_at']
    list_filter = ['uploaded_at']
    search_fields = ['filename', 'build__package__package_id']
    readonly_fields = ['uploaded_at']


@admin.register(BuildLog)
class BuildLogAdmin(admin.ModelAdmin):
    list_display = ['build', 'level', 'timestamp']
    list_filter = ['level', 'timestamp']
    search_fields = ['build__package__package_id', 'message']
    readonly_fields = ['timestamp']


@admin.register(Token)
class TokenAdmin(admin.ModelAdmin):
    list_display = ['name', 'repository', 'token_type', 'is_active', 'created_at', 'expires_at']
    list_filter = ['token_type', 'is_active', 'created_at']
    search_fields = ['name', 'repository__name']
    readonly_fields = ['token', 'created_at']


@admin.register(SiteConfig)
class SiteConfigAdmin(admin.ModelAdmin):
    fieldsets = [
        ('Build Retention', {
            'fields': ['failed_builds_to_keep'],
        }),
        ('Upstream Version Checks', {
            'fields': ['upstream_version_check_interval_hours'],
        }),
        ('Available Version Scan', {
            'fields': ['available_version_check_interval_hours'],
        }),
        ('Promotions', {
            'fields': ['promotion_retry_interval_minutes', 'promotion_stale_timeout_minutes'],
        }),
    ]

    def has_add_permission(self, request):
        return not SiteConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(FlatpakRemote)
class FlatpakRemoteAdmin(admin.ModelAdmin):
    list_display = ['name', 'url', 'is_active', 'priority', 'created_at']
    list_editable = ['is_active', 'priority']
    search_fields = ['name', 'url']
