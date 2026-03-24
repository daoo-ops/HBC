from django import forms

from operations.models import PendingItem, Submission


class DateInput(forms.DateInput):
    input_type = "date"


class PendingItemForm(forms.ModelForm):
    class Meta:
        model = PendingItem
        fields = [
            "client",
            "description",
            "missing_documents",
            "expected_date",
            "priority",
            "status",
        ]
        widgets = {
            "expected_date": DateInput(),
            "missing_documents": forms.Textarea(attrs={"rows": 3}),
        }
        labels = {
            "client": "Cliente",
            "description": "Descripción",
            "missing_documents": "Documentos faltantes",
            "expected_date": "Fecha esperada",
            "priority": "Prioridad",
            "status": "Estado",
        }


class SubmissionForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "obligation" in self.fields:
            self.fields["obligation"].queryset = self.fields["obligation"].queryset.filter(is_active=True).order_by("name")

    def clean(self):
        cleaned = super().clean()
        period_kind = cleaned.get("period_kind")
        period_year = cleaned.get("period_year")
        period_month = cleaned.get("period_month")

        if period_kind == Submission.PeriodKind.MONTHLY:
            if not period_year or not period_month:
                raise forms.ValidationError("Para período mensual, año y mes son obligatorios.")
            if period_month < 1 or period_month > 12:
                self.add_error("period_month", "Mes inválido.")
        elif period_kind == Submission.PeriodKind.ANNUAL:
            if not period_year:
                raise forms.ValidationError("Para período anual, el año es obligatorio.")
            cleaned["period_month"] = None
        elif period_kind and not period_year:
            raise forms.ValidationError("Indicá al menos el año del período.")

        return cleaned

    class Meta:
        model = Submission
        fields = [
            "client",
            "obligation",
            "submission_type",
            "period_kind",
            "period_year",
            "period_month",
            "due_date",
            "submitted_at",
            "status",
            "notes",
        ]
        widgets = {
            "period_year": forms.NumberInput(attrs={"min": 2000, "max": 2100}),
            "period_month": forms.NumberInput(attrs={"min": 1, "max": 12}),
            "due_date": DateInput(),
            "submitted_at": DateInput(),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }
        labels = {
            "client": "Cliente",
            "obligation": "Obligación fiscal",
            "submission_type": "Obligación (texto)",
            "period_kind": "Tipo de período",
            "period_year": "Año",
            "period_month": "Mes",
            "due_date": "Vencimiento",
            "submitted_at": "Fecha de presentación",
            "status": "Estado",
            "notes": "Observaciones",
        }
