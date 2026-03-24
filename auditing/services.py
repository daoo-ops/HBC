from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from django.db.models import Model

from auditing.models import AuditLog


def _serialize_value(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Model):
        return value.pk
    return value


def get_instance_snapshot(instance):
    data = {}
    for field in instance._meta.fields:
        if field.name in {"password"}:
            data[field.name] = "***redacted***"
            continue
        attr_name = getattr(field, "attname", field.name)
        value = getattr(instance, attr_name, None)
        data[field.name] = _serialize_value(value)
    return data


def log_event(*, actor, action, entity, entity_id, before_data=None, after_data=None, metadata=None):
    return AuditLog.objects.create(
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        action=action,
        entity=entity,
        entity_id=str(entity_id),
        before_data=before_data or {},
        after_data=after_data or {},
        metadata=metadata or {},
    )


def log_model_event(*, actor, action, instance, before_data=None, after_data=None, metadata=None):
    return log_event(
        actor=actor,
        action=action,
        entity=instance.__class__.__name__.lower(),
        entity_id=instance.pk,
        before_data=before_data,
        after_data=after_data,
        metadata=metadata,
    )
