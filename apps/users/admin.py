from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .mixins import apply_default_organisation
from .models import User, UserProfile, APIToken, LDAPSource, LDAPGroupMapping


class DefaultOrganisationAdminMixin:
    def save_model(self, request, obj, form, change):
        if not change and hasattr(obj, 'created_by_id'):
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        if not change:
            apply_default_organisation(form.instance, request.user)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ['username', 'email', 'is_local', 'is_staff', 'is_active', 'created_at']
    list_filter = ['is_local', 'is_staff', 'is_superuser', 'is_active']
    fieldsets = BaseUserAdmin.fieldsets + (
        ('flat-manager', {'fields': ('is_local',)}),
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


class LDAPGroupMappingInline(admin.TabularInline):
    model = LDAPGroupMapping
    extra = 0


@admin.register(LDAPSource)
class LDAPSourceAdmin(admin.ModelAdmin):
    list_display = ['name', 'hostname', 'port', 'server_type', 'is_active']
    list_filter = ['server_type', 'is_active']
    inlines = [LDAPGroupMappingInline]
    readonly_fields = ['created_at', 'updated_at']
    # bind_password_encrypted is intentionally excluded from fieldsets for safety
    exclude = ['bind_password_encrypted']
