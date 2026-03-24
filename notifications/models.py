from django.conf import settings
from django.db import models


class UserNotification(models.Model):
    class Severity(models.TextChoices):
        URGENT = "URGENT", "Urgente"
        NORMAL = "NORMAL", "Normal"
        INFO = "INFO", "Informativa"

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sent_notifications",
    )
    client = models.ForeignKey(
        "clients.Client",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="notifications",
    )
    severity = models.CharField(max_length=10, choices=Severity.choices, default=Severity.NORMAL, db_index=True)
    message = models.CharField(max_length=255)
    target_url = models.CharField(max_length=255, blank=True)
    event_key = models.CharField(max_length=120, blank=True, db_index=True)
    source_ref = models.CharField(max_length=120, blank=True, db_index=True)
    is_read = models.BooleanField(default=False, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["is_read", "-created_at"]
        indexes = [
            models.Index(fields=["recipient", "is_read", "-created_at"]),
            models.Index(fields=["recipient", "event_key", "source_ref", "-created_at"]),
        ]

    def __str__(self):
        return f"Notif {self.recipient_id} {self.severity}: {self.message[:40]}"
