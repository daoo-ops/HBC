from rest_framework import serializers

from accounts.models import User
from banks.models import BankRequest
from operations.models import PendingItem


class BankRequestSerializer(serializers.ModelSerializer):
    client_name = serializers.CharField(source="client.name", read_only=True)
    requested_by_username = serializers.CharField(source="requested_by.username", read_only=True)
    responsible_username = serializers.CharField(source="responsible.username", read_only=True)
    last_note_by_username = serializers.CharField(source="last_note_by.username", read_only=True)

    def validate_responsible(self, value):
        if value and value.role != User.Role.FUNCIONARIO:
            raise serializers.ValidationError("El responsable debe tener rol FUNCIONARIO.")
        return value

    def validate(self, attrs):
        request_type = attrs.get("request_type", getattr(self.instance, "request_type", None))
        request_type_other = attrs.get("request_type_other", getattr(self.instance, "request_type_other", ""))
        operational_reason = attrs.get("operational_reason", getattr(self.instance, "operational_reason", None))
        operational_reason_other = attrs.get(
            "operational_reason_other",
            getattr(self.instance, "operational_reason_other", ""),
        )

        if request_type == BankRequest.RequestType.OTRO and not str(request_type_other or "").strip():
            raise serializers.ValidationError({"request_type_other": "Debe especificar el tipo cuando selecciona 'Otro'."})
        if request_type != BankRequest.RequestType.OTRO:
            attrs["request_type_other"] = ""
        if operational_reason == BankRequest.OperationalReason.OTHER and not str(operational_reason_other or "").strip():
            raise serializers.ValidationError(
                {"operational_reason_other": "Debe especificar el motivo cuando selecciona 'Otro'."}
            )
        if operational_reason != BankRequest.OperationalReason.OTHER:
            attrs["operational_reason_other"] = ""

        client = attrs.get("client", getattr(self.instance, "client", None))
        receipt_pending = attrs.get("receipts_pending_item", getattr(self.instance, "receipts_pending_item", None))
        if receipt_pending and receipt_pending.client_id != client.id:
            raise serializers.ValidationError({"receipts_pending_item": "El pendiente de recibos debe pertenecer al mismo cliente."})

        return attrs

    class Meta:
        model = BankRequest
        fields = [
            "id",
            "client",
            "client_name",
            "request_type",
            "request_type_other",
            "operational_reason",
            "operational_reason_other",
            "request_priority",
            "status",
            "responsible",
            "responsible_username",
            "requested_by",
            "requested_by_username",
            "receipts_status",
            "receipts_client_notified",
            "receipts_notified_by",
            "receipts_notified_at",
            "receipts_pending_item",
            "receipts_loaded_by",
            "receipts_loaded_at",
            "started_by",
            "started_at",
            "completed_by",
            "completed_at",
            "archived_by",
            "archived_at",
            "last_note",
            "last_note_by",
            "last_note_by_username",
            "last_note_at",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "requested_by",
            "receipts_loaded_by",
            "receipts_loaded_at",
            "receipts_notified_by",
            "receipts_notified_at",
            "started_by",
            "started_at",
            "completed_by",
            "completed_at",
            "archived_by",
            "archived_at",
            "last_note_by",
            "last_note_by_username",
            "last_note_at",
            "created_at",
            "updated_at",
        ]


class BankRequestNoteSerializer(serializers.Serializer):
    note = serializers.CharField()


class BankRequestPendingSerializer(serializers.Serializer):
    description = serializers.CharField(required=False, allow_blank=True)
    missing_documents = serializers.CharField(required=False, allow_blank=True)
    expected_date = serializers.DateField(required=False, allow_null=True)
    priority = serializers.CharField(required=False, allow_blank=True)

    def validate_priority(self, value):
        normalized = str(value or "").strip().upper()
        if normalized == "SOON":
            return PendingItem.Priority.OK
        if normalized not in {PendingItem.Priority.OK, PendingItem.Priority.URGENT}:
            raise serializers.ValidationError("Prioridad inválida.")
        return normalized


class BankRequestDocumentActionSerializer(serializers.Serializer):
    pass


class BankRequestStatusActionSerializer(serializers.Serializer):
    pass
