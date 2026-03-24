from django.core.exceptions import ValidationError
from django.utils import timezone

from accounts.models import User
from operations.models import PendingItem

from banks.models import BankRequest


def is_manager(user) -> bool:
    return user.role in {User.Role.MASTER, User.Role.ADMIN}


def can_access_bank_request(user, item: BankRequest) -> bool:
    if is_manager(user):
        return True
    return item.responsible_id == user.id


def can_manage_bank_requests(user) -> bool:
    return is_manager(user)


def ensure_documents_loaded_for_progress(item: BankRequest):
    if item.receipts_status != BankRequest.DocumentStatus.LOADED:
        raise ValidationError("No podés avanzar: Recibos sigue en estado Pendiente.")


def _get_pending_field(document_kind: str) -> str:
    if document_kind == "receipts":
        return "receipts_pending_item"
    raise ValidationError("Tipo de documento inválido.")


def _get_status_field(document_kind: str) -> str:
    if document_kind == "receipts":
        return "receipts_status"
    raise ValidationError("Tipo de documento inválido.")


def _ensure_pending_open(item: PendingItem):
    if item.status != PendingItem.Status.OPEN:
        item.status = PendingItem.Status.OPEN
        item.resolved_at = None
    if item.is_deleted:
        item.is_deleted = False
        item.deleted_at = None
        item.deleted_by = None


def create_or_link_document_pending(
    *,
    item: BankRequest,
    actor,
    document_kind: str,
    description: str,
    missing_documents: str = "",
    expected_date=None,
    priority: str = PendingItem.Priority.URGENT,
):
    valid_priorities = {choice[0] for choice in PendingItem.Priority.choices}
    if priority not in valid_priorities:
        raise ValidationError("Prioridad de pendiente inválida.")

    pending_field = _get_pending_field(document_kind)
    status_field = _get_status_field(document_kind)
    linked_pending = getattr(item, pending_field)

    if linked_pending and linked_pending.client_id == item.client_id:
        _ensure_pending_open(linked_pending)
        linked_pending.description = description or linked_pending.description
        linked_pending.missing_documents = missing_documents
        linked_pending.expected_date = expected_date
        linked_pending.priority = priority
        linked_pending.save(
            update_fields=[
                "description",
                "missing_documents",
                "expected_date",
                "priority",
                "status",
                "resolved_at",
                "is_deleted",
                "deleted_at",
                "deleted_by",
                "updated_at",
            ]
        )
        setattr(item, status_field, BankRequest.DocumentStatus.PENDING)
        item.receipts_client_notified = False
        item.receipts_notified_by = None
        item.receipts_notified_at = None
        item.save(
            update_fields=[
                status_field,
                "receipts_client_notified",
                "receipts_notified_by",
                "receipts_notified_at",
                "updated_at",
            ]
        )
        return linked_pending, False

    new_pending = PendingItem.objects.create(
        client=item.client,
        description=description,
        missing_documents=missing_documents,
        expected_date=expected_date,
        priority=priority,
        status=PendingItem.Status.OPEN,
        created_by=actor if getattr(actor, "is_authenticated", False) else None,
    )
    setattr(item, pending_field, new_pending)
    setattr(item, status_field, BankRequest.DocumentStatus.PENDING)
    item.receipts_client_notified = False
    item.receipts_notified_by = None
    item.receipts_notified_at = None
    item.save(
        update_fields=[
            pending_field,
            status_field,
            "receipts_client_notified",
            "receipts_notified_by",
            "receipts_notified_at",
            "updated_at",
        ]
    )
    return new_pending, True


def mark_document_loaded(*, item: BankRequest, actor, document_kind: str):
    pending_field = _get_pending_field(document_kind)
    status_field = _get_status_field(document_kind)
    now = timezone.now()

    updates = [status_field, "updated_at"]
    setattr(item, status_field, BankRequest.DocumentStatus.LOADED)

    item.receipts_loaded_by = actor if getattr(actor, "is_authenticated", False) else None
    item.receipts_loaded_at = now
    updates.extend(["receipts_loaded_by", "receipts_loaded_at"])

    linked_pending = getattr(item, pending_field)
    resolved_pending = None
    if linked_pending and linked_pending.client_id == item.client_id and not linked_pending.is_deleted:
        if linked_pending.status != PendingItem.Status.RESOLVED:
            linked_pending.status = PendingItem.Status.RESOLVED
            linked_pending.resolved_at = now
            linked_pending.save(update_fields=["status", "resolved_at", "updated_at"])
            resolved_pending = linked_pending

    item.save(update_fields=updates)
    return resolved_pending


def mark_in_progress(*, item: BankRequest, actor):
    ensure_documents_loaded_for_progress(item)
    now = timezone.now()
    item.status = BankRequest.Status.IN_PROGRESS
    update_fields = ["status", "updated_at"]
    if not item.started_at:
        item.started_at = now
        item.started_by = actor if getattr(actor, "is_authenticated", False) else None
        update_fields.extend(["started_at", "started_by"])
    item.save(update_fields=update_fields)


def mark_completed(*, item: BankRequest, actor):
    ensure_documents_loaded_for_progress(item)
    now = timezone.now()
    item.status = BankRequest.Status.COMPLETED
    update_fields = ["status", "updated_at"]
    if not item.started_at:
        item.started_at = now
        item.started_by = actor if getattr(actor, "is_authenticated", False) else None
        update_fields.extend(["started_at", "started_by"])
    item.completed_at = now
    item.completed_by = actor if getattr(actor, "is_authenticated", False) else None
    update_fields.extend(["completed_at", "completed_by"])
    item.save(update_fields=update_fields)


def mark_archived(*, item: BankRequest, actor):
    if item.status != BankRequest.Status.COMPLETED:
        raise ValidationError("Solo podés archivar solicitudes en estado Realizado.")
    item.status = BankRequest.Status.ARCHIVED
    item.archived_at = timezone.now()
    item.archived_by = actor if getattr(actor, "is_authenticated", False) else None
    item.save(update_fields=["status", "archived_at", "archived_by", "updated_at"])


def reopen_archived(*, item: BankRequest):
    if item.status != BankRequest.Status.ARCHIVED:
        raise ValidationError("Solo podés reabrir solicitudes archivadas.")
    item.status = BankRequest.Status.IN_PROGRESS
    item.archived_at = None
    item.archived_by = None
    item.save(update_fields=["status", "archived_at", "archived_by", "updated_at"])
