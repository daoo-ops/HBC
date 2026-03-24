from datetime import date

from django.conf import settings
from django.utils import timezone

from clients.utils import normalize_ruc_base
from operations.models import Submission

# Calendario perpetuo DNIT (DJ determinativas) por terminación de RUC base.
# Referencia oficial puede sobrescribirse vía settings.HBC_RUC_DUE_DAY_MAP.
DEFAULT_RUC_DUE_DAY_MAP = {
    0: 7,
    1: 9,
    2: 11,
    3: 13,
    4: 15,
    5: 17,
    6: 19,
    7: 21,
    8: 23,
    9: 25,
}


def get_ruc_due_day_map():
    config = getattr(settings, "HBC_RUC_DUE_DAY_MAP", None)
    if not isinstance(config, dict):
        return DEFAULT_RUC_DUE_DAY_MAP
    normalized = {}
    for key, value in config.items():
        try:
            normalized[int(key)] = int(value)
        except (TypeError, ValueError):
            continue
    return normalized or DEFAULT_RUC_DUE_DAY_MAP


def get_holiday_dates():
    values = getattr(settings, "HBC_HOLIDAYS", []) or []
    holidays = set()
    for raw in values:
        if isinstance(raw, date):
            holidays.add(raw)
            continue
        try:
            holidays.add(date.fromisoformat(str(raw)))
        except (TypeError, ValueError):
            continue
    return holidays


def dnit_due_day_for_ruc(ruc: str):
    base = normalize_ruc_base(ruc)
    if not base:
        return None
    try:
        last_digit = int(base[-1])
    except (TypeError, ValueError):
        return None
    return get_ruc_due_day_map().get(last_digit)


def dnit_due_date_for_month(ruc: str, year: int, month: int):
    day = dnit_due_day_for_ruc(ruc)
    if not day:
        return None
    due = date(year, month, day)
    holidays = get_holiday_dates()
    # Ajuste a siguiente día hábil si cae fin de semana o feriado configurado.
    while due.weekday() in {5, 6} or due in holidays:
        due = date.fromordinal(due.toordinal() + 1)
    return due


def build_automatic_deadline_payload(clients, year=None, month=None):
    today = date.today()
    year = year or today.year
    month = month or today.month
    payload = []

    for client in clients:
        due = dnit_due_date_for_month(client.ruc_base or client.ruc, year, month)
        if due is None:
            continue
        client_obligations = client.client_obligations.select_related("obligation").filter(
            status="ACTIVE",
            due_mode="AUTO",
            needs_manual_review=False,
            obligation__isnull=False,
            obligation__uses_ruc_calendar=True,
            obligation__is_active=True,
        )

        delta = (due - today).days
        if delta < 0:
            priority = "URGENT"
        else:
            priority = "OK"

        for link in client_obligations:
            payload.append(
                {
                    "id": f"auto-{client.id}-{link.obligation_id}-{year}-{month}",
                    "client_id": client.id,
                    "client_name": client.name,
                    "description": f"Vencimiento DNIT ({link.obligation.name})",
                    "obligation_type": link.obligation.code,
                    "due_date": due.isoformat(),
                    "priority": priority,
                    "source": "AUTO",
                    "status": "OPEN",
                    "days_remaining": delta,
                }
            )

    payload.sort(key=lambda item: item["due_date"])
    return payload


def ensure_period_submissions_for_clients(clients, year=None, month=None):
    """
    Genera obligaciones del período (mensuales, automáticas por RUC) de forma idempotente.
    Si no se puede inferir con seguridad, no crea registros.
    """
    today = timezone.localdate()
    year = year or today.year
    month = month or today.month

    clients = list(clients)
    if not clients:
        return {"created": 0, "skipped": 0}

    client_ids = [item.id for item in clients]
    generated_keys = set()

    existing = Submission.objects.filter(
        client_id__in=client_ids,
        obligation__isnull=False,
    ).select_related("obligation")
    for item in existing:
        if not item.obligation_id:
            continue
        key = None
        if item.period_year == year and item.period_month == month:
            key = (item.client_id, item.obligation_id, year, month)
        elif item.due_date and item.due_date.year == year and item.due_date.month == month:
            key = (item.client_id, item.obligation_id, year, month)
        if key:
            generated_keys.add(key)

    created = 0
    skipped = 0

    for client in clients:
        client_obligations = client.client_obligations.select_related("obligation").filter(
            status="ACTIVE",
            due_mode="AUTO",
            needs_manual_review=False,
            obligation__isnull=False,
            obligation__is_active=True,
            obligation__uses_ruc_calendar=True,
            obligation__default_periodicity="MONTHLY",
        )

        for link in client_obligations:
            due = dnit_due_date_for_month(client.ruc_base or client.ruc, year, month)
            if due is None:
                skipped += 1
                continue

            key = (client.id, link.obligation_id, year, month)
            if key in generated_keys:
                continue

            status = Submission.Status.LATE if due < today else Submission.Status.PENDING
            Submission.objects.create(
                client_id=client.id,
                obligation_id=link.obligation_id,
                submission_type=link.obligation.name,
                period_kind=Submission.PeriodKind.MONTHLY,
                period_year=year,
                period_month=month,
                due_date=due,
                status=status,
                needs_manual_review=False,
            )
            generated_keys.add(key)
            created += 1

    return {"created": created, "skipped": skipped}
