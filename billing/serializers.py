from rest_framework import serializers

from billing.models import Charge, Contract


class ContractSerializer(serializers.ModelSerializer):
    client_name = serializers.CharField(source="client.name", read_only=True)

    class Meta:
        model = Contract
        fields = [
            "id",
            "client",
            "client_name",
            "start_date",
            "end_date",
            "monthly_amount",
            "currency",
            "active",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]


class ChargeSerializer(serializers.ModelSerializer):
    client_name = serializers.CharField(source="client.name", read_only=True)

    class Meta:
        model = Charge
        fields = [
            "id",
            "client",
            "client_name",
            "contract",
            "period_month",
            "amount",
            "debt_amount",
            "currency",
            "payment_type",
            "status",
            "paid_at",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["paid_at", "created_at", "updated_at"]
