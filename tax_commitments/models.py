import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class TaxCommitment(models.Model):
    class CommitmentType(models.TextChoices):
        IRE = "IRE", "Pago de IRE"
        IDU = "IDU", "Pago de IDU"
        FACILIDAD = "FACILIDAD", "Pago de facilidad"
        ANTICIPO = "ANTICIPO", "Anticipo"
        MAQUILA_F107 = "MAQUILA_F107", "Tributo Único Maquila (F107)"
        OTHER = "OTHER", "Otro"

    class Currency(models.TextChoices):
        PYG = "PYG", "Guaraní"
        USD = "USD", "Dólar"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pendiente"
        NOTIFIED = "NOTIFIED", "Avisado"
        PAID = "PAID", "Pagado"
        ARCHIVED = "ARCHIVED", "Archivado"

    class Source(models.TextChoices):
        MANUAL = "MANUAL", "Manual"
        ACCOUNT_STATEMENT_IMPORT = "ACCOUNT_STATEMENT_IMPORT", "Importado desde estado de cuenta"

    class InstallmentMode(models.TextChoices):
        AUTO = "AUTO", "Automático"
        MANUAL = "MANUAL", "Manual"

    client = models.ForeignKey("clients.Client", on_delete=models.CASCADE, related_name="tax_commitments")
    commitment_type = models.CharField(max_length=20, choices=CommitmentType.choices, db_index=True)
    type_other = models.CharField(max_length=120, blank=True)
    reference_number = models.CharField(max_length=120, blank=True)
    period_reference = models.CharField(max_length=120, blank=True)
    installment_group_id = models.UUIDField(null=True, blank=True, db_index=True)
    installment_number = models.PositiveSmallIntegerField(null=True, blank=True, db_index=True)
    installment_total = models.PositiveSmallIntegerField(null=True, blank=True)
    installment_mode = models.CharField(
        max_length=10,
        choices=InstallmentMode.choices,
        default=InstallmentMode.AUTO,
        db_index=True,
    )
    due_date = models.DateField(db_index=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    currency = models.CharField(max_length=3, choices=Currency.choices, default=Currency.PYG)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True)
    source = models.CharField(max_length=30, choices=Source.choices, default=Source.MANUAL, db_index=True)
    notes = models.TextField(blank=True)
    notified_at = models.DateTimeField(null=True, blank=True)
    notified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tax_commitments_notified",
    )
    paid_at = models.DateTimeField(null=True, blank=True)
    paid_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tax_commitments_paid",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tax_commitments_created",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["due_date", "client__name", "id"]
        indexes = [
            models.Index(fields=["status", "due_date"]),
            models.Index(fields=["client", "status"]),
            models.Index(fields=["commitment_type", "due_date"]),
            models.Index(fields=["source", "status"]),
        ]

    def clean(self):
        super().clean()
        if self.commitment_type == self.CommitmentType.OTHER and not (self.type_other or "").strip():
            raise ValidationError({"type_other": "Debe especificar el tipo cuando selecciona 'Otro'."})
        if self.commitment_type != self.CommitmentType.OTHER and self.type_other:
            self.type_other = ""
        if self.amount is not None and self.amount <= 0:
            raise ValidationError({"amount": "El monto debe ser mayor a cero."})

        has_installment_number = self.installment_number is not None
        has_installment_total = self.installment_total is not None
        if has_installment_number != has_installment_total:
            raise ValidationError("Debe indicar numero y total de cuotas juntos.")
        if has_installment_number and has_installment_total and self.installment_number > self.installment_total:
            raise ValidationError({"installment_number": "La cuota no puede superar el total de cuotas."})
        if self.installment_group_id and not has_installment_number:
            raise ValidationError("Si hay grupo de cuotas, debe indicar numero y total.")

    @property
    def is_overdue_effective(self):
        if self.status in {self.Status.PAID, self.Status.ARCHIVED}:
            return False
        return bool(self.due_date and self.due_date < timezone.localdate())

    @property
    def effective_status(self):
        if self.status == self.Status.ARCHIVED:
            return self.Status.ARCHIVED
        if self.status == self.Status.PAID:
            return self.Status.PAID
        if self.is_overdue_effective:
            return "OVERDUE"
        if self.status == self.Status.NOTIFIED:
            return self.Status.NOTIFIED
        return self.Status.PENDING

    @property
    def effective_status_display(self):
        if self.effective_status == "OVERDUE":
            return "Vencido"
        if self.effective_status == self.Status.PENDING:
            return "Pendiente"
        if self.effective_status == self.Status.NOTIFIED:
            return "Avisado"
        if self.effective_status == self.Status.PAID:
            return "Pagado"
        return "Archivado"

    @property
    def type_display(self):
        if self.commitment_type == self.CommitmentType.OTHER and self.type_other:
            return self.type_other
        return self.get_commitment_type_display()

    @staticmethod
    def new_installment_group_id():
        return uuid.uuid4()

    def __str__(self):
        return f"{self.client} - {self.type_display} ({self.effective_status_display})"
