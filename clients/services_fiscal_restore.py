import re
import unicodedata

from clients.models import Client, ClientObligation, Obligation
from operations.models import Submission
from operations.services import ensure_period_submissions_for_clients


CATALOG_2026 = {
    "IRE_GENERAL": {
        "name": "IRE General",
        "tax_type": "IRE",
        "form_code": "500",
        "default_periodicity": "ANNUAL",
        "default_due_mode": "MANUAL",
        "uses_ruc_calendar": True,
        "description": "IRE General - Formulario 500 (anual).",
    },
    "IRE_SIMPLE": {
        "name": "IRE Simple",
        "tax_type": "IRE",
        "form_code": "501",
        "default_periodicity": "ANNUAL",
        "default_due_mode": "MANUAL",
        "uses_ruc_calendar": True,
        "description": "IRE Simple - Formulario 501 (anual).",
    },
    "IRE_RESIMPLE": {
        "name": "IRE Resimple",
        "tax_type": "IRE",
        "form_code": "502",
        "default_periodicity": "ANNUAL",
        "default_due_mode": "MANUAL",
        "uses_ruc_calendar": True,
        "description": "IRE Resimple - Formulario 502 (anual).",
    },
    "IRP_SERVICIOS_PERSONALES": {
        "name": "IRP Servicios Personales",
        "tax_type": "IRP",
        "form_code": "515",
        "default_periodicity": "ANNUAL",
        "default_due_mode": "MANUAL",
        "uses_ruc_calendar": True,
        "description": "IRP Servicios Personales - Formulario 515 (anual).",
    },
    "IRP_RENTAS_GANANCIAS_CAPITAL": {
        "name": "IRP Rentas y Ganancias de Capital",
        "tax_type": "IRP",
        "form_code": "516",
        "default_periodicity": "ANNUAL",
        "default_due_mode": "MANUAL",
        "uses_ruc_calendar": True,
        "description": "IRP Rentas y Ganancias de Capital - Formulario 516 (anual).",
    },
    "IVA_GENERAL": {
        "name": "IVA General",
        "tax_type": "IVA",
        "form_code": "120",
        "default_periodicity": "MONTHLY",
        "default_due_mode": "AUTO",
        "uses_ruc_calendar": True,
        "description": "IVA mensual - Formulario 120.",
    },
    "REGISTRO_COMPROBANTES_MARANGATU": {
        "name": "Registro Anual de Comprobantes (IRP-RSP / obligación 715)",
        "tax_type": "IRP-RSP",
        "form_code": "715",
        "default_periodicity": "ANNUAL",
        "default_due_mode": "MANUAL",
        "uses_ruc_calendar": False,
        "description": "Registro anual de comprobantes - obligación 715.",
    },
}


def _normalize_text(value: str) -> str:
    text = value or ""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = text.replace("-", " ").replace("_", " ").replace("/", " ")
    return " ".join(text.upper().split())


def infer_obligation_codes(raw_value: str):
    normalized = _normalize_text(raw_value)
    if not normalized:
        return [], False

    codes = set()

    if "IVA" in normalized:
        codes.add("IVA_GENERAL")

    if (
        "IRE RESIMPLE" in normalized
        or "IRE RE SIMPLE" in normalized
        or "RESIMPLE" in normalized
        or "F502" in normalized
    ):
        codes.add("IRE_RESIMPLE")
    if "IRE SIMPLE" in normalized or "F501" in normalized:
        codes.add("IRE_SIMPLE")
    if "IRE GENERAL" in normalized or "F500" in normalized:
        codes.add("IRE_GENERAL")

    if (
        "IRP SERVICIOS PERSONALES" in normalized
        or "IRP SERVICIO" in normalized
        or "SERVICIOS PERSONALES" in normalized
        or "F515" in normalized
    ):
        codes.add("IRP_SERVICIOS_PERSONALES")
    if (
        "IRP RENTAS Y GANANCIAS DE CAPITAL" in normalized
        or "IRP RENTA" in normalized
        or "GANANCIAS DE CAPITAL" in normalized
        or "F516" in normalized
    ):
        codes.add("IRP_RENTAS_GANANCIAS_CAPITAL")

    if (
        "MARANGATU" in normalized
        or "REGISTRO DE COMPROBANTES" in normalized
        or "REGISTRO ANUAL DE COMPROBANTES" in normalized
        or "IRP RSP" in normalized
        or bool(re.search(r"\b715\b", normalized))
    ):
        codes.add("REGISTRO_COMPROBANTES_MARANGATU")

    ambiguous = False
    ire_codes = {"IRE_GENERAL", "IRE_SIMPLE", "IRE_RESIMPLE"} & codes
    if "IRE" in normalized and not ire_codes:
        ambiguous = True
    if len(ire_codes) > 1:
        ambiguous = True

    irp_codes = {"IRP_SERVICIOS_PERSONALES", "IRP_RENTAS_GANANCIAS_CAPITAL"} & codes
    if "IRP" in normalized and not irp_codes:
        ambiguous = True

    if not codes:
        ambiguous = True

    return sorted(codes), ambiguous


def restore_obligation_catalog():
    stats = {
        "created": 0,
        "updated": 0,
        "total_after": 0,
    }
    obligation_by_code = {}

    for code, data in CATALOG_2026.items():
        obligation, created = Obligation.objects.update_or_create(
            code=code,
            defaults={
                "name": data["name"],
                "tax_type": data["tax_type"],
                "form_code": data["form_code"],
                "default_periodicity": data["default_periodicity"],
                "default_due_mode": data["default_due_mode"],
                "uses_ruc_calendar": data["uses_ruc_calendar"],
                "description": data["description"],
                "is_active": True,
            },
        )
        obligation_by_code[code] = obligation
        if created:
            stats["created"] += 1
        else:
            stats["updated"] += 1

    stats["total_after"] = Obligation.objects.count()
    return stats, obligation_by_code


def _upsert_review_placeholder(client, presentation_type, note):
    placeholder = (
        ClientObligation.objects.filter(client_id=client.id, obligation__isnull=True)
        .order_by("id")
        .first()
    )
    defaults = {
        "status": ClientObligation.Status.ACTIVE,
        "periodicity": ClientObligation.Periodicity.MONTHLY,
        "due_mode": ClientObligation.DueMode.MANUAL,
        "needs_manual_review": True,
        "source_presentation_type": presentation_type[:255],
        "observations": note,
    }
    if placeholder is None:
        ClientObligation.objects.create(client_id=client.id, obligation_id=None, **defaults)
        return "created"

    changed = False
    for key, value in defaults.items():
        if getattr(placeholder, key) != value:
            setattr(placeholder, key, value)
            changed = True
    if changed:
        placeholder.save()
        return "updated"
    return "noop"


def rebuild_client_obligations(clients_qs, obligation_by_code):
    stats = {
        "clients_evaluated": 0,
        "clients_with_presentation_type": 0,
        "clients_without_presentation_type": 0,
        "links_created": 0,
        "links_updated": 0,
        "review_placeholders_created": 0,
        "review_placeholders_updated": 0,
        "ambiguous_clients": 0,
        "unmapped_clients": 0,
    }

    for client in clients_qs.iterator():
        stats["clients_evaluated"] += 1
        presentation_type = (client.presentation_type or "").strip()
        if not presentation_type:
            stats["clients_without_presentation_type"] += 1
            continue
        stats["clients_with_presentation_type"] += 1

        codes, ambiguous = infer_obligation_codes(presentation_type)
        if ambiguous:
            stats["ambiguous_clients"] += 1

        if not codes:
            stats["unmapped_clients"] += 1
            result = _upsert_review_placeholder(
                client=client,
                presentation_type=presentation_type,
                note="No se pudo mapear automáticamente desde presentation_type. Revisión manual requerida.",
            )
            if result == "created":
                stats["review_placeholders_created"] += 1
            elif result == "updated":
                stats["review_placeholders_updated"] += 1
            continue

        for code in codes:
            obligation = obligation_by_code.get(code)
            if not obligation:
                continue

            defaults = {
                "status": ClientObligation.Status.ACTIVE,
                "periodicity": obligation.default_periodicity or ClientObligation.Periodicity.MONTHLY,
                "due_mode": obligation.default_due_mode or ClientObligation.DueMode.AUTO,
                "needs_manual_review": ambiguous,
                "source_presentation_type": presentation_type[:255],
                "observations": (
                    "Asignación automática con ambigüedad. Revisión manual recomendada."
                    if ambiguous
                    else ""
                ),
            }
            _, created = ClientObligation.objects.update_or_create(
                client_id=client.id,
                obligation_id=obligation.id,
                defaults=defaults,
            )
            if created:
                stats["links_created"] += 1
            else:
                stats["links_updated"] += 1

    return stats


def regenerate_submissions_for_period(year: int, month: int):
    clients_scope = (
        Client.objects.filter(is_deleted=False, status=Client.Status.ACTIVE)
        .prefetch_related("client_obligations__obligation")
    )
    result = ensure_period_submissions_for_clients(clients_scope, year=year, month=month)
    return {
        "created": result.get("created", 0),
        "skipped": result.get("skipped", 0),
        "total_after": Submission.objects.count(),
    }
