from django.utils import timezone
from django.utils.dateparse import parse_date
from django.db.models import Q
from rest_framework import viewsets

from accounts.models import User
from accounts.permissions import ClientAccessPermission
from auditing.services import get_instance_snapshot, log_model_event
from clients.models import Client
from clients.services import track_responsible_change
from clients.serializers import ClientOperationalSerializer, ClientSerializer
from clients.utils import parse_bool_param


class ClientViewSet(viewsets.ModelViewSet):
    permission_classes = [ClientAccessPermission]

    def get_serializer_class(self):
        if self.request.user.role in {User.Role.MASTER, User.Role.ADMIN}:
            return ClientSerializer
        return ClientOperationalSerializer

    def get_queryset(self):
        queryset = Client.objects.filter(is_deleted=False)
        params = self.request.query_params
        if self.request.user.role == User.Role.FUNCIONARIO:
            queryset = queryset.filter(responsible_id=self.request.user.id)
        else:
            responsible = params.get("responsible")
            if responsible:
                queryset = queryset.filter(responsible_id=responsible)
            mine = parse_bool_param(params.get("mine"))
            if mine is True:
                queryset = queryset.filter(responsible_id=self.request.user.id)

        q = params.get("q")
        if q:
            queryset = queryset.filter(Q(name__icontains=q) | Q(ruc__icontains=q))

        zone = params.get("zona") or params.get("zone")
        if zone:
            queryset = queryset.filter(zone=zone)

        status_filter = params.get("estado") or params.get("status")
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        con_deuda = parse_bool_param(params.get("con_deuda"))
        if con_deuda is True:
            queryset = queryset.filter(debt_amount__gt=0)
        elif con_deuda is False:
            queryset = queryset.filter(debt_amount__lte=0)

        due_from = parse_date(params.get("vence_desde", ""))
        if due_from:
            queryset = queryset.filter(due_date__gte=due_from)

        due_to = parse_date(params.get("vence_hasta", ""))
        if due_to:
            queryset = queryset.filter(due_date__lte=due_to)

        return queryset.distinct().order_by("name")

    def perform_create(self, serializer):
        old_responsible_id = None
        requested_invoice_status = serializer.validated_data.get("invoice_period_status")
        client = serializer.save()
        if requested_invoice_status and requested_invoice_status != Client.InvoicePeriodStatus.PENDING:
            client.invoice_period_status_updated_by = self.request.user
            client.invoice_period_status_updated_at = timezone.now()
            client.save(update_fields=["invoice_period_status_updated_by", "invoice_period_status_updated_at", "updated_at"])
        log_model_event(
            actor=self.request.user,
            action="create",
            instance=client,
            after_data=get_instance_snapshot(client),
        )
        track_responsible_change(
            client=client,
            old_responsible_id=old_responsible_id,
            actor=self.request.user,
            reason="Asignación inicial desde API",
        )

    def perform_update(self, serializer):
        old_responsible_id = serializer.instance.responsible_id
        before = get_instance_snapshot(serializer.instance)
        old_invoice_status = serializer.instance.invoice_period_status
        client = serializer.save()
        if client.invoice_period_status != old_invoice_status:
            client.invoice_period_status_updated_by = self.request.user
            client.invoice_period_status_updated_at = timezone.now()
            client.save(update_fields=["invoice_period_status_updated_by", "invoice_period_status_updated_at", "updated_at"])
        log_model_event(
            actor=self.request.user,
            action="update",
            instance=client,
            before_data=before,
            after_data=get_instance_snapshot(client),
        )
        track_responsible_change(
            client=client,
            old_responsible_id=old_responsible_id,
            actor=self.request.user,
            reason="Reasignación desde API",
        )

    def perform_destroy(self, instance):
        before = get_instance_snapshot(instance)
        instance.is_deleted = True
        instance.deleted_at = timezone.now()
        instance.save(update_fields=["is_deleted", "deleted_at", "updated_at"])
        log_model_event(
            actor=self.request.user,
            action="delete",
            instance=instance,
            before_data=before,
            after_data=get_instance_snapshot(instance),
        )
