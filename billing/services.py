from decimal import Decimal

from billing.models import Charge


def sync_client_billing_snapshot(client):
    latest = client.charges.order_by("-period_month", "-created_at").first()
    if not latest:
        client.paid = False
        client.debt_amount = Decimal("0")
        client.save(update_fields=["paid", "debt_amount", "updated_at"])
        return

    client.paid = latest.status == Charge.Status.PAID
    if latest.status == Charge.Status.PAID:
        client.debt_amount = Decimal("0")
    else:
        client.debt_amount = latest.debt_amount or latest.amount

    client.save(update_fields=["paid", "debt_amount", "updated_at"])
