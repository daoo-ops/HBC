from datetime import timedelta
from typing import Iterable, Optional, Set

from django.utils import timezone

from notifications.models import UserNotification


DEDUP_WINDOW = timedelta(minutes=2)


def _as_user_id_set(values: Optional[Iterable]) -> Set[int]:
    user_ids: Set[int] = set()
    if not values:
        return user_ids
    for value in values:
        if not value:
            continue
        if isinstance(value, int):
            user_ids.add(value)
            continue
        value_id = getattr(value, "id", None)
        if value_id:
            user_ids.add(value_id)
    return user_ids


def recipients_for_client(*, client, extras: Optional[Iterable] = None) -> Set[int]:
    recipient_ids = _as_user_id_set(extras)
    if client and client.responsible_id:
        recipient_ids.add(client.responsible_id)
    return recipient_ids


def recipients_for_bank_request(*, bank_request, extras: Optional[Iterable] = None) -> Set[int]:
    recipient_ids = _as_user_id_set(extras)
    if bank_request.responsible_id:
        recipient_ids.add(bank_request.responsible_id)
    if bank_request.requested_by_id:
        recipient_ids.add(bank_request.requested_by_id)
    if bank_request.client_id and bank_request.client and bank_request.client.responsible_id:
        recipient_ids.add(bank_request.client.responsible_id)
    return recipient_ids


def notify_users(
    *,
    actor,
    recipient_ids: Iterable[int],
    message: str,
    severity: str = UserNotification.Severity.NORMAL,
    client=None,
    target_url: str = "",
    event_key: str = "",
    source_ref: str = "",
) -> int:
    user_ids = _as_user_id_set(recipient_ids)
    actor_id = getattr(actor, "id", None)
    if actor_id:
        user_ids.discard(actor_id)

    if not user_ids:
        return 0

    now = timezone.now()
    dedup_from = now - DEDUP_WINDOW
    created_count = 0

    for recipient_id in user_ids:
        if event_key and UserNotification.objects.filter(
            recipient_id=recipient_id,
            event_key=event_key,
            source_ref=source_ref,
            created_at__gte=dedup_from,
        ).exists():
            continue
        UserNotification.objects.create(
            recipient_id=recipient_id,
            actor=actor if getattr(actor, "is_authenticated", False) else None,
            client=client,
            severity=severity,
            message=message[:255],
            target_url=(target_url or "")[:255],
            event_key=(event_key or "")[:120],
            source_ref=(source_ref or "")[:120],
        )
        created_count += 1

    return created_count
