from django.conf import settings
from django.db import models


class ImportBatch(models.Model):
    class Status(models.TextChoices):
        PREVIEW = "PREVIEW", "Preview"
        COMMITTED = "COMMITTED", "Committed"
        FAILED = "FAILED", "Failed"

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="import_batches",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PREVIEW)
    source_name = models.CharField(max_length=255, blank=True)
    summary = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
