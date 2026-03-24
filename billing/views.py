from django.utils import timezone
from rest_framework import decorators, response, status, viewsets

from accounts.permissions import IsMasterOrAdmin
from auditing.services import get_instance_snapshot, log_model_event
from billing.models import Charge, Contract
from billing.serializers import ChargeSerializer, ContractSerializer
from billing.services import sync_client_billing_snapshot


class ContractViewSet(viewsets.ModelViewSet):
    queryset = Contract.objects.all().order_by("-created_at")
    serializer_class = ContractSerializer
    permission_classes = [IsMasterOrAdmin]

    def get_queryset(self):
        queryset = super().get_queryset()
        client_id = self.request.query_params.get("client")
        if client_id:
            queryset = queryset.filter(client_id=client_id)
        active = self.request.query_params.get("active")
        if active in {"true", "1"}:
            queryset = queryset.filter(active=True)
        elif active in {"false", "0"}:
            queryset = queryset.filter(active=False)
        return queryset

    def perform_create(self, serializer):
        contract = serializer.save()
        if contract.end_date:
            contract.client.contract_until = contract.end_date
            contract.client.save(update_fields=["contract_until", "updated_at"])
        log_model_event(
            actor=self.request.user,
            action="create",
            instance=contract,
            after_data=get_instance_snapshot(contract),
        )

    def perform_update(self, serializer):
        before = get_instance_snapshot(serializer.instance)
        contract = serializer.save()
        if contract.end_date:
            contract.client.contract_until = contract.end_date
            contract.client.save(update_fields=["contract_until", "updated_at"])
        log_model_event(
            actor=self.request.user,
            action="update",
            instance=contract,
            before_data=before,
            after_data=get_instance_snapshot(contract),
        )


class ChargeViewSet(viewsets.ModelViewSet):
    queryset = Charge.objects.all().order_by("-period_month")
    serializer_class = ChargeSerializer
    permission_classes = [IsMasterOrAdmin]

    def get_queryset(self):
        queryset = super().get_queryset()
        client_id = self.request.query_params.get("client")
        if client_id:
            queryset = queryset.filter(client_id=client_id)
        status_param = self.request.query_params.get("status")
        if status_param:
            queryset = queryset.filter(status=status_param)
        payment_type = self.request.query_params.get("payment_type")
        if payment_type:
            queryset = queryset.filter(payment_type=payment_type)
        return queryset

    def perform_create(self, serializer):
        charge = serializer.save()
        if charge.status == Charge.Status.PAID:
            charge.paid_at = charge.paid_at or timezone.now()
            charge.debt_amount = 0
            charge.save(update_fields=["paid_at", "debt_amount", "updated_at"])
        elif charge.debt_amount <= 0:
            charge.debt_amount = charge.amount
            charge.save(update_fields=["debt_amount", "updated_at"])

        sync_client_billing_snapshot(charge.client)
        log_model_event(
            actor=self.request.user,
            action="create",
            instance=charge,
            after_data=get_instance_snapshot(charge),
        )

    def perform_update(self, serializer):
        before = get_instance_snapshot(serializer.instance)
        charge = serializer.save()
        if charge.status == Charge.Status.PAID and not charge.paid_at:
            charge.paid_at = timezone.now()
            charge.debt_amount = 0
            charge.save(update_fields=["paid_at", "debt_amount", "updated_at"])
        sync_client_billing_snapshot(charge.client)
        log_model_event(
            actor=self.request.user,
            action="update",
            instance=charge,
            before_data=before,
            after_data=get_instance_snapshot(charge),
        )

    @decorators.action(detail=True, methods=["post"], url_path="mark-paid")
    def mark_paid(self, request, pk=None):
        charge = self.get_object()
        before = get_instance_snapshot(charge)
        charge.status = Charge.Status.PAID
        charge.paid_at = timezone.now()
        charge.debt_amount = 0
        charge.save(update_fields=["status", "paid_at", "debt_amount", "updated_at"])
        sync_client_billing_snapshot(charge.client)

        log_model_event(
            actor=request.user,
            action="mark_paid",
            instance=charge,
            before_data=before,
            after_data=get_instance_snapshot(charge),
        )
        return response.Response(self.get_serializer(charge).data, status=status.HTTP_200_OK)
