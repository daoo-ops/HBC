from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from accounts.models import User
from clients.utils import extract_ruc_digit, normalize_ruc, normalize_ruc_base


class Client(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Activo"
        INACTIVE = "INACTIVE", "Inactivo"
        SUSPENDED = "SUSPENDED", "Suspendido"

    class Zone(models.TextChoices):
        SANTA_RITA = "SANTA_RITA", "Santa Rita"
        KM_32 = "KM_32", "KM 32"
        COMISIONES = "COMISIONES", "Misiones"
        KATUETE = "KATUETE", "Katuete"
        SUSPENDIDO = "SUSPENDIDO", "Suspendido"
        OTHER = "OTHER", "Otra"

    class InvoicePeriodStatus(models.TextChoices):
        PENDING = "PENDING", "Pendiente"
        RECEIVED = "RECEIVED", "Recibidas"

    name = models.CharField(max_length=255, db_index=True)
    ruc = models.CharField(max_length=32, blank=True)
    ruc_dv = models.CharField(max_length=4, blank=True)
    ruc_base = models.CharField(max_length=20, blank=True, db_index=True)
    phone = models.CharField(max_length=50, blank=True)
    address = models.CharField(max_length=255, blank=True)
    zone = models.CharField(max_length=20, choices=Zone.choices, default=Zone.SANTA_RITA, db_index=True)
    presentation_type = models.CharField(max_length=255, blank=True)
    due_date = models.DateField(null=True, blank=True, db_index=True)
    submission_date = models.DateField(null=True, blank=True)
    pending_notes = models.TextField(blank=True)
    observations = models.TextField(blank=True)
    monthly_amount_pyg = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    monthly_amount_usd = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    paid = models.BooleanField(default=False)
    debt_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    contract_until = models.DateField(null=True, blank=True)
    invoice_period_status = models.CharField(
        "Facturas del período",
        max_length=10,
        choices=InvoicePeriodStatus.choices,
        default=InvoicePeriodStatus.PENDING,
        db_index=True,
    )
    invoice_period_status_updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="clients_invoice_period_updated",
    )
    invoice_period_status_updated_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    responsible = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_clients",
        limit_choices_to={"role": "FUNCIONARIO"},
    )

    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["name"]),
            models.Index(fields=["ruc"]),
            models.Index(fields=["zone", "status"]),
            models.Index(fields=["due_date"]),
        ]
        ordering = ["name"]

    def save(self, *args, **kwargs):
        self.ruc = normalize_ruc(self.ruc)
        self.ruc_base = normalize_ruc_base(self.ruc)
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.responsible_id and self.responsible and self.responsible.role != User.Role.FUNCIONARIO:
            raise ValidationError({"responsible": "El responsable debe tener rol FUNCIONARIO."})

    def __str__(self):
        return f"{self.name} ({self.ruc or 'sin-ruc'})"

    @property
    def ruc_digit(self):
        if self.ruc_dv:
            return self.ruc_dv
        return extract_ruc_digit(self.ruc)


class ClientInvoicePeriodStatus(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pendiente"
        PARTIAL = "PARTIAL", "Parcial"
        RECEIVED = "RECEIVED", "Recibidas"

    client = models.ForeignKey("clients.Client", on_delete=models.CASCADE, related_name="invoice_period_records")
    year = models.PositiveSmallIntegerField(db_index=True)
    month = models.PositiveSmallIntegerField(db_index=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="client_invoice_period_updates",
    )
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-year", "-month", "client_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["client", "year", "month"],
                name="unique_client_invoice_period_status",
            )
        ]
        indexes = [
            models.Index(fields=["year", "month", "status"]),
        ]

    def __str__(self):
        return f"{self.client_id} {self.month:02d}/{self.year} {self.status}"


class ClientNote(models.Model):
    client = models.ForeignKey("clients.Client", on_delete=models.CASCADE, related_name="notes")
    note = models.TextField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="client_notes_created",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="client_notes_updated",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Nota {self.client_id} #{self.id}"


class ClientResponsibilityHistory(models.Model):
    client = models.ForeignKey("clients.Client", on_delete=models.CASCADE, related_name="responsibility_history")
    old_responsible = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="client_responsibility_old_records",
    )
    new_responsible = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="client_responsibility_new_records",
    )
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="client_responsibility_changes_made",
    )
    reason = models.CharField(max_length=255, blank=True)
    changed_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-changed_at"]
        indexes = [
            models.Index(fields=["client", "-changed_at"]),
        ]

    def __str__(self):
        return f"Responsable {self.client_id} {self.old_responsible_id}->{self.new_responsible_id}"


class Obligation(models.Model):
    code = models.CharField(max_length=80, unique=True)
    name = models.CharField(max_length=255, unique=True)
    tax_type = models.CharField(max_length=120, null=True, blank=True)
    form_code = models.CharField(max_length=32, null=True, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    uses_ruc_calendar = models.BooleanField(default=False)
    default_periodicity = models.CharField(max_length=20, blank=True, default="MONTHLY")
    default_due_mode = models.CharField(max_length=20, blank=True, default="AUTO")
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class ClientObligation(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Activa"
        INACTIVE = "INACTIVE", "Inactiva"

    class Periodicity(models.TextChoices):
        MONTHLY = "MONTHLY", "Mensual"
        QUARTERLY = "QUARTERLY", "Trimestral"
        ANNUAL = "ANNUAL", "Anual"
        OTHER = "OTHER", "Otra"

    class DueMode(models.TextChoices):
        AUTO = "AUTO", "Automático por RUC"
        MANUAL = "MANUAL", "Manual"

    client = models.ForeignKey("clients.Client", on_delete=models.CASCADE, related_name="client_obligations")
    obligation = models.ForeignKey(
        "clients.Obligation",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="client_obligations",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    periodicity = models.CharField(
        max_length=20,
        choices=Periodicity.choices,
        default=Periodicity.MONTHLY,
    )
    due_mode = models.CharField(max_length=20, choices=DueMode.choices, default=DueMode.AUTO)
    manual_due_day = models.PositiveSmallIntegerField(null=True, blank=True)
    needs_manual_review = models.BooleanField(default=False, db_index=True)
    source_presentation_type = models.CharField(max_length=255, blank=True)
    observations = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["obligation__name", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["client", "obligation"],
                condition=models.Q(obligation__isnull=False),
                name="unique_client_obligation_when_obligation_set",
            ),
        ]

    def __str__(self):
        if self.obligation_id:
            return f"{self.client} - {self.obligation}"
        return f"{self.client} - Revisar obligación"
