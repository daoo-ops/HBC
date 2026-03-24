from django.contrib import admin

from operations.models import Deadline, PendingItem, Submission


@admin.register(Deadline)
class DeadlineAdmin(admin.ModelAdmin):
    list_description = (
        "Plazos de presentación de obligaciones tributarias de los clientes. "
        "Los registros con source=AUTO son generados automáticamente por el sistema según el calendario y el RUC del cliente. "
        "Los de source=MANUAL fueron creados manualmente por un operador. "
        "Cada deadline tiene un estado (Abierto/Completado) y una prioridad (Normal/Urgente). "
        "Son la base del tablero de 'Obligaciones que vencen' en el inicio operativo."
    )
    list_display = ("description", "client", "due_date", "priority", "status", "source")
    list_filter = ("priority", "status", "source")
    search_fields = ("description", "client__name", "client__ruc")


@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_description = (
        "Registro de presentaciones (declaraciones juradas) realizadas o pendientes por cada cliente. "
        "Cada submission vincula un cliente, una obligación tributaria, un período y un estado "
        "(Pendiente / Presentado / Atrasado). Al marcar como 'Presentado' se registra la fecha de presentación. "
        "Los submissions archivados se ocultan del tablero principal pero quedan en el historial. "
        "Son la columna vertebral del seguimiento de trabajo operativo del estudio."
    )
    list_display = (
        "client",
        "submission_type",
        "obligation",
        "period_kind",
        "period_year",
        "period_month",
        "status",
        "is_archived",
        "needs_manual_review",
        "due_date",
        "submitted_at",
        "archived_at",
    )
    list_filter = ("status", "is_archived", "period_kind", "needs_manual_review")
    search_fields = ("client__name", "submission_type", "obligation__name", "obligation__form_code")


@admin.register(PendingItem)
class PendingItemAdmin(admin.ModelAdmin):
    list_description = (
        "Tareas pendientes y documentos faltantes asociados a clientes. "
        "Se usan para rastrear cosas que el cliente debe entregar o que el equipo debe gestionar internamente "
        "(ej: 'Falta balance', 'Pendiente firma de contrato'). "
        "Tienen prioridad (Normal/Urgente), fecha esperada de resolución y estado (Activa/Resuelta). "
        "Los pendientes resueltos o eliminados no aparecen en el tablero pero se conservan para auditoría."
    )
    list_display = ("description", "client", "priority", "status", "expected_date", "resolved_at", "is_deleted")
    list_filter = ("priority", "status", "is_deleted")
    search_fields = ("description", "client__name")
