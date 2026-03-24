from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from accounts.models import User


class BankRequest(models.Model):
    class OperationalReason(models.TextChoices):
        MISSING_UPLOAD = "MISSING_UPLOAD", "Falta cargar recibos"
        RECEIPTS_MISSING = "RECEIPTS_MISSING", "Recibos en falta"
        OTHER = "OTHER", "Otro"

    class Priority(models.TextChoices):
        NORMAL = "NORMAL", "Normal"
        URGENT = "URGENT", "Urgente"

    class RequestType(models.TextChoices):
        PROVISORIO = "PROVISORIO", "Provisorio"
        FLUJO_CAJA = "FLUJO_CAJA", "Flujo de caja"
        OTRO = "OTRO", "Otro"

    class Status(models.TextChoices):
        REQUESTED = "REQUESTED", "Solicitado"
        IN_PROGRESS = "IN_PROGRESS", "En proceso"
        COMPLETED = "COMPLETED", "Realizado"
        ARCHIVED = "ARCHIVED", "Archivado"

    class DocumentStatus(models.TextChoices):
        PENDING = "PENDING", "Pendiente"
        LOADED = "LOADED", "Cargado"

    client = models.ForeignKey("clients.Client", on_delete=models.CASCADE, related_name="bank_requests")
    request_type = models.CharField(max_length=20, choices=RequestType.choices, default=RequestType.PROVISORIO, db_index=True)
    request_type_other = models.CharField(max_length=255, blank=True)
    operational_reason = models.CharField(
        max_length=30,
        choices=OperationalReason.choices,
        default=OperationalReason.MISSING_UPLOAD,
        db_index=True,
    )
    operational_reason_other = models.CharField(max_length=255, blank=True)
    request_priority = models.CharField(
        max_length=10,
        choices=Priority.choices,
        default=Priority.NORMAL,
        db_index=True,
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.REQUESTED, db_index=True)
    responsible = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_bank_requests",
        limit_choices_to={"role": User.Role.FUNCIONARIO},
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requested_bank_requests",
    )
    receipts_status = models.CharField(
        max_length=10,
        choices=DocumentStatus.choices,
        default=DocumentStatus.PENDING,
        db_index=True,
    )
    receipts_client_notified = models.BooleanField(default=False, db_index=True)
    receipts_notified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bank_requests_receipts_notified",
    )
    receipts_notified_at = models.DateTimeField(null=True, blank=True)
    invoices_status = models.CharField(
        max_length=10,
        choices=DocumentStatus.choices,
        default=DocumentStatus.PENDING,
        db_index=True,
    )
    receipts_loaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bank_requests_receipts_loaded",
    )
    receipts_loaded_at = models.DateTimeField(null=True, blank=True)
    invoices_loaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bank_requests_invoices_loaded",
    )
    invoices_loaded_at = models.DateTimeField(null=True, blank=True)
    started_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bank_requests_started",
    )
    started_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bank_requests_completed",
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    archived_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bank_requests_archived",
    )
    archived_at = models.DateTimeField(null=True, blank=True)
    last_note = models.TextField(blank=True)
    last_note_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bank_requests_last_noted",
    )
    last_note_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    receipts_pending_item = models.ForeignKey(
        "operations.PendingItem",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="receipt_bank_requests",
    )
    invoices_pending_item = models.ForeignKey(
        "operations.PendingItem",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoice_bank_requests",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "responsible"]),
            models.Index(fields=["client", "status"]),
            models.Index(fields=["receipts_status", "invoices_status"]),
        ]

    def clean(self):
        super().clean()
        if self.request_type == self.RequestType.OTRO and not self.request_type_other.strip():
            raise ValidationError({"request_type_other": "Debe especificar el tipo cuando selecciona 'Otro'."})
        if self.request_type != self.RequestType.OTRO and self.request_type_other:
            self.request_type_other = ""
        if self.operational_reason == self.OperationalReason.OTHER and not self.operational_reason_other.strip():
            raise ValidationError({"operational_reason_other": "Debe especificar el motivo cuando selecciona 'Otro'."})
        if self.operational_reason != self.OperationalReason.OTHER and self.operational_reason_other:
            self.operational_reason_other = ""
        if self.responsible_id and self.responsible and self.responsible.role != User.Role.FUNCIONARIO:
            raise ValidationError({"responsible": "El responsable debe tener rol FUNCIONARIO."})

    def __str__(self):
        return f"{self.client} - {self.get_request_type_display()} ({self.get_status_display()})"
