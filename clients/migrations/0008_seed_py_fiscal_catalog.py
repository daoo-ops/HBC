from django.db import migrations


PY_FISCAL_CATALOG = [
    {
        "code": "IVA_GENERAL",
        "name": "IVA General",
        "tax_type": "IVA",
        "form_code": "120",
        "default_periodicity": "MONTHLY",
        "default_due_mode": "AUTO",
        "description": "IVA mensual - Formulario 120.",
    },
    {
        "code": "IRE_GENERAL",
        "name": "IRE General",
        "tax_type": "IRE",
        "form_code": "500",
        "default_periodicity": "ANNUAL",
        "default_due_mode": "MANUAL",
        "description": "IRE General - Formulario 500 (anual).",
    },
    {
        "code": "IRE_SIMPLE",
        "name": "IRE Simple",
        "tax_type": "IRE",
        "form_code": "501",
        "default_periodicity": "ANNUAL",
        "default_due_mode": "MANUAL",
        "description": "IRE Simple - Formulario 501 (anual).",
    },
    {
        "code": "IRE_RESIMPLE",
        "name": "IRE Resimple",
        "tax_type": "IRE",
        "form_code": "502",
        "default_periodicity": "ANNUAL",
        "default_due_mode": "MANUAL",
        "description": "IRE Resimple - Formulario 502 (anual).",
    },
    {
        "code": "IRP_SERVICIOS_PERSONALES",
        "name": "IRP Servicios Personales",
        "tax_type": "IRP",
        "form_code": "515",
        "default_periodicity": "ANNUAL",
        "default_due_mode": "MANUAL",
        "description": "IRP Servicios Personales - Formulario 515 (anual).",
    },
    {
        "code": "IRP_RENTAS_GANANCIAS_CAPITAL",
        "name": "IRP Rentas y Ganancias de Capital",
        "tax_type": "IRP",
        "form_code": "516",
        "default_periodicity": "ANNUAL",
        "default_due_mode": "MANUAL",
        "description": "IRP Rentas y Ganancias de Capital - Formulario 516 (anual).",
    },
    {
        "code": "REGISTRO_COMPROBANTES_MARANGATU",
        "name": "Registro Anual de Comprobantes (IRP-RSP / obligación 715)",
        "tax_type": "IRP-RSP",
        "form_code": "715",
        "default_periodicity": "ANNUAL",
        "default_due_mode": "MANUAL",
        "description": "Registro anual de comprobantes - obligación 715.",
    },
]


def seed_py_fiscal_catalog(apps, schema_editor):
    Obligation = apps.get_model("clients", "Obligation")

    for data in PY_FISCAL_CATALOG:
        obligation, created = Obligation.objects.get_or_create(
            code=data["code"],
            defaults={
                "name": data["name"],
                "tax_type": data["tax_type"],
                "form_code": data["form_code"],
                "is_active": True,
                "default_periodicity": data["default_periodicity"],
                "default_due_mode": data["default_due_mode"],
                "description": data["description"],
            },
        )
        if created:
            continue

        obligation.name = data["name"]
        obligation.tax_type = data["tax_type"]
        obligation.form_code = data["form_code"]
        obligation.default_periodicity = data["default_periodicity"]
        obligation.default_due_mode = data["default_due_mode"]
        obligation.description = data["description"]
        obligation.is_active = True
        obligation.save(
            update_fields=[
                "name",
                "tax_type",
                "form_code",
                "default_periodicity",
                "default_due_mode",
                "description",
                "is_active",
                "updated_at",
            ]
        )


class Migration(migrations.Migration):

    dependencies = [
        ('clients', '0007_obligation_fiscal_fields'),
    ]

    operations = [
        migrations.RunPython(seed_py_fiscal_catalog, migrations.RunPython.noop),
    ]
