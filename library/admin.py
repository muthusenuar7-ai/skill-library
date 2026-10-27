from django.contrib import admin

from .models import ActivityLog, Category, SecurityAuditLog, Skill, SkillAttachment


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "display_order", "is_active")


@admin.register(Skill)
class SkillAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "status", "is_featured", "updated_at")
    list_filter = ("status", "category")
    search_fields = ("title", "slug", "tags")

    def has_delete_permission(self, request, obj=None):
        return False  # archive instead of deleting


admin.site.register(SkillAttachment)


@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "actor_email", "action", "target_type", "description")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SecurityAuditLog)
class SecurityAuditLogAdmin(admin.ModelAdmin):
    """Read-only: the clearing audit trail cannot be added to, edited or deleted from any screen."""
    list_display = ("created_at", "actor_email", "action", "records_affected", "protected_kept", "detail")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
