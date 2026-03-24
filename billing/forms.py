from django import forms

from billing.models import Charge, Contract


class DateInput(forms.DateInput):
    input_type = "date"


class ChargeForm(forms.ModelForm):
    class Meta:
        model = Charge
        fields = [
            "client",
            "contract",
            "period_month",
            "amount",
            "debt_amount",
            "currency",
            "payment_type",
            "status",
            "notes",
        ]
        widgets = {
            "period_month": DateInput(),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }
        labels = {
            "client": "Cliente",
            "contract": "Contrato",
            "period_month": "Período (mes)",
            "amount": "Monto",
            "debt_amount": "Deuda",
            "currency": "Moneda",
            "payment_type": "Tipo de pago",
            "status": "Estado",
            "notes": "Observaciones",
        }


class ContractForm(forms.ModelForm):
    class Meta:
        model = Contract
        fields = [
            "client",
            "start_date",
            "end_date",
            "monthly_amount",
            "currency",
            "active",
            "notes",
        ]
        widgets = {
            "start_date": DateInput(),
            "end_date": DateInput(),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }
        labels = {
            "client": "Cliente",
            "start_date": "Fecha de inicio",
            "end_date": "Fecha de fin",
            "monthly_amount": "Monto mensual",
            "currency": "Moneda",
            "active": "Activo",
            "notes": "Observaciones",
        }
