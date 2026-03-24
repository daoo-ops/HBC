from django import forms

from accounts.models import User
from banks.models import BankRequest


class DateInput(forms.DateInput):
    input_type = "date"


class BankRequestForm(forms.ModelForm):
    class Meta:
        model = BankRequest
        fields = [
            "client",
            "request_type",
            "request_type_other",
            "operational_reason",
            "operational_reason_other",
            "request_priority",
            "responsible",
            "notes",
        ]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 4}),
        }
        labels = {
            "client": "Cliente",
            "request_type": "Tipo de solicitud",
            "request_type_other": "Otro tipo",
            "operational_reason": "Motivo operativo",
            "operational_reason_other": "Otro motivo",
            "request_priority": "Prioridad",
            "responsible": "Responsable",
            "notes": "Observaciones",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["responsible"].queryset = User.objects.filter(role=User.Role.FUNCIONARIO).order_by("username")
        self.fields["notes"].help_text = "Opcional."
        self.fields["request_priority"].help_text = "Define la prioridad operativa de la solicitud."

    def clean(self):
        cleaned = super().clean()
        reason = cleaned.get("operational_reason")
        reason_other = (cleaned.get("operational_reason_other") or "").strip()
        if reason == BankRequest.OperationalReason.OTHER and not reason_other:
            self.add_error("operational_reason_other", "Debe especificar el motivo cuando selecciona 'Otro'.")
        if reason != BankRequest.OperationalReason.OTHER:
            cleaned["operational_reason_other"] = ""
        return cleaned


class BankRequestNoteForm(forms.Form):
    note = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}), label="Observación")
