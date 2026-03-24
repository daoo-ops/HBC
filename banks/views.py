from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework import decorators, response, status, viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated

from auditing.services import get_instance_snapshot, log_model_event
from banks.models import BankRequest
from banks.serializers import (
    BankRequestDocumentActionSerializer,
    BankRequestNoteSerializer,
    BankRequestPendingSerializer,
    BankRequestSerializer,
    BankRequestStatusActionSerializer,
)
from banks.services import (
    can_access_bank_request,
    can_manage_bank_requests,
    create_or_link_document_pending,
    mark_archived,
    mark_completed,
    mark_document_loaded,
    mark_in_progress,
    reopen_archived,
)
from clients.utils import parse_bool_param
from operations.models import PendingItem


def _raise_if_not_manager(user):
    if not can_manage_bank_requests(user):
        raise PermissionDenied("No tenés permisos para esta acción.")


def _validation_error_text(exc: DjangoValidationError) -> str:
    if hasattr(exc, "messages") and exc.messages:
        return exc.messages[0]
    return str(exc)


class BankRequestViewSet(viewsets.ModelViewSet):
    queryset = BankRequest.objects.select_related(
        "client",
        "responsible",
        "requested_by",
        "last_note_by",
        "receipts_pending_item",
        "receipts_notified_by",
        "receipts_loaded_by",
        "started_by",
        "completed_by",
        "archived_by",
    ).all()
    serializer_class = BankRequestSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user

        if not can_manage_bank_requests(user):
            queryset = queryset.filter(responsible_id=user.id)

        params = self.request.query_params
        client = params.get("client")
        if client:
            queryset = queryset.filter(client_id=client)

        status_param = params.get("status")
        if status_param:
            queryset = queryset.filter(status=status_param)

        request_type = params.get("request_type")
        if request_type:
            queryset = queryset.filter(request_type=request_type)

        responsible = params.get("responsible")
        if responsible and can_manage_bank_requests(user):
            queryset = queryset.filter(responsible_id=responsible)

        receipts_status = params.get("receipts_status")
        if receipts_status:
            queryset = queryset.filter(receipts_status=receipts_status)

        mine = parse_bool_param(params.get("mine"))
        if mine is True:
            queryset = queryset.filter(responsible_id=user.id)

        return queryset

    def perform_create(self, serializer):
        _raise_if_not_manager(self.request.user)
        item = serializer.save(requested_by=self.request.user)
        if not item.responsible_id and item.client and item.client.responsible_id:
            item.responsible = item.client.responsible
            item.save(update_fields=["responsible", "updated_at"])
        log_model_event(
            actor=self.request.user,
            action="create",
            instance=item,
            after_data=get_instance_snapshot(item),
        )

    def perform_update(self, serializer):
        _raise_if_not_manager(self.request.user)
        before = get_instance_snapshot(serializer.instance)
        item = serializer.save()
        log_model_event(
            actor=self.request.user,
            action="update",
            instance=item,
            before_data=before,
            after_data=get_instance_snapshot(item),
        )

    def perform_destroy(self, instance):
        _raise_if_not_manager(self.request.user)
        before = get_instance_snapshot(instance)
        super().perform_destroy(instance)
        log_model_event(
            actor=self.request.user,
            action="delete",
            instance=instance,
            before_data=before,
            after_data={},
        )

    def get_object(self):
        item = super().get_object()
        if not can_access_bank_request(self.request.user, item):
            raise PermissionDenied("No tenés permisos para acceder a esta solicitud.")
        return item

    @decorators.action(detail=True, methods=["post"], url_path="add-note")
    def add_note(self, request, pk=None):
        item = self.get_object()
        serializer = BankRequestNoteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        note = serializer.validated_data["note"].strip()
        if not note:
            return response.Response({"detail": "La nota no puede estar vacía."}, status=status.HTTP_400_BAD_REQUEST)

        before = get_instance_snapshot(item)
        now = timezone.now()
        actor = request.user if request.user.is_authenticated else None
        item.last_note = note
        item.last_note_by = actor
        item.last_note_at = now
        # Compatibilidad: mantener notes con el último valor visible.
        item.notes = note
        item.save(update_fields=["last_note", "last_note_by", "last_note_at", "notes", "updated_at"])
        log_model_event(
            actor=request.user,
            action="add_note",
            instance=item,
            before_data=before,
            after_data=get_instance_snapshot(item),
        )
        return response.Response(self.get_serializer(item).data, status=status.HTTP_200_OK)

    @decorators.action(detail=True, methods=["post"], url_path="create-receipts-pending")
    def create_receipts_pending(self, request, pk=None):
        _raise_if_not_manager(request.user)
        item = self.get_object()
        serializer = BankRequestPendingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        description = serializer.validated_data.get("description") or f"Bancos y recibos: {item.get_request_type_display()} - Recibos pendientes"
        missing = serializer.validated_data.get("missing_documents", "")
        expected_date = serializer.validated_data.get("expected_date")
        priority = serializer.validated_data.get("priority")
        if not priority:
            priority = PendingItem.Priority.URGENT if item.request_priority == BankRequest.Priority.URGENT else PendingItem.Priority.OK

        before_item = get_instance_snapshot(item)
        pending, created = create_or_link_document_pending(
            item=item,
            actor=request.user,
            document_kind="receipts",
            description=description,
            missing_documents=missing,
            expected_date=expected_date,
            priority=priority,
        )
        log_model_event(
            actor=request.user,
            action="create_receipts_pending",
            instance=item,
            before_data=before_item,
            after_data=get_instance_snapshot(item),
            metadata={"pending_item_id": pending.id, "created": created, "priority": priority},
        )
        log_model_event(
            actor=request.user,
            action="create" if created else "update",
            instance=pending,
            after_data=get_instance_snapshot(pending),
            metadata={"source": "bank_request", "document_kind": "receipts", "bank_request_id": item.id},
        )
        payload = self.get_serializer(item).data
        payload["linked_pending_item"] = pending.id
        payload["linked_pending_created"] = created
        return response.Response(payload, status=status.HTTP_200_OK)

    @decorators.action(detail=True, methods=["post"], url_path="mark-receipts-loaded")
    def mark_receipts_loaded(self, request, pk=None):
        item = self.get_object()
        serializer = BankRequestDocumentActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        before_item = get_instance_snapshot(item)
        resolved_pending = mark_document_loaded(item=item, actor=request.user, document_kind="receipts")
        log_model_event(
            actor=request.user,
            action="mark_receipts_loaded",
            instance=item,
            before_data=before_item,
            after_data=get_instance_snapshot(item),
            metadata={"resolved_pending_item_id": resolved_pending.id if resolved_pending else None},
        )
        if resolved_pending:
            log_model_event(
                actor=request.user,
                action="resolve_from_bank_request",
                instance=resolved_pending,
                after_data=get_instance_snapshot(resolved_pending),
                metadata={"bank_request_id": item.id, "document_kind": "receipts"},
            )
        return response.Response(self.get_serializer(item).data, status=status.HTTP_200_OK)

    @decorators.action(detail=True, methods=["post"], url_path="mark-in-progress")
    def mark_in_progress(self, request, pk=None):
        _raise_if_not_manager(request.user)
        item = self.get_object()
        serializer = BankRequestStatusActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        before = get_instance_snapshot(item)

        try:
            mark_in_progress(item=item, actor=request.user)
        except DjangoValidationError as exc:
            return response.Response({"detail": _validation_error_text(exc)}, status=status.HTTP_400_BAD_REQUEST)

        log_model_event(
            actor=request.user,
            action="mark_in_progress",
            instance=item,
            before_data=before,
            after_data=get_instance_snapshot(item),
        )
        return response.Response(self.get_serializer(item).data, status=status.HTTP_200_OK)

    @decorators.action(detail=True, methods=["post"], url_path="mark-completed")
    def mark_completed(self, request, pk=None):
        _raise_if_not_manager(request.user)
        item = self.get_object()
        serializer = BankRequestStatusActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        before = get_instance_snapshot(item)

        try:
            mark_completed(item=item, actor=request.user)
        except DjangoValidationError as exc:
            return response.Response({"detail": _validation_error_text(exc)}, status=status.HTTP_400_BAD_REQUEST)

        log_model_event(
            actor=request.user,
            action="mark_completed",
            instance=item,
            before_data=before,
            after_data=get_instance_snapshot(item),
        )
        return response.Response(self.get_serializer(item).data, status=status.HTTP_200_OK)

    @decorators.action(detail=True, methods=["post"], url_path="archive")
    def archive(self, request, pk=None):
        _raise_if_not_manager(request.user)
        item = self.get_object()
        serializer = BankRequestStatusActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        before = get_instance_snapshot(item)

        try:
            mark_archived(item=item, actor=request.user)
        except DjangoValidationError as exc:
            return response.Response({"detail": _validation_error_text(exc)}, status=status.HTTP_400_BAD_REQUEST)

        log_model_event(
            actor=request.user,
            action="archive",
            instance=item,
            before_data=before,
            after_data=get_instance_snapshot(item),
        )
        return response.Response(self.get_serializer(item).data, status=status.HTTP_200_OK)

    @decorators.action(detail=True, methods=["post"], url_path="reopen")
    def reopen(self, request, pk=None):
        _raise_if_not_manager(request.user)
        item = self.get_object()
        serializer = BankRequestStatusActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        before = get_instance_snapshot(item)

        try:
            reopen_archived(item=item)
        except DjangoValidationError as exc:
            return response.Response({"detail": _validation_error_text(exc)}, status=status.HTTP_400_BAD_REQUEST)

        log_model_event(
            actor=request.user,
            action="reopen",
            instance=item,
            before_data=before,
            after_data=get_instance_snapshot(item),
        )
        return response.Response(self.get_serializer(item).data, status=status.HTTP_200_OK)
