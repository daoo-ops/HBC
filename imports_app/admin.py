from django.contrib import admin

from imports_app.models import ImportBatch


@admin.register(ImportBatch)
class ImportBatchAdmin(admin.ModelAdmin):
    list_display = ("created_at", "status", "source_name", "created_by")
    list_filter = ("status",)
    search_fields = ("source_name", "created_by__username")
