from django.contrib import admin

from clients.models import Client, ClientNote, ClientObligation, ClientResponsibilityHistory, Obligation


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_description = (
        "Registro maestro de todos los clientes del estudio. Cada cliente tiene un RUC, una zona geográfica, "
        "un responsable asignado (funcionario), monto mensual de honorarios y estado de contrato. "
        "Desde aquí se pueden activar, suspender o eliminar clientes. Los cambios de responsable quedan registrados "
        "automáticamente en el historial de responsabilidad."
    )
    list_display = (
        "name",
        "ruc",
        "ruc_dv",
        "zone",
        "status",
        "responsible",
        "monthly_amount_pyg",
        "debt_amount",
        "paid",
        "is_deleted",
    )
    list_filter = ("zone", "status", "is_deleted")
    search_fields = ("name", "ruc", "ruc_base", "responsible__username")


@admin.register(ClientNote)
class ClientNoteAdmin(admin.ModelAdmin):
    list_description = (
        "Notas y observaciones internas vinculadas a un cliente específico. "
        "Sirven para documentar acuerdos, particularidades del cliente, recordatorios o cualquier "
        "información relevante que el equipo necesite tener a mano. Cada nota registra quién la creó y cuándo."
    )
    list_display = ("client", "created_by", "created_at", "updated_at")
    search_fields = ("client__name", "client__ruc", "note")


@admin.register(Obligation)
class ObligationAdmin(admin.ModelAdmin):
    list_description = (
        "Catálogo de obligaciones tributarias disponibles en el sistema (ej: IVA mensual, IRE anual, etc.). "
        "Cada obligación tiene un código único, tipo de impuesto y formulario SET asociado. "
        "Las obligaciones marcadas como 'usa calendario RUC' calculan el vencimiento automáticamente "
        "según el dígito verificador del RUC del cliente. Modificar este catálogo afecta a todos los clientes."
    )
    list_display = ("name", "tax_type", "form_code", "code", "uses_ruc_calendar", "is_active")
    list_filter = ("uses_ruc_calendar", "is_active")
    search_fields = ("name", "code", "tax_type", "form_code")


@admin.register(ClientObligation)
class ClientObligationAdmin(admin.ModelAdmin):
    list_description = (
        "Asignación de una obligación tributaria específica a un cliente concreto. "
        "Define la periodicidad (mensual, trimestral, anual), el modo de vencimiento (automático por RUC o manual) "
        "y si requiere revisión manual. Es el vínculo entre el catálogo de obligaciones y cada cliente. "
        "El sistema genera los plazos de presentación (Deadlines) a partir de estos registros."
    )
    list_display = ("client", "obligation", "status", "due_mode", "periodicity", "needs_manual_review")
    list_filter = ("status", "due_mode", "periodicity", "needs_manual_review")
    search_fields = ("client__name", "client__ruc", "obligation__name", "source_presentation_type")


@admin.register(ClientResponsibilityHistory)
class ClientResponsibilityHistoryAdmin(admin.ModelAdmin):
    list_description = (
        "Registro histórico inmutable de todos los cambios de responsable en los clientes. "
        "Cada vez que un cliente cambia de funcionario responsable, se crea automáticamente un registro aquí "
        "indicando quién era el responsable anterior, quién es el nuevo y quién realizó el cambio. "
        "Este historial es de solo lectura y no debe modificarse manualmente."
    )
    list_display = ("client", "old_responsible", "new_responsible", "changed_by", "changed_at")
    list_filter = ("changed_at",)
    search_fields = (
        "client__name",
        "client__ruc",
        "old_responsible__username",
        "new_responsible__username",
        "changed_by__username",
    )
