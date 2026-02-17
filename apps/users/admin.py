from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User, UserProfile, APIToken


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ['username', 'email', 'is_repo_admin', 'is_build_admin', 'is_staff', 'created_at']
    list_filter = ['is_repo_admin', 'is_build_admin', 'is_staff', 'is_superuser']
    fieldsets = BaseUserAdmin.fieldsets + (
        ('Permissions', {'fields': ('is_repo_admin', 'is_build_admin')}),
        ('Timestamps', {'fields': ('created_at', 'updated_at')}),
    )
    readonly_fields = ['created_at', 'updated_at']


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'organization', 'phone']
    search_fields = ['user__username', 'organization']


@admin.register(APIToken)
class APITokenAdmin(admin.ModelAdmin):
    list_display = ['user', 'name', 'created_at', 'last_used', 'is_active']
    list_filter = ['is_active', 'created_at']
    search_fields = ['user__username', 'name']
    readonly_fields = ['token', 'created_at', 'last_used']
