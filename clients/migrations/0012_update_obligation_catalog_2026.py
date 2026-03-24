from django.db import migrations


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


def update_catalog(apps, schema_editor):
    Obligation = apps.get_model("clients", "Obligation")

    for code, data in CATALOG_2026.items():
        Obligation.objects.update_or_create(
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


class Migration(migrations.Migration):
    dependencies = [
        ("clients", "0011_alter_clientinvoiceperiodstatus_status"),
    ]

    operations = [
        migrations.RunPython(update_catalog, migrations.RunPython.noop),
    ]
