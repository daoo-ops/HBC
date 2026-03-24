from datetime import date
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APITestCase

from accounts.models import User
from billing.models import Charge, Contract
from clients.models import Client


class BillingApiPermissionTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="admin_billing", password="secret123", role=User.Role.ADMIN)
        self.funcionario = User.objects.create_user(
            username="func_billing",
            password="secret123",
            role=User.Role.FUNCIONARIO,
        )
        self.client_obj = Client.objects.create(
            name="Cliente Billing",
            ruc="8001002-3",
            zone=Client.Zone.SANTA_RITA,
            status=Client.Status.ACTIVE,
        )
        self.contract = Contract.objects.create(
            client=self.client_obj,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            monthly_amount=Decimal("500000"),
            currency=Contract.Currency.PYG,
            active=True,
        )
        self.charge = Charge.objects.create(
            client=self.client_obj,
            contract=self.contract,
            period_month=date(2026, 3, 1),
            amount=Decimal("500000"),
            debt_amount=Decimal("500000"),
            currency=Charge.Currency.PYG,
            status=Charge.Status.PENDING,
        )

    def test_funcionario_cannot_access_billing_api(self):
        self.client.force_authenticate(self.funcionario)

        res_charges = self.client.get("/charges")
        res_contracts = self.client.get("/contracts")
        res_mark_paid = self.client.post(f"/charges/{self.charge.id}/mark-paid")

        self.assertEqual(res_charges.status_code, 403)
        self.assertEqual(res_contracts.status_code, 403)
        self.assertEqual(res_mark_paid.status_code, 403)

    def test_admin_can_access_billing_api(self):
        self.client.force_authenticate(self.admin)

        res_charges = self.client.get("/charges")
        res_contracts = self.client.get("/contracts")

        self.assertEqual(res_charges.status_code, 200)
        self.assertEqual(res_contracts.status_code, 200)

    def test_admin_can_filter_charges_by_payment_type(self):
        self.charge.payment_type = Charge.PaymentType.HONORARIOS
        self.charge.save(update_fields=["payment_type", "updated_at"])
        Charge.objects.create(
            client=self.client_obj,
            contract=self.contract,
            period_month=date(2026, 4, 1),
            amount=Decimal("300000"),
            debt_amount=Decimal("300000"),
            currency=Charge.Currency.PYG,
            status=Charge.Status.PENDING,
            payment_type=Charge.PaymentType.IMPUESTOS,
        )

        self.client.force_authenticate(self.admin)
        res = self.client.get("/charges?payment_type=IMPUESTOS")

        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data), 1)
        self.assertEqual(res.data[0]["payment_type"], Charge.PaymentType.IMPUESTOS)

    def test_existing_like_charge_defaults_to_honorarios(self):
        self.assertEqual(self.charge.payment_type, Charge.PaymentType.HONORARIOS)


class BillingWebPermissionTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="admin_web", password="secret123", role=User.Role.ADMIN)
        self.funcionario = User.objects.create_user(
            username="func_web",
            password="secret123",
            role=User.Role.FUNCIONARIO,
        )
        self.client_obj = Client.objects.create(
            name="Cliente Web Billing",
            ruc="80146792-6",
            zone=Client.Zone.SANTA_RITA,
            status=Client.Status.ACTIVE,
        )
        self.contract = Contract.objects.create(
            client=self.client_obj,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            monthly_amount=Decimal("700000"),
            currency=Contract.Currency.PYG,
            active=True,
        )
        self.charge = Charge.objects.create(
            client=self.client_obj,
            contract=self.contract,
            period_month=date(2026, 2, 1),
            amount=Decimal("700000"),
            debt_amount=Decimal("700000"),
            currency=Charge.Currency.PYG,
            status=Charge.Status.PENDING,
        )

    def test_funcionario_cannot_access_billing_web_urls(self):
        self.client.force_login(self.funcionario)

        responses = [
            self.client.get("/app/charges/"),
            self.client.get("/app/contracts/"),
            self.client.get(f"/app/charges/{self.charge.id}/edit/"),
            self.client.get(f"/app/contracts/{self.contract.id}/edit/"),
            self.client.post(f"/app/charges/{self.charge.id}/mark-paid/"),
        ]

        self.assertTrue(all(resp.status_code == 403 for resp in responses))

    def test_admin_can_access_billing_web_urls(self):
        self.client.force_login(self.admin)

        res_charges = self.client.get("/app/charges/")
        res_contracts = self.client.get("/app/contracts/")

        self.assertEqual(res_charges.status_code, 200)
        self.assertEqual(res_contracts.status_code, 200)

    def test_admin_can_filter_charge_list_by_payment_type(self):
        self.charge.payment_type = Charge.PaymentType.OTROS
        self.charge.save(update_fields=["payment_type", "updated_at"])

        self.client.force_login(self.admin)
        response = self.client.get("/app/charges/?payment_type=OTROS")
        html = response.content.decode("utf-8")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Otros", html)

    def test_charge_list_shows_payment_type_and_status_summary_cards(self):
        self.charge.payment_type = Charge.PaymentType.IMPUESTOS
        self.charge.status = Charge.Status.PAID
        self.charge.save(update_fields=["payment_type", "status", "updated_at"])

        self.client.force_login(self.admin)
        response = self.client.get("/app/charges/")
        html = response.content.decode("utf-8")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Honorarios", html)
        self.assertIn("Impuestos", html)
        self.assertIn("Otros", html)
        self.assertIn("Pendientes", html)
        self.assertIn("Pagados", html)
