from django import forms
from decimal import Decimal, InvalidOperation
from django.utils.dateparse import parse_date

from accounts.models import User
from tax_commitments.models import TaxCommitment
from tax_commitments.services import build_installment_dates, split_amount_into_installments


class DateInput(forms.DateInput):
    input_type = "date"


class TaxCommitmentForm(forms.ModelForm):
    installments_count = forms.IntegerField(
        required=False,
        min_value=1,
        max_value=120,
        label="Cantidad de cuotas",
        help_text="1 = cuota única. Mayor a 1 = plan en cuotas.",
    )
    installment_date_mode = forms.ChoiceField(
        required=False,
        label="Tipo de fechas",
        choices=(
            ("AUTO", "Automáticas"),
            ("MANUAL", "Manuales"),
        ),
        initial="AUTO",
        help_text="Automáticas: secuencia mensual. Manuales: fecha por cuota.",
    )

    class Meta:
        model = TaxCommitment
        fields = [
            "client",
            "commitment_type",
            "installment_mode",
            "type_other",
            "reference_number",
            "period_reference",
            "due_date",
            "amount",
            "currency",
            "installments_count",
            "installment_date_mode",
            "notes",
        ]
        widgets = {
            "due_date": DateInput(),
            "notes": forms.Textarea(attrs={"rows": 2}),
            "type_other": forms.TextInput(attrs={"placeholder": "Especificar tipo"}),
            "reference_number": forms.TextInput(attrs={"placeholder": "Ref. N°"}),
            "period_reference": forms.TextInput(attrs={"placeholder": "Período o referencia"}),
        }
        labels = {
            "client": "Cliente",
            "commitment_type": "Tipo de compromiso",
            "installment_mode": "Modo de cuotas",
            "type_other": "Otro tipo",
            "reference_number": "Ref. N°",
            "period_reference": "Período / referencia",
            "due_date": "Vencimiento",
            "amount": "Monto",
            "currency": "Moneda",
            "notes": "Observación",
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        self._manual_installment_amounts = []
        self._manual_installment_dates = []
        self.fields["installment_mode"].required = False
        self.fields["installment_mode"].initial = TaxCommitment.InstallmentMode.AUTO
        self.fields["installment_date_mode"].required = False
        self.fields["installment_date_mode"].initial = "AUTO"
        self.fields["due_date"].required = False
        self.fields["amount"].required = False
        self.fields["amount"].help_text = "Monto total en AUTOMÁTICO o monto único cuando no hay cuotas."
        self.fields["client"].help_text = "Busque por nombre o RUC."
        self.fields["client"].label_from_instance = self._client_option_label
        if self.instance and self.instance.pk:
            self.fields["installments_count"].initial = self.instance.installment_total or 1
            self.fields["installment_mode"].initial = self.instance.installment_mode or TaxCommitment.InstallmentMode.AUTO

        if user and user.role == User.Role.FUNCIONARIO:
            self.fields["client"].queryset = self.fields["client"].queryset.filter(
                is_deleted=False,
                responsible_id=user.id,
            )
        else:
            self.fields["client"].queryset = self.fields["client"].queryset.filter(is_deleted=False)

    @staticmethod
    def _client_option_label(client):
        base = (client.ruc_base or client.ruc or "").strip()
        dv = (client.ruc_digit or "").strip()
        if base and dv:
            return f"{client.name} - {base}-{dv}"
        if base:
            return f"{client.name} - {base}"
        return client.name

    def clean(self):
        cleaned = super().clean()
        installments_count = cleaned.get("installments_count") or 1
        due_date = cleaned.get("due_date")
        amount = cleaned.get("amount")
        commitment_type = cleaned.get("commitment_type")
        type_other = (cleaned.get("type_other") or "").strip()
        currency = cleaned.get("currency") or TaxCommitment.Currency.PYG
        mode = cleaned.get("installment_mode") or TaxCommitment.InstallmentMode.AUTO
        date_mode = (cleaned.get("installment_date_mode") or "AUTO").upper()
        # Backward-compatibility: old clients/tests may still submit the legacy
        # checkbox name instead of installment_date_mode.
        if "installment_date_mode" not in self.data:
            legacy_customize = str(self.data.get("customize_installment_dates") or "").strip().lower()
            if legacy_customize in {"1", "true", "on", "yes"}:
                date_mode = "MANUAL"
        if date_mode not in {"AUTO", "MANUAL"}:
            date_mode = "AUTO"
        cleaned["installment_date_mode"] = date_mode
        customize_dates = date_mode == "MANUAL"
        if commitment_type == TaxCommitment.CommitmentType.FACILIDAD and not cleaned.get("installment_mode"):
            mode = TaxCommitment.InstallmentMode.MANUAL
        cleaned["installment_mode"] = mode

        if commitment_type == TaxCommitment.CommitmentType.OTHER and not type_other:
            self.add_error("type_other", "Debe especificar el tipo cuando selecciona 'Otro'.")

        is_generating = installments_count > 1 and not (self.instance and self.instance.pk)

        if is_generating:
            if not customize_dates and not due_date:
                self.add_error("due_date", "Debe indicar el primer vencimiento para generar cuotas.")
            if mode == TaxCommitment.InstallmentMode.AUTO:
                if amount is None:
                    self.add_error("amount", "Debe indicar el monto total para generar cuotas automáticas.")
                elif amount <= 0:
                    self.add_error("amount", "El monto total debe ser mayor a cero.")
                else:
                    try:
                        split_amount_into_installments(amount, installments_count, currency)
                    except ValueError as exc:
                        self.add_error("amount", str(exc))
            else:
                raw_values = self.data.getlist("manual_amounts")
                parsed_values = []
                for index, raw in enumerate(raw_values, start=1):
                    token = str(raw or "").strip().replace(",", ".")
                    if not token:
                        continue
                    try:
                        number = Decimal(token)
                    except InvalidOperation:
                        self.add_error(None, f"Monto manual de cuota {index} inválido.")
                        continue
                    if number <= 0:
                        self.add_error(None, f"Monto manual de cuota {index} debe ser mayor a cero.")
                        continue
                    parsed_values.append(number)

                if len(parsed_values) != installments_count:
                    self.add_error(
                        None,
                        f"En modo manual debe completar exactamente {installments_count} montos de cuota.",
                    )
                self._manual_installment_amounts = parsed_values
                if amount is not None and parsed_values:
                    manual_total = sum(parsed_values, Decimal("0"))
                    if manual_total != amount:
                        self.add_error(
                            "amount",
                            "El monto total no coincide con la suma manual de cuotas.",
                        )

            if customize_dates:
                raw_dates = self.data.getlist("manual_due_dates")
                parsed_dates = []
                for index, raw in enumerate(raw_dates, start=1):
                    token = str(raw or "").strip()
                    if not token:
                        continue
                    parsed = parse_date(token)
                    if not parsed:
                        self.add_error(None, f"Fecha manual de cuota {index} inválida.")
                        continue
                    parsed_dates.append(parsed)
                if len(parsed_dates) != installments_count:
                    self.add_error(
                        None,
                        f"Si personaliza fechas debe completar exactamente {installments_count} fechas de cuota.",
                    )
                self._manual_installment_dates = parsed_dates
            else:
                self._manual_installment_dates = []

        else:
            if not due_date:
                if self.instance and self.instance.pk:
                    cleaned["due_date"] = self.instance.due_date
                else:
                    self.add_error("due_date", "Debe indicar el vencimiento del compromiso.")
            
            if amount is None:
                if self.instance and self.instance.pk:
                    cleaned["amount"] = self.instance.amount
                else:
                    self.add_error("amount", "Debe indicar el monto del compromiso.")
            
            final_amount = cleaned.get("amount")
            if final_amount is not None and final_amount <= 0:
                self.add_error("amount", "El monto debe ser mayor a cero.")
                
            self._manual_installment_dates = []

        if mode not in {TaxCommitment.InstallmentMode.AUTO, TaxCommitment.InstallmentMode.MANUAL}:
            self.add_error("installment_mode", "Modo de cuotas inválido.")
        return cleaned

    @property
    def manual_installment_amounts(self):
        return list(self._manual_installment_amounts)

    @property
    def manual_installment_dates(self):
        return list(self._manual_installment_dates)

    def should_generate_installments(self) -> bool:
        return (self.cleaned_data.get("installments_count") or 1) > 1 and not (self.instance and self.instance.pk)

    def build_installment_data(self) -> list[dict]:
        installments_count = self.cleaned_data.get("installments_count") or 1
        due_date = self.cleaned_data.get("due_date")
        currency = self.cleaned_data.get("currency") or TaxCommitment.Currency.PYG
        mode = self.cleaned_data.get("installment_mode") or TaxCommitment.InstallmentMode.AUTO
        date_mode = (self.cleaned_data.get("installment_date_mode") or "AUTO").upper()
        customize_dates = date_mode == "MANUAL"
        if mode == TaxCommitment.InstallmentMode.MANUAL:
            installment_amounts = self.manual_installment_amounts
        else:
            total_amount = self.cleaned_data["amount"]
            installment_amounts = split_amount_into_installments(total_amount, installments_count, currency)
        if customize_dates and self.manual_installment_dates:
            installment_dates = self.manual_installment_dates
        else:
            installment_dates = build_installment_dates(due_date, installments_count)
        return [
            {
                "installment_number": index + 1,
                "installment_total": installments_count,
                "due_date": installment_dates[index],
                "amount": installment_amounts[index],
            }
            for index in range(installments_count)
        ]

    def get_manual_amounts_for_render(self):
        if self.is_bound:
            values = []
            for raw in self.data.getlist("manual_amounts"):
                token = str(raw or "").strip()
                if token:
                    values.append(token)
            return values
        return []

    def get_manual_due_dates_for_render(self):
        if self.is_bound:
            values = []
            for raw in self.data.getlist("manual_due_dates"):
                token = str(raw or "").strip()
                if token:
                    values.append(token)
            return values
        return []


class TaxCommitmentInstallmentForm(forms.ModelForm):
    """Formulario simplificado para editar una sola cuota de un compromiso tributario.
    Solo permite cambiar: fecha de vencimiento, monto, estado y observaciones.
    No toca la lógica de generación de cuotas múltiples.
    """

    class Meta:
        model = TaxCommitment
        fields = ["due_date", "amount", "status", "notes"]
        widgets = {
            "due_date": DateInput(),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }
        labels = {
            "due_date": "Fecha de vencimiento",
            "amount": "Monto",
            "status": "Estado",
            "notes": "Observación",
        }

    def clean_amount(self):
        amount = self.cleaned_data.get("amount")
        if amount is not None and amount <= 0:
            raise forms.ValidationError("El monto debe ser mayor a cero.")
        return amount
