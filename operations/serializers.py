from rest_framework import serializers

from operations.models import Deadline, PendingItem, Submission


class DeadlineSerializer(serializers.ModelSerializer):
    client_name = serializers.CharField(source="client.name", read_only=True)

    class Meta:
        model = Deadline
        fields = [
            "id",
            "client",
            "client_name",
            "description",
            "due_date",
            "obligation_type",
            "source",
            "priority",
            "status",
            "created_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_by", "source", "created_at", "updated_at"]


class SubmissionSerializer(serializers.ModelSerializer):
    client_name = serializers.CharField(source="client.name", read_only=True)
    archived_by_username = serializers.CharField(source="archived_by.username", read_only=True)
    obligation_name = serializers.CharField(source="obligation.name", read_only=True)
    obligation_form = serializers.CharField(source="obligation.form_code", read_only=True)
    period_display = serializers.CharField(read_only=True)

    def validate(self, attrs):
        period_kind = attrs.get("period_kind", getattr(self.instance, "period_kind", None))
        period_year = attrs.get("period_year", getattr(self.instance, "period_year", None))
        period_month = attrs.get("period_month", getattr(self.instance, "period_month", None))

        if period_kind == Submission.PeriodKind.MONTHLY:
            if not period_year or not period_month:
                raise serializers.ValidationError("Para período mensual, año y mes son obligatorios.")
            if period_month < 1 or period_month > 12:
                raise serializers.ValidationError({"period_month": "Mes inválido."})
        elif period_kind == Submission.PeriodKind.ANNUAL:
            if not period_year:
                raise serializers.ValidationError("Para período anual, el año es obligatorio.")
            attrs["period_month"] = None

        return attrs

    class Meta:
        model = Submission
        fields = [
            "id",
            "client",
            "client_name",
            "obligation",
            "obligation_name",
            "obligation_form",
            "submission_type",
            "period_kind",
            "period_year",
            "period_month",
            "period_display",
            "needs_manual_review",
            "due_date",
            "submitted_at",
            "status",
            "is_archived",
            "archived_at",
            "archived_by",
            "archived_by_username",
            "notes",
            "created_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "is_archived",
            "archived_at",
            "archived_by",
            "archived_by_username",
            "period_display",
            "created_by",
            "created_at",
            "updated_at",
        ]


class PendingItemSerializer(serializers.ModelSerializer):
    client_name = serializers.CharField(source="client.name", read_only=True)
    priority = serializers.CharField(required=False)

    def validate_priority(self, value):
        normalized = str(value or "").strip().upper()
        if normalized == "SOON":
            return PendingItem.Priority.OK
        if normalized not in {PendingItem.Priority.OK, PendingItem.Priority.URGENT}:
            raise serializers.ValidationError("Prioridad inválida.")
        return normalized

    class Meta:
        model = PendingItem
        fields = [
            "id",
            "client",
            "client_name",
            "description",
            "missing_documents",
            "expected_date",
            "priority",
            "status",
            "resolved_at",
            "created_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_by", "resolved_at", "created_at", "updated_at"]
