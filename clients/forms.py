from django import forms

from accounts.models import User
from clients.models import Client, ClientNote, ClientObligation, Obligation
from clients.utils import normalize_ruc


class DateInput(forms.DateInput):
    input_type = "date"


class BaseClientForm(forms.ModelForm):
    obligations = forms.ModelMultipleChoiceField(
        queryset=Obligation.objects.filter(is_active=True).order_by("name"),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Obligaciones / presentaciones",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        field_labels = {
            "name": "Nombre",
            "ruc": "RUC",
            "ruc_dv": "DV",
            "responsible": "Responsable",
            "phone": "Teléfono",
            "address": "Dirección",
            "zone": "Zona",
            "presentation_type": "Tipo de presentación",
            "due_date": "Vencimiento",
            "submission_date": "Fecha de presentación",
            "pending_notes": "Pendientes / documentos faltantes",
            "observations": "Observaciones",
            "monthly_amount_pyg": "Honorario PYG",
            "monthly_amount_usd": "Honorario USD",
            "paid": "Pagado",
            "debt_amount": "Deuda",
            "contract_until": "Contrato hasta",
            "invoice_period_status": "Facturas del período",
            "status": "Estado",
            "obligations": "Obligaciones fiscales",
        }
        for key, label in field_labels.items():
            if key in self.fields:
                self.fields[key].label = label
        if "invoice_period_status" in self.fields:
            self.fields["invoice_period_status"].required = False
            if self.instance and self.instance.pk:
                self.fields["invoice_period_status"].initial = self.instance.invoice_period_status
            else:
                self.fields["invoice_period_status"].initial = Client.InvoicePeriodStatus.PENDING
        if self.instance and self.instance.pk:
            selected_ids = self.instance.client_obligations.filter(
                obligation__isnull=False,
                status=ClientObligation.Status.ACTIVE,
            ).values_list("obligation_id", flat=True)
            self.fields["obligations"].initial = list(selected_ids)

    def save(self, commit=True):
        client = super().save(commit=commit)

        def _sync_obligations():
            selected = set(self.cleaned_data.get("obligations", []).values_list("id", flat=True))
            existing = {
                link.obligation_id: link
                for link in client.client_obligations.filter(obligation__isnull=False)
            }

            for obligation_id in selected:
                link = existing.get(obligation_id)
                if link:
                    updates = []
                    if link.status != ClientObligation.Status.ACTIVE:
                        link.status = ClientObligation.Status.ACTIVE
                        updates.append("status")
                    if link.needs_manual_review:
                        link.needs_manual_review = False
                        updates.append("needs_manual_review")
                    if updates:
                        updates.append("updated_at")
                        link.save(update_fields=updates)
                else:
                    obligation = Obligation.objects.get(id=obligation_id)
                    ClientObligation.objects.create(
                        client=client,
                        obligation=obligation,
                        status=ClientObligation.Status.ACTIVE,
                        periodicity=obligation.default_periodicity or ClientObligation.Periodicity.MONTHLY,
                        due_mode=obligation.default_due_mode or ClientObligation.DueMode.AUTO,
                    )

            for obligation_id, link in existing.items():
                if obligation_id not in selected and link.status != ClientObligation.Status.INACTIVE:
                    link.status = ClientObligation.Status.INACTIVE
                    link.save(update_fields=["status", "updated_at"])

        if commit:
            _sync_obligations()
        else:
            self.save_m2m = _sync_obligations

        return client

    def clean_ruc_dv(self):
        value = (self.cleaned_data.get("ruc_dv") or "").strip()
        if not value:
            return ""
        return normalize_ruc(value).replace("-", "")

    def clean_invoice_period_status(self):
        value = (self.cleaned_data.get("invoice_period_status") or "").strip()
        if not value:
            return Client.InvoicePeriodStatus.PENDING
        return value


class ClientForm(BaseClientForm):
    class Meta:
        model = Client
        fields = [
            "name",
            "ruc",
            "ruc_dv",
            "responsible",
            "phone",
            "address",
            "zone",
            "presentation_type",
            "due_date",
            "submission_date",
            "pending_notes",
            "observations",
            "monthly_amount_pyg",
            "monthly_amount_usd",
            "paid",
            "debt_amount",
            "contract_until",
            "invoice_period_status",
            "status",
            "obligations",
        ]
        widgets = {
            "ruc_dv": forms.TextInput(attrs={"placeholder": "Opcional"}),
            "due_date": DateInput(),
            "submission_date": DateInput(),
            "contract_until": DateInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["responsible"].queryset = User.objects.filter(role=User.Role.FUNCIONARIO).order_by("username")

    def clean_responsible(self):
        responsible = self.cleaned_data.get("responsible")
        if not responsible:
            return None
        if responsible.role != User.Role.FUNCIONARIO:
            raise forms.ValidationError("El responsable debe ser un usuario con rol FUNCIONARIO.")
        return responsible


class ClientOperationalForm(BaseClientForm):
    class Meta:
        model = Client
        fields = [
            "name",
            "ruc",
            "ruc_dv",
            "phone",
            "address",
            "zone",
            "presentation_type",
            "due_date",
            "submission_date",
            "pending_notes",
            "observations",
            "invoice_period_status",
            "status",
            "obligations",
        ]
        widgets = {
            "ruc_dv": forms.TextInput(attrs={"placeholder": "Opcional"}),
            "due_date": DateInput(),
            "submission_date": DateInput(),
        }


class ClientNoteForm(forms.ModelForm):
    class Meta:
        model = ClientNote
        fields = ["note"]
        widgets = {
            "note": forms.Textarea(attrs={"rows": 4}),
        }
        labels = {
            "note": "Observación",
        }
