from django.contrib import admin

from tax_commitments.models import TaxCommitment


@admin.register(TaxCommitment)
class TaxCommitmentAdmin(admin.ModelAdmin):
    list_description = (
        "Compromisos de pago de impuestos especiales de los clientes (IRE, IDU, facilidades de pago, anticipos, etc.). "
        "Cada registro indica el cliente, el tipo de pago, el monto, la moneda, la fecha de vencimiento y el estado "
        "(Pendiente → Avisado → Pagado). Los compromisos pueden ser en cuotas: en ese caso, todos los registros del mismo "
        "grupo comparten un 'installment_group_id'. Los registros importados desde estado de cuenta tienen source=ACCOUNT_STATEMENT_IMPORT. "
        "Estos datos alimentan la sección 'Compromisos tributarios' del inicio operativo."
    )
    list_display = (
        "id",
        "client",
        "commitment_type",
        "reference_number",
        "due_date",
        "amount",
        "currency",
        "installment_mode",
        "status",
        "source",
    )
    list_filter = ("status", "commitment_type", "currency", "installment_mode", "source", "due_date")
    search_fields = ("client__name", "client__ruc", "reference_number", "period_reference", "notes")
    autocomplete_fields = ("client", "created_by", "notified_by", "paid_by")
