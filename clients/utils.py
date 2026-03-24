import re
from typing import Optional


def normalize_ruc(ruc: str) -> str:
    if not ruc:
        return ""
    return re.sub(r"[^0-9-]", "", str(ruc).strip())


def normalize_ruc_base(ruc: str) -> str:
    value = normalize_ruc(ruc)
    if not value:
        return ""
    return value.split("-")[0]


def extract_ruc_digit(ruc: str) -> str:
    value = normalize_ruc(ruc)
    if not value:
        return ""
    if "-" in value:
        parts = value.split("-")
        return parts[-1] if parts[-1] else ""
    return ""


def calculate_ruc_dv_from_base(ruc_base: str) -> str:
    base = normalize_ruc_base(ruc_base)
    if not base or not base.isdigit():
        return ""

    total = 0
    weight = 2
    for digit in reversed(base):
        total += int(digit) * weight
        weight += 1
        if weight > 11:
            weight = 2

    remainder = total % 11
    verifier = 11 - remainder
    if verifier == 11:
        return "0"
    if verifier == 10:
        # Caso no estándar: evitar autocompletar con dato potencialmente incorrecto.
        return ""
    return str(verifier)


def parse_bool_param(value: Optional[str]) -> Optional[bool]:
    if value is None:
        return None
    clean = str(value).strip().lower()
    if clean in {"1", "true", "si", "sí", "yes"}:
        return True
    if clean in {"0", "false", "no"}:
        return False
    return None
