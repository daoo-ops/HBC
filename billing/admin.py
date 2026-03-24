from django.contrib import admin

from billing.models import Charge, Contract


@admin.register(Contract)
class ContractAdmin(admin.ModelAdmin):
    list_description = (
        "Contratos de servicio vigentes o históricos entre el estudio y sus clientes. "
        "Cada contrato define el monto mensual acordado, la moneda (PYG o USD), las fechas de inicio y fin "
        "y si está activo. Es el respaldo formal del acuerdo comercial. "
        "Los datos del contrato se usan como referencia para la facturación mensual."
    )
    list_display = ("client", "monthly_amount", "currency", "start_date", "end_date", "active")
    list_filter = ("currency", "active")
    search_fields = ("client__name", "client__ruc")


@admin.register(Charge)
class ChargeAdmin(admin.ModelAdmin):
    list_description = (
        "Cobros mensuales generados para cada cliente (honorarios profesionales). "
        "Cada registro corresponde a un período mensual específico e indica si fue pagado, la fecha de pago, "
        "el monto adeudado y el método de pago. Los cobros con estado 'Deuda' aparecen en la vista de seguimiento "
        "y afectan el campo 'debt_amount' del cliente. Se puede llevar el control de cobros en PYG y USD."
    )
    list_display = ("client", "period_month", "payment_type", "amount", "currency", "status", "debt_amount", "paid_at")
    list_filter = ("currency", "payment_type", "status")
    search_fields = ("client__name", "client__ruc")
