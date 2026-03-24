from rest_framework import serializers

from accounts.models import User
from clients.models import Client


class ClientBaseSerializer(serializers.ModelSerializer):
    zone_display = serializers.CharField(source="get_zone_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    invoice_period_status_display = serializers.CharField(source="get_invoice_period_status_display", read_only=True)
    obligations = serializers.SerializerMethodField()
    responsible_name = serializers.CharField(source="responsible.username", read_only=True)

    def get_obligations(self, obj):
        links = obj.client_obligations.select_related("obligation").filter(obligation__isnull=False)
        return [
            {
                "id": link.id,
                "obligation_id": link.obligation_id,
                "obligation_name": link.obligation.name,
                "obligation_code": link.obligation.code,
                "status": link.status,
                "due_mode": link.due_mode,
                "periodicity": link.periodicity,
                "needs_manual_review": link.needs_manual_review,
            }
            for link in links
        ]


class ClientSerializer(ClientBaseSerializer):
    def validate_responsible(self, value):
        if value is None:
            return value
        if value.role != User.Role.FUNCIONARIO:
            raise serializers.ValidationError("El responsable debe tener rol FUNCIONARIO.")
        return value

    class Meta:
        model = Client
        fields = [
            "id",
            "name",
            "ruc",
            "ruc_dv",
            "ruc_base",
            "responsible",
            "responsible_name",
            "phone",
            "address",
            "zone",
            "zone_display",
            "presentation_type",
            "due_date",
            "submission_date",
            "pending_notes",
            "observations",
            "monthly_amount_pyg",
            "monthly_amount_usd",
            "paid",
            "debt_amount",
            "contract_until",
            "status",
            "status_display",
            "invoice_period_status",
            "invoice_period_status_display",
            "invoice_period_status_updated_by",
            "invoice_period_status_updated_at",
            "obligations",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["ruc_base", "invoice_period_status_updated_by", "invoice_period_status_updated_at", "created_at", "updated_at"]


class ClientOperationalSerializer(ClientBaseSerializer):
    class Meta:
        model = Client
        fields = [
            "id",
            "name",
            "ruc",
            "ruc_dv",
            "ruc_base",
            "responsible",
            "responsible_name",
            "phone",
            "address",
            "zone",
            "zone_display",
            "presentation_type",
            "due_date",
            "submission_date",
            "pending_notes",
            "observations",
            "status",
            "status_display",
            "invoice_period_status",
            "invoice_period_status_display",
            "invoice_period_status_updated_by",
            "invoice_period_status_updated_at",
            "obligations",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "ruc_base",
            "responsible",
            "invoice_period_status_updated_by",
            "invoice_period_status_updated_at",
            "created_at",
            "updated_at",
        ]
