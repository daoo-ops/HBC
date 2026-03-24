from decimal import Decimal

from django.db import models


class Contract(models.Model):
    class Currency(models.TextChoices):
        PYG = "PYG", "Guaraní"
        USD = "USD", "Dólar"

    client = models.ForeignKey("clients.Client", on_delete=models.CASCADE, related_name="contracts")
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    monthly_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    currency = models.CharField(max_length=3, choices=Currency.choices, default=Currency.PYG)
    active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]


class Charge(models.Model):
    class Currency(models.TextChoices):
        PYG = "PYG", "Guaraní"
        USD = "USD", "Dólar"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pendiente"
        PAID = "PAID", "Pagado"

    class PaymentType(models.TextChoices):
        HONORARIOS = "HONORARIOS", "Honorarios"
        IMPUESTOS = "IMPUESTOS", "Impuestos"
        OTROS = "OTROS", "Otros"

    client = models.ForeignKey("clients.Client", on_delete=models.CASCADE, related_name="charges")
    contract = models.ForeignKey(
        Contract,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="charges",
    )
    period_month = models.DateField(db_index=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    debt_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    currency = models.CharField(max_length=3, choices=Currency.choices, default=Currency.PYG)
    payment_type = models.CharField(
        max_length=20,
        choices=PaymentType.choices,
        default=PaymentType.HONORARIOS,
        db_index=True,
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-period_month", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["client", "period_month", "currency"],
                name="unique_client_month_currency_charge",
            )
        ]
