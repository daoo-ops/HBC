from django import forms
from django.core.exceptions import ValidationError

from payment_logs.models import PaymentReceptionLog


class PaymentReceptionLogForm(forms.ModelForm):
    class Meta:
        model = PaymentReceptionLog
        fields = [
            "client",
            "payment_date",
            "paid_by",
            "concept_type",
            "concept_other",
            "payment_method",
            "third_party_check_name",
            "observation",
        ]
        labels = {
            "client": "Cliente",
            "payment_date": "Fecha de recepción",
            "paid_by": "Quién pagó",
            "concept_type": "Concepto",
            "concept_other": "Otro concepto",
            "payment_method": "Medio de pago",
            "third_party_check_name": "Cheque de tercero (nombre)",
            "observation": "Observación",
        }
        widgets = {
            "payment_date": forms.DateInput(attrs={"type": "date"}),
            "paid_by": forms.TextInput(attrs={"placeholder": "Nombre de quien realizó el pago"}),
            "concept_other": forms.TextInput(attrs={"placeholder": "Describir el concepto"}),
            "third_party_check_name": forms.TextInput(attrs={"placeholder": "Nombre del tercero del cheque"}),
            "observation": forms.Textarea(attrs={"rows": 3, "placeholder": "Observación operativa (opcional)"}),
        }

    def clean(self):
        cleaned_data = super().clean()
        if not cleaned_data:
            return cleaned_data

        # Reutiliza la validación de dominio del modelo para mantener coherencia.
        instance = self.instance or PaymentReceptionLog()
        for field_name in self.fields:
            if field_name in cleaned_data:
                setattr(instance, field_name, cleaned_data.get(field_name))

        try:
            instance.clean()
        except ValidationError as exc:
            if hasattr(exc, "message_dict"):
                for field, messages in exc.message_dict.items():
                    for message in messages:
                        self.add_error(field if field in self.fields else None, message)
            else:
                self.add_error(None, exc)

        # Refleja en el formulario la limpieza que hace el modelo.
        if "concept_other" in cleaned_data:
            cleaned_data["concept_other"] = instance.concept_other
        if "third_party_check_name" in cleaned_data:
            cleaned_data["third_party_check_name"] = instance.third_party_check_name
        return cleaned_data
