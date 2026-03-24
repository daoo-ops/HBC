from rest_framework import mixins, viewsets

from accounts.permissions import IsMasterOrAdmin
from auditing.models import AuditLog
from auditing.serializers import AuditLogSerializer


class AuditLogViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = AuditLogSerializer
    permission_classes = [IsMasterOrAdmin]

    def get_queryset(self):
        queryset = AuditLog.objects.all()
        entity = self.request.query_params.get("entity")
        if entity:
            queryset = queryset.filter(entity=entity)
        entity_id = self.request.query_params.get("entity_id")
        if entity_id:
            queryset = queryset.filter(entity_id=entity_id)
        action = self.request.query_params.get("action")
        if action:
            queryset = queryset.filter(action=action)
        return queryset
