from datetime import date

from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from rest_framework import decorators, response, status, viewsets

from accounts.models import User
from accounts.permissions import OperationalAccessPermission
from auditing.services import get_instance_snapshot, log_model_event
from clients.models import Client
from clients.utils import parse_bool_param
from operations.models import Deadline, PendingItem, Submission
from operations.serializers import DeadlineSerializer, PendingItemSerializer, SubmissionSerializer
from operations.services import build_automatic_deadline_payload


def _is_manager(user):
    return user.role in {User.Role.MASTER, User.Role.ADMIN}


def _ensure_client_assignment(user, client):
    if _is_manager(user):
        return
    if client is None or client.responsible_id != user.id:
        raise PermissionDenied("No tenés permisos para operar este cliente.")


def _normalize_pending_priority(value: str) -> str:
    normalized = str(value or "").strip().upper()
    if normalized == "SOON":
        return PendingItem.Priority.OK
    return normalized


def _infer_period_kind_from_obligation(obligation):
    if not obligation:
        return None
    periodicity = (obligation.default_periodicity or "").upper()
    if periodicity == "MONTHLY":
        return Submission.PeriodKind.MONTHLY
    if periodicity == "ANNUAL":
        return Submission.PeriodKind.ANNUAL
    return Submission.PeriodKind.OTHER


def _normalize_submission_fields(submission):
    if submission.obligation_id and not submission.period_kind:
        submission.period_kind = _infer_period_kind_from_obligation(submission.obligation)

    reference_date = submission.due_date or submission.submitted_at
    if submission.period_kind == Submission.PeriodKind.MONTHLY:
        if not submission.period_year and reference_date:
            submission.period_year = reference_date.year
        if not submission.period_month and reference_date:
            submission.period_month = reference_date.month

    if submission.period_kind == Submission.PeriodKind.ANNUAL:
        if not submission.period_year and reference_date:
            submission.period_year = reference_date.year
        submission.period_month = None

    if submission.period_kind == Submission.PeriodKind.OTHER:
        if not submission.period_year and reference_date:
            submission.period_year = reference_date.year
        if not submission.period_month and reference_date:
            submission.period_month = reference_date.month

    submission.needs_manual_review = not bool(submission.obligation_id and submission.period_year)


class DeadlineViewSet(viewsets.ModelViewSet):
    queryset = Deadline.objects.all().order_by("due_date")
    serializer_class = DeadlineSerializer
    permission_classes = [OperationalAccessPermission]

    def get_queryset(self):
        queryset = super().get_queryset()
        if not _is_manager(self.request.user):
            queryset = queryset.filter(client__responsible_id=self.request.user.id)
        status_param = self.request.query_params.get("status")
        if status_param:
            queryset = queryset.filter(status=status_param)
        priority = _normalize_pending_priority(self.request.query_params.get("priority"))
        if priority:
            queryset = queryset.filter(priority=priority)
        return queryset

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        serialized = self.get_serializer(queryset, many=True).data

        include_auto = request.query_params.get("include_auto", "true").lower() != "false"
        if include_auto:
            year = int(request.query_params.get("year", date.today().year))
            month = int(request.query_params.get("month", date.today().month))
            clients = Client.objects.filter(is_deleted=False, status=Client.Status.ACTIVE).prefetch_related(
                "client_obligations__obligation"
            )
            if not _is_manager(request.user):
                clients = clients.filter(responsible_id=request.user.id)
            serialized.extend(build_automatic_deadline_payload(clients, year=year, month=month))
            serialized = sorted(serialized, key=lambda item: item.get("due_date") or "")

        return response.Response(serialized)

    def perform_create(self, serializer):
        _ensure_client_assignment(self.request.user, serializer.validated_data.get("client"))
        deadline = serializer.save(created_by=self.request.user, source=Deadline.Source.MANUAL)
        log_model_event(
            actor=self.request.user,
            action="create",
            instance=deadline,
            after_data=get_instance_snapshot(deadline),
        )

    def perform_update(self, serializer):
        target_client = serializer.validated_data.get("client", serializer.instance.client)
        _ensure_client_assignment(self.request.user, target_client)
        before = get_instance_snapshot(serializer.instance)
        deadline = serializer.save()
        log_model_event(
            actor=self.request.user,
            action="update",
            instance=deadline,
            before_data=before,
            after_data=get_instance_snapshot(deadline),
        )

    def perform_destroy(self, instance):
        before = get_instance_snapshot(instance)
        super().perform_destroy(instance)
        log_model_event(
            actor=self.request.user,
            action="delete",
            instance=instance,
            before_data=before,
            after_data={},
        )


class SubmissionViewSet(viewsets.ModelViewSet):
    queryset = Submission.objects.select_related("client", "obligation").order_by("-created_at")
    serializer_class = SubmissionSerializer
    permission_classes = [OperationalAccessPermission]

    def get_queryset(self):
        queryset = super().get_queryset()
        if not _is_manager(self.request.user):
            queryset = queryset.filter(client__responsible_id=self.request.user.id)
        client_id = self.request.query_params.get("client")
        if client_id:
            queryset = queryset.filter(client_id=client_id)
        status_param = self.request.query_params.get("status")
        if status_param:
            queryset = queryset.filter(status=status_param)
        obligation = self.request.query_params.get("obligation")
        if obligation:
            queryset = queryset.filter(obligation_id=obligation)
        period_year = self.request.query_params.get("period_year")
        if period_year:
            queryset = queryset.filter(period_year=period_year)
        period_month = self.request.query_params.get("period_month")
        if period_month:
            queryset = queryset.filter(period_month=period_month)
        archived = parse_bool_param(self.request.query_params.get("archived"))
        if archived is True:
            queryset = queryset.filter(is_archived=True)
        elif archived is False:
            queryset = queryset.filter(is_archived=False)
        return queryset

    def perform_create(self, serializer):
        _ensure_client_assignment(self.request.user, serializer.validated_data.get("client"))
        submission = serializer.save(created_by=self.request.user)
        _normalize_submission_fields(submission)
        submission.save(
            update_fields=[
                "period_kind",
                "period_year",
                "period_month",
                "needs_manual_review",
                "updated_at",
            ]
        )
        log_model_event(
            actor=self.request.user,
            action="create",
            instance=submission,
            after_data=get_instance_snapshot(submission),
        )

    def perform_update(self, serializer):
        target_client = serializer.validated_data.get("client", serializer.instance.client)
        _ensure_client_assignment(self.request.user, target_client)
        before = get_instance_snapshot(serializer.instance)
        submission = serializer.save()
        _normalize_submission_fields(submission)
        submission.save(
            update_fields=[
                "period_kind",
                "period_year",
                "period_month",
                "needs_manual_review",
                "updated_at",
            ]
        )
        log_model_event(
            actor=self.request.user,
            action="update",
            instance=submission,
            before_data=before,
            after_data=get_instance_snapshot(submission),
        )

    def perform_destroy(self, instance):
        before = get_instance_snapshot(instance)
        super().perform_destroy(instance)
        log_model_event(
            actor=self.request.user,
            action="delete",
            instance=instance,
            before_data=before,
            after_data={},
        )

    @decorators.action(detail=True, methods=["post"], url_path="archive")
    def archive(self, request, pk=None):
        submission = self.get_object()
        if submission.status != Submission.Status.SUBMITTED:
            return response.Response(
                {"detail": "Solo obligaciones finalizadas pueden archivarse."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        before = get_instance_snapshot(submission)
        submission.is_archived = True
        submission.archived_at = timezone.now()
        submission.archived_by = request.user
        submission.save(update_fields=["is_archived", "archived_at", "archived_by", "updated_at"])
        log_model_event(
            actor=request.user,
            action="archive",
            instance=submission,
            before_data=before,
            after_data=get_instance_snapshot(submission),
        )
        return response.Response(self.get_serializer(submission).data, status=status.HTTP_200_OK)

    @decorators.action(detail=True, methods=["post"], url_path="reopen")
    def reopen(self, request, pk=None):
        submission = self.get_object()
        before = get_instance_snapshot(submission)
        submission.is_archived = False
        submission.archived_at = None
        submission.archived_by = None
        submission.save(update_fields=["is_archived", "archived_at", "archived_by", "updated_at"])
        log_model_event(
            actor=request.user,
            action="reopen",
            instance=submission,
            before_data=before,
            after_data=get_instance_snapshot(submission),
        )
        return response.Response(self.get_serializer(submission).data, status=status.HTTP_200_OK)

    @decorators.action(detail=True, methods=["post"], url_path="mark-submitted")
    def mark_submitted(self, request, pk=None):
        submission = self.get_object()
        if submission.is_archived:
            return response.Response(
                {"detail": "No podés finalizar una obligación archivada. Reabrila primero."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        before = get_instance_snapshot(submission)
        submission.status = Submission.Status.SUBMITTED
        if not submission.submitted_at:
            submission.submitted_at = timezone.localdate()
        submission.save(update_fields=["status", "submitted_at", "updated_at"])
        log_model_event(
            actor=request.user,
            action="mark_submitted",
            instance=submission,
            before_data=before,
            after_data=get_instance_snapshot(submission),
        )
        return response.Response(self.get_serializer(submission).data, status=status.HTTP_200_OK)

    @decorators.action(detail=True, methods=["post"], url_path="reactivate")
    def reactivate(self, request, pk=None):
        submission = self.get_object()
        if submission.is_archived:
            return response.Response(
                {"detail": "No podés reactivar una obligación archivada. Reabrila primero."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if submission.status != Submission.Status.SUBMITTED:
            return response.Response(
                {"detail": "Solo obligaciones finalizadas pueden reactivarse."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        before = get_instance_snapshot(submission)
        submission.status = Submission.Status.PENDING
        submission.submitted_at = None
        submission.save(update_fields=["status", "submitted_at", "updated_at"])
        log_model_event(
            actor=request.user,
            action="reactivate",
            instance=submission,
            before_data=before,
            after_data=get_instance_snapshot(submission),
        )
        return response.Response(self.get_serializer(submission).data, status=status.HTTP_200_OK)


class PendingItemViewSet(viewsets.ModelViewSet):
    queryset = PendingItem.objects.filter(is_deleted=False).order_by("priority", "expected_date")
    serializer_class = PendingItemSerializer
    permission_classes = [OperationalAccessPermission]

    def get_queryset(self):
        queryset = super().get_queryset()
        if not _is_manager(self.request.user):
            queryset = queryset.filter(client__responsible_id=self.request.user.id)
        status_param = self.request.query_params.get("status")
        if status_param:
            queryset = queryset.filter(status=status_param)
        priority = _normalize_pending_priority(self.request.query_params.get("priority"))
        if priority:
            queryset = queryset.filter(priority=priority)
        client_id = self.request.query_params.get("client")
        if client_id:
            queryset = queryset.filter(client_id=client_id)
        return queryset

    def perform_create(self, serializer):
        _ensure_client_assignment(self.request.user, serializer.validated_data.get("client"))
        item = serializer.save(created_by=self.request.user)
        log_model_event(
            actor=self.request.user,
            action="create",
            instance=item,
            after_data=get_instance_snapshot(item),
        )

    def perform_update(self, serializer):
        target_client = serializer.validated_data.get("client", serializer.instance.client)
        _ensure_client_assignment(self.request.user, target_client)
        before = get_instance_snapshot(serializer.instance)
        item = serializer.save()
        log_model_event(
            actor=self.request.user,
            action="update",
            instance=item,
            before_data=before,
            after_data=get_instance_snapshot(item),
        )

    @decorators.action(detail=True, methods=["patch"], url_path="resolve")
    def resolve(self, request, pk=None):
        item = self.get_object()
        before = get_instance_snapshot(item)
        item.status = PendingItem.Status.RESOLVED
        item.resolved_at = timezone.now()
        item.save(update_fields=["status", "resolved_at", "updated_at"])

        log_model_event(
            actor=request.user,
            action="resolve",
            instance=item,
            before_data=before,
            after_data=get_instance_snapshot(item),
        )
        serializer = self.get_serializer(item)
        return response.Response(serializer.data, status=status.HTTP_200_OK)

    def perform_destroy(self, instance):
        before = get_instance_snapshot(instance)
        instance.is_deleted = True
        instance.deleted_at = timezone.now()
        instance.deleted_by = self.request.user
        instance.save(update_fields=["is_deleted", "deleted_at", "deleted_by", "updated_at"])
        log_model_event(
            actor=self.request.user,
            action="soft_delete",
            instance=instance,
            before_data=before,
            after_data=get_instance_snapshot(instance),
        )
