from calendar import monthrange
from datetime import date
from decimal import Decimal, ROUND_HALF_UP


def installment_unit_for_currency(currency: str) -> Decimal:
    return Decimal("1") if currency == "PYG" else Decimal("0.01")


def split_amount_into_installments(total_amount: Decimal, installments_count: int, currency: str) -> list[Decimal]:
    if installments_count <= 0:
        raise ValueError("La cantidad de cuotas debe ser mayor a cero.")

    unit = installment_unit_for_currency(currency)
    if currency == "PYG" and total_amount != total_amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP):
        raise ValueError("Para PYG, el monto total debe ser entero.")

    normalized_total = total_amount.quantize(unit, rounding=ROUND_HALF_UP)
    units_total = int((normalized_total / unit).to_integral_value(rounding=ROUND_HALF_UP))
    base_units = units_total // installments_count
    remainder_units = units_total - (base_units * installments_count)

    amounts = [Decimal(base_units) * unit for _ in range(installments_count)]
    amounts[-1] = amounts[-1] + (Decimal(remainder_units) * unit)
    return amounts


def add_months_to_date(value: date, months: int) -> date:
    month_index = (value.month - 1) + months
    year = value.year + (month_index // 12)
    month = (month_index % 12) + 1
    day = min(value.day, monthrange(year, month)[1])
    return date(year, month, day)


def build_installment_dates(first_due_date: date, installments_count: int) -> list[date]:
    return [add_months_to_date(first_due_date, offset) for offset in range(installments_count)]
