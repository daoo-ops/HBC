from django.contrib import admin

from banks.models import BankRequest


@admin.register(BankRequest)
class BankRequestAdmin(admin.ModelAdmin):
    list_description = (
        "Solicitudes de gestión bancaria realizadas para o por los clientes. "
        "Incluye trámites como apertura de cuentas, solicitud de certificados, transferencias especiales, etc. "
        "Cada solicitud tiene un tipo, motivo operativo, prioridad y un seguimiento de estado "
        "(Pendiente → En proceso → Completada → Archivada). Se puede registrar la carga de recibos "
        "y llevar una bitácora de notas internas. El responsable asignado es el funcionario que gestiona el trámite."
    )
    list_display = (
        "client",
        "request_type",
        "operational_reason",
        "request_priority",
        "status",
        "responsible",
        "requested_by",
        "last_note_at",
        "receipts_status",
        "created_at",
    )
    list_filter = ("request_type", "operational_reason", "request_priority", "status", "receipts_status")
    search_fields = (
        "client__name",
        "client__ruc",
        "request_type_other",
        "operational_reason_other",
        "last_note",
        "notes",
    )
    autocomplete_fields = (
        "client",
        "responsible",
        "requested_by",
        "last_note_by",
        "receipts_loaded_by",
        "started_by",
        "completed_by",
        "archived_by",
        "receipts_pending_item",
    )
