from django.contrib import admin

from auditing.models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "action", "entity", "entity_id", "actor")
    list_filter = ("action", "entity")
    search_fields = ("entity", "entity_id", "actor__username")
