import unicodedata
from django.db import migrations


OBLIGATION_CODE_BY_HINT = {
    "IVA_GENERAL": ["IVA", "FORMULARIO 120", "F120"],
    "IRE_GENERAL": ["IRE GENERAL", "FORMULARIO 500", "F500"],
    "IRE_SIMPLE": ["IRE SIMPLE", "FORMULARIO 501", "F501"],
    "IRE_RESIMPLE": ["IRE RESIMPLE", "IRE RE SIMPLE", "RESIMPLE", "FORMULARIO 502", "F502"],
    "IRP_SERVICIOS_PERSONALES": [
        "IRP SERVICIOS PERSONALES",
        "SERVICIOS PERSONALES",
        "FORMULARIO 515",
        "F515",
        "FORMULARIO 104",
        "F104",
    ],
    "IRP_RENTAS_GANANCIAS_CAPITAL": [
        "IRP RENTAS Y GANANCIAS DE CAPITAL",
        "GANANCIAS DE CAPITAL",
        "RENTAS DE CAPITAL",
        "FORMULARIO 516",
        "F516",
        "FORMULARIO 104",
        "F104",
    ],
    "REGISTRO_COMPROBANTES_MARANGATU": [
        "REGISTRO DE COMPROBANTES",
        "MARANGATU",
        "REGISTRO ANUAL DE COMPROBANTES",
        "IRP RSP",
        "OBLIGACION 715",
        "715",
    ],
}


def _normalize_text(value):
    text = value or ""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = text.replace("-", " ").replace("_", " ").replace("/", " ")
    return " ".join(text.upper().split())


def _infer_obligation_code(raw_value):
    normalized = _normalize_text(raw_value)
    if not normalized:
        return None, True

    matches = set()
    for code, hints in OBLIGATION_CODE_BY_HINT.items():
        if any(hint in normalized for hint in hints):
            matches.add(code)

    if "IRE" in normalized:
        ire_matches = {"IRE_GENERAL", "IRE_SIMPLE", "IRE_RESIMPLE"} & matches
        if len(ire_matches) != 1:
            return None, True

    if "IRP" in normalized:
        irp_matches = {"IRP_SERVICIOS_PERSONALES", "IRP_RENTAS_GANANCIAS_CAPITAL"} & matches
        if len(irp_matches) != 1:
            return None, True

    if len(matches) != 1:
        return None, True
    return next(iter(matches)), False


def _infer_period_kind(obligation):
    periodicity = (obligation.default_periodicity or "").upper()
    if periodicity == "MONTHLY":
        return "MONTHLY"
    if periodicity == "ANNUAL":
        return "ANNUAL"
    return "OTHER"


def backfill_submission_fiscal_fields(apps, schema_editor):
    Submission = apps.get_model("operations", "Submission")
    Obligation = apps.get_model("clients", "Obligation")

    obligation_by_code = {item.code: item for item in Obligation.objects.all().iterator()}

    for submission in Submission.objects.select_related("obligation").all().iterator():
        obligation = submission.obligation
        ambiguous = False

        if not obligation:
            code, ambiguous = _infer_obligation_code(submission.submission_type)
            if code:
                obligation = obligation_by_code.get(code)
                submission.obligation_id = obligation.id if obligation else None

        if obligation and not submission.period_kind:
            submission.period_kind = _infer_period_kind(obligation)

        reference_date = submission.due_date or submission.submitted_at
        if submission.period_kind == "MONTHLY":
            if not submission.period_year and reference_date:
                submission.period_year = reference_date.year
            if not submission.period_month and reference_date:
                submission.period_month = reference_date.month
        elif submission.period_kind == "ANNUAL":
            if not submission.period_year and reference_date:
                submission.period_year = reference_date.year
            submission.period_month = None
        elif submission.period_kind == "OTHER":
            if not submission.period_year and reference_date:
                submission.period_year = reference_date.year
            if not submission.period_month and reference_date:
                submission.period_month = reference_date.month

        has_confident_mapping = bool(submission.obligation_id and submission.period_year)
        submission.needs_manual_review = (not has_confident_mapping) or ambiguous

        submission.save(
            update_fields=[
                "obligation",
                "period_kind",
                "period_year",
                "period_month",
                "needs_manual_review",
                "updated_at",
            ]
        )


class Migration(migrations.Migration):

    dependencies = [
        ('operations', '0004_submission_fiscal_fields_nullable'),
    ]

    operations = [
        migrations.RunPython(backfill_submission_fiscal_fields, migrations.RunPython.noop),
    ]
