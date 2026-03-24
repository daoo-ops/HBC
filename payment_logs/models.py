from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class PaymentReceptionLog(models.Model):
    class ConceptType(models.TextChoices):
        HONORARIOS = "HONORARIOS", "Honorarios"
        IMPUESTOS = "IMPUESTOS", "Impuestos"
        ANTICIPOS = "ANTICIPOS", "Anticipos"
        FACILIDAD = "FACILIDAD", "Facilidad"
        OTROS = "OTROS", "Otros"

    class PaymentMethod(models.TextChoices):
        TRANSFERENCIA = "TRANSFERENCIA", "Transferencia"
        EFECTIVO = "EFECTIVO", "Efectivo"
        CHEQUE_PROPIO = "CHEQUE_PROPIO", "Cheque propio"
        CHEQUE_TERCERO = "CHEQUE_TERCERO", "Cheque de tercero"
        OTRO = "OTRO", "Otro"

    client = models.ForeignKey(
        "clients.Client",
        on_delete=models.CASCADE,
        related_name="payment_reception_logs",
    )
    payment_date = models.DateField(db_index=True)
    paid_by = models.CharField(max_length=120)
    concept_type = models.CharField(
        max_length=20,
        choices=ConceptType.choices,
        db_index=True,
    )
    concept_other = models.CharField(max_length=120, blank=True)
    payment_method = models.CharField(
        max_length=20,
        choices=PaymentMethod.choices,
        db_index=True,
    )
    third_party_check_name = models.CharField(max_length=160, blank=True)
    observation = models.TextField(blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payment_logs_recorded",
    )
    is_archived = models.BooleanField(default=False, db_index=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    archived_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payment_logs_archived",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-payment_date", "-created_at"]
        indexes = [
            models.Index(fields=["client", "payment_date"]),
            models.Index(fields=["concept_type", "payment_method"]),
        ]

    def clean(self):
        super().clean()

        if self.concept_type == self.ConceptType.OTROS and not self.concept_other.strip():
            raise ValidationError({"concept_other": "Indicá el concepto cuando el tipo es 'Otros'."})

        if self.concept_type != self.ConceptType.OTROS and self.concept_other:
            self.concept_other = ""

        if self.payment_method == self.PaymentMethod.CHEQUE_TERCERO and not self.third_party_check_name.strip():
            raise ValidationError(
                {"third_party_check_name": "Indicá el tercero del cheque cuando el medio es cheque de tercero."}
            )

        if self.payment_method != self.PaymentMethod.CHEQUE_TERCERO and self.third_party_check_name:
            self.third_party_check_name = ""

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.client.name} · {self.payment_date} · {self.get_concept_type_display()}"
