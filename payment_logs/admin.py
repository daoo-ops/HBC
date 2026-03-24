from django.contrib import admin

from payment_logs.forms import PaymentReceptionLogForm
from payment_logs.models import PaymentReceptionLog


@admin.register(PaymentReceptionLog)
class PaymentReceptionLogAdmin(admin.ModelAdmin):
    list_description = (
        "Bitácora de recepciones de pago recibidas en el estudio. "
        "Registra cada vez que un cliente realiza un pago presencial o depósito (efectivo, cheque, transferencia), "
        "indicando quién pagó, el concepto (honorarios, gastos, etc.), el método de pago y la fecha. "
        "Es el registro contable de caja del estudio. Los registros archivados se ocultan de la vista principal "
        "pero se conservan para auditoría y control histórico."
    )
    form = PaymentReceptionLogForm
    list_display = (
        "payment_date",
        "client",
        "paid_by",
        "concept_type",
        "payment_method",
        "is_archived",
        "recorded_by",
        "created_at",
    )
    list_filter = (
        "is_archived",
        "concept_type",
        "payment_method",
        "payment_date",
    )
    search_fields = (
        "client__name",
        "paid_by",
        "observation",
        "third_party_check_name",
    )
    readonly_fields = ("created_at", "updated_at", "recorded_by", "archived_at", "archived_by")
    autocomplete_fields = ("client", "recorded_by", "archived_by")
    fieldsets = (
        (
            "Datos de recepción",
            {
                "fields": (
                    "client",
                    "payment_date",
                    "paid_by",
                    "concept_type",
                    "concept_other",
                    "payment_method",
                    "third_party_check_name",
                    "observation",
                )
            },
        ),
        (
            "Estado",
            {"fields": ("is_archived", "archived_at", "archived_by")},
        ),
        (
            "Trazabilidad",
            {"fields": ("recorded_by", "created_at", "updated_at")},
        ),
    )
