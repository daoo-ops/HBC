from typing import Optional

from auditing.services import log_model_event
from accounts.models import User
from clients.models import Client, ClientResponsibilityHistory


def track_responsible_change(
    *,
    client: Client,
    old_responsible_id: Optional[int],
    actor,
    reason: str = "",
):
    new_responsible_id = client.responsible_id
    if old_responsible_id == new_responsible_id:
        return

    old_responsible = None
    new_responsible = None
    if old_responsible_id:
        old_responsible = User.objects.filter(id=old_responsible_id).first()
    if new_responsible_id:
        new_responsible = User.objects.filter(id=new_responsible_id).first()

    ClientResponsibilityHistory.objects.create(
        client=client,
        old_responsible=old_responsible,
        new_responsible=new_responsible,
        changed_by=actor,
        reason=reason or "",
    )

    log_model_event(
        actor=actor,
        action="reassign_responsible",
        instance=client,
        before_data={
            "responsible_id": old_responsible_id,
            "responsible_username": old_responsible.username if old_responsible else "",
        },
        after_data={
            "responsible_id": new_responsible_id,
            "responsible_username": new_responsible.username if new_responsible else "",
            "reason": reason or "",
        },
    )
