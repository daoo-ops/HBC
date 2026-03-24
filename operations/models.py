from django.conf import settings
from django.db import models


class Deadline(models.Model):
    class Source(models.TextChoices):
        AUTO = "AUTO", "Automático"
        MANUAL = "MANUAL", "Manual"

    class Priority(models.TextChoices):
        URGENT = "URGENT", "Urgente"
        OK = "OK", "Normal"

    class Status(models.TextChoices):
        OPEN = "OPEN", "Abierto"
        COMPLETED = "COMPLETED", "Completado"

    client = models.ForeignKey(
        "clients.Client",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="deadlines",
    )
    description = models.CharField(max_length=255)
    due_date = models.DateField(db_index=True)
    obligation_type = models.CharField(max_length=255, blank=True)
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.MANUAL)
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.OK, db_index=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.OPEN, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_deadlines",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["due_date", "priority"]


class Submission(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pendiente"
        SUBMITTED = "SUBMITTED", "Presentado"
        LATE = "LATE", "Atrasado"

    class PeriodKind(models.TextChoices):
        MONTHLY = "MONTHLY", "Mensual"
        ANNUAL = "ANNUAL", "Anual"
        OTHER = "OTHER", "Otro"

    client = models.ForeignKey("clients.Client", on_delete=models.CASCADE, related_name="submissions")
    submission_type = models.CharField(max_length=255)
    obligation = models.ForeignKey(
        "clients.Obligation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submissions",
    )
    period_kind = models.CharField(max_length=20, choices=PeriodKind.choices, null=True, blank=True)
    period_year = models.PositiveSmallIntegerField(null=True, blank=True, db_index=True)
    period_month = models.PositiveSmallIntegerField(null=True, blank=True, db_index=True)
    needs_manual_review = models.BooleanField(null=True, blank=True, default=None, db_index=True)
    due_date = models.DateField(null=True, blank=True)
    submitted_at = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True)
    is_archived = models.BooleanField(default=False, db_index=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    archived_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="archived_submissions",
    )
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_submissions",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def obligation_name_display(self):
        if self.obligation_id and self.obligation:
            return self.obligation.name
        return self.submission_type

    @property
    def obligation_form_display(self):
        if self.obligation_id and self.obligation and self.obligation.form_code:
            return self.obligation.form_code
        return "-"

    @property
    def period_display(self):
        period_kind = self.period_kind
        period_year = self.period_year
        period_month = self.period_month

        if not period_kind and self.obligation_id and self.obligation:
            periodicity = (self.obligation.default_periodicity or "").upper()
            if periodicity == "MONTHLY":
                period_kind = self.PeriodKind.MONTHLY
            elif periodicity == "ANNUAL":
                period_kind = self.PeriodKind.ANNUAL
            else:
                period_kind = self.PeriodKind.OTHER

        reference_date = self.due_date or self.submitted_at
        if not period_year and reference_date:
            period_year = reference_date.year
        if not period_month and reference_date and period_kind != self.PeriodKind.ANNUAL:
            period_month = reference_date.month

        if not period_year:
            return "-"
        if period_kind == self.PeriodKind.MONTHLY and period_month:
            return f"{period_month:02d}/{period_year}"
        if period_kind == self.PeriodKind.ANNUAL:
            return f"Ejercicio {period_year}"
        if period_month:
            return f"{period_month:02d}/{period_year}"
        return str(period_year)


class PendingItem(models.Model):
    class Priority(models.TextChoices):
        URGENT = "URGENT", "Urgente"
        OK = "OK", "Normal"

    class Status(models.TextChoices):
        OPEN = "OPEN", "Activa"
        RESOLVED = "RESOLVED", "Resuelta"

    client = models.ForeignKey("clients.Client", on_delete=models.CASCADE, related_name="pending_items")
    description = models.CharField(max_length=255)
    missing_documents = models.TextField(blank=True)
    expected_date = models.DateField(null=True, blank=True)
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.OK, db_index=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN, db_index=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="deleted_pending_items",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_pending_items",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["priority", "expected_date", "created_at"]
