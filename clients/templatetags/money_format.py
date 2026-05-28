from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django import template

register = template.Library()


def _to_decimal(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _format_with_thousands(value_as_str: str) -> str:
    return value_as_str.replace(",", ".")


@register.filter(name="money")
def money(value, currency="PYG"):
    amount = _to_decimal(value)
    if amount is None:
        return "-"
    currency_code = (currency or "PYG").upper()

    if currency_code == "USD":
        rounded = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        base = f"{rounded:,.2f}"
        # 1,234,567.89 -> 1.234.567,89
        return base.replace(",", "_").replace(".", ",").replace("_", ".")

    rounded = amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    base = f"{int(rounded):,}"
    return _format_with_thousands(base)


@register.filter(name="format_number")
def format_number(value):
    amount = _to_decimal(value)
    if amount is None:
        return "-"
    rounded = amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    base = f"{int(rounded):,}"
    return _format_with_thousands(base)
