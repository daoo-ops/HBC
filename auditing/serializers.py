from rest_framework import serializers

from auditing.models import AuditLog


class AuditLogSerializer(serializers.ModelSerializer):
    actor_username = serializers.CharField(source="actor.username", read_only=True)

    class Meta:
        model = AuditLog
        fields = [
            "id",
            "action",
            "entity",
            "entity_id",
            "actor",
            "actor_username",
            "before_data",
            "after_data",
            "metadata",
            "created_at",
        ]
