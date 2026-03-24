from django.contrib import admin

from notifications.models import UserNotification


@admin.register(UserNotification)
class UserNotificationAdmin(admin.ModelAdmin):
    list_description = (
        "Notificaciones internas generadas automáticamente por el sistema para los usuarios. "
        "Se crean cuando ocurren eventos relevantes (ej: una obligación vence hoy, un pendiente fue asignado, etc.). "
        "Cada notificación tiene un destinatario, un nivel de severidad (info, alerta, peligro), "
        "un mensaje y un indicador de si fue leída. Las notificaciones no leídas aparecen en el timbre de "
        "notificaciones del sistema. Este módulo NO debe usarse para enviar mensajes manuales."
    )
    list_display = ("id", "recipient", "severity", "is_read", "created_at", "message")
    list_filter = ("severity", "is_read", "created_at")
    search_fields = ("recipient__username", "message", "event_key", "source_ref")
    autocomplete_fields = ("recipient", "actor", "client")
