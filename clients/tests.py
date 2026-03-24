from datetime import date, datetime
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.urls import reverse
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import User
from banks.models import BankRequest
from billing.models import Charge, Contract
from clients.forms import ClientForm
from clients.models import (
    Client,
    ClientInvoicePeriodStatus,
    ClientNote,
    ClientObligation,
    ClientResponsibilityHistory,
    Obligation,
)
from clients.utils import calculate_ruc_dv_from_base
from operations.models import Deadline, PendingItem, Submission
from operations.services import dnit_due_date_for_month


class ClientFilterTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="admin2", password="secret123", role=User.Role.ADMIN)
        self.client.force_authenticate(self.admin)

        Client.objects.create(
            name="Empresa Uno",
            ruc="80146792",
            zone=Client.Zone.SANTA_RITA,
            status=Client.Status.ACTIVE,
            debt_amount=Decimal("1000"),
            due_date=date(2026, 3, 15),
        )
        Client.objects.create(
            name="Empresa Dos",
            ruc="5555555",
            zone=Client.Zone.KM_32,
            status=Client.Status.SUSPENDED,
            debt_amount=Decimal("0"),
            due_date=date(2026, 4, 10),
        )

    def test_filters_by_zone_status_and_debt(self):
        res = self.client.get("/clients?zone=SANTA_RITA&status=ACTIVE&con_deuda=true")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data), 1)
        self.assertEqual(res.data[0]["name"], "Empresa Uno")

    def test_filters_by_due_range(self):
        res = self.client.get("/clients?vence_desde=2026-03-01&vence_hasta=2026-03-31")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data), 1)
        self.assertEqual(res.data[0]["name"], "Empresa Uno")


class ClientObligationFormTests(TestCase):
    def setUp(self):
        self.iva, _ = Obligation.objects.get_or_create(code="IVA_GENERAL", defaults={"name": "IVA General"})
        self.ire, _ = Obligation.objects.get_or_create(code="IRE_GENERAL", defaults={"name": "IRE General"})

    def test_client_form_creates_active_client_obligations(self):
        form = ClientForm(
            data={
                "name": "Cliente Obligaciones",
                "ruc": "8001002-3",
                "phone": "",
                "address": "",
                "zone": Client.Zone.SANTA_RITA,
                "presentation_type": "",
                "due_date": "",
                "submission_date": "",
                "pending_notes": "",
                "observations": "",
                "monthly_amount_pyg": "0",
                "monthly_amount_usd": "0",
                "paid": False,
                "debt_amount": "0",
                "contract_until": "",
                "status": Client.Status.ACTIVE,
                "obligations": [str(self.iva.id), str(self.ire.id)],
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        client = form.save()

        links = ClientObligation.objects.filter(client=client, status=ClientObligation.Status.ACTIVE)
        self.assertEqual(links.count(), 2)

    def test_client_form_marks_removed_obligation_as_inactive(self):
        client = Client.objects.create(
            name="Cliente Editar Obligaciones",
            ruc="8002003-4",
            zone=Client.Zone.SANTA_RITA,
            status=Client.Status.ACTIVE,
        )
        ClientObligation.objects.create(client=client, obligation=self.iva, status=ClientObligation.Status.ACTIVE)
        ClientObligation.objects.create(client=client, obligation=self.ire, status=ClientObligation.Status.ACTIVE)

        form = ClientForm(
            data={
                "name": client.name,
                "ruc": client.ruc,
                "phone": client.phone,
                "address": client.address,
                "zone": client.zone,
                "presentation_type": client.presentation_type,
                "due_date": "",
                "submission_date": "",
                "pending_notes": client.pending_notes,
                "observations": client.observations,
                "monthly_amount_pyg": client.monthly_amount_pyg,
                "monthly_amount_usd": client.monthly_amount_usd,
                "paid": client.paid,
                "debt_amount": client.debt_amount,
                "contract_until": "",
                "status": client.status,
                "obligations": [str(self.iva.id)],
            },
            instance=client,
        )

        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        iva_link = ClientObligation.objects.get(client=client, obligation=self.iva)
        ire_link = ClientObligation.objects.get(client=client, obligation=self.ire)
        self.assertEqual(iva_link.status, ClientObligation.Status.ACTIVE)
        self.assertEqual(ire_link.status, ClientObligation.Status.INACTIVE)


class RucDvUtilityTests(TestCase):
    def test_calculate_ruc_dv_from_base_returns_digit_or_empty(self):
        self.assertTrue(calculate_ruc_dv_from_base("80044916").isdigit())
        self.assertEqual(calculate_ruc_dv_from_base(""), "")
        self.assertEqual(calculate_ruc_dv_from_base("ABC"), "")


class ClientFinancialVisibilityByRoleTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="admin_client_api", password="secret123", role=User.Role.ADMIN)
        self.funcionario = User.objects.create_user(
            username="func_client_api",
            password="secret123",
            role=User.Role.FUNCIONARIO,
        )
        Client.objects.create(
            name="Cliente Sensible",
            ruc="80146792-6",
            zone=Client.Zone.SANTA_RITA,
            status=Client.Status.ACTIVE,
            responsible=self.funcionario,
            monthly_amount_pyg=Decimal("750000"),
            monthly_amount_usd=Decimal("120"),
            debt_amount=Decimal("500000"),
            paid=False,
        )

    def test_funcionario_clients_api_hides_financial_fields(self):
        self.client.force_authenticate(self.funcionario)
        res = self.client.get("/clients")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data), 1)
        item = res.data[0]
        self.assertNotIn("monthly_amount_pyg", item)
        self.assertNotIn("monthly_amount_usd", item)
        self.assertNotIn("debt_amount", item)
        self.assertNotIn("paid", item)
        self.assertNotIn("contract_until", item)

    def test_admin_clients_api_includes_financial_fields(self):
        self.client.force_authenticate(self.admin)
        res = self.client.get("/clients")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data), 1)
        item = res.data[0]
        self.assertIn("monthly_amount_pyg", item)
        self.assertIn("monthly_amount_usd", item)
        self.assertIn("debt_amount", item)
        self.assertIn("paid", item)
        self.assertIn("contract_until", item)


class ClientResponsibleApiAccessTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="admin_resp", password="secret123", role=User.Role.ADMIN)
        self.func1 = User.objects.create_user(username="func_resp_1", password="secret123", role=User.Role.FUNCIONARIO)
        self.func2 = User.objects.create_user(username="func_resp_2", password="secret123", role=User.Role.FUNCIONARIO)
        self.client_a = Client.objects.create(
            name="Cliente A",
            ruc="80146792-6",
            zone=Client.Zone.SANTA_RITA,
            status=Client.Status.ACTIVE,
            responsible=self.func1,
        )
        self.client_b = Client.objects.create(
            name="Cliente B",
            ruc="80146791-5",
            zone=Client.Zone.KM_32,
            status=Client.Status.ACTIVE,
            responsible=self.func2,
        )

    def test_funcionario_only_lists_assigned_clients(self):
        self.client.force_authenticate(self.func1)
        res = self.client.get("/clients")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data), 1)
        self.assertEqual(res.data[0]["id"], self.client_a.id)

    def test_funcionario_cannot_access_unassigned_client_detail(self):
        self.client.force_authenticate(self.func1)
        res = self.client.get(f"/clients/{self.client_b.id}")
        self.assertEqual(res.status_code, 404)

    def test_funcionario_can_edit_assigned_client(self):
        self.client.force_authenticate(self.func1)
        res = self.client.patch(f"/clients/{self.client_a.id}", {"phone": "0991-555000"}, format="json")
        self.assertEqual(res.status_code, 200)
        self.client_a.refresh_from_db()
        self.assertEqual(self.client_a.phone, "0991-555000")

    def test_funcionario_can_update_invoice_period_status_on_assigned_client(self):
        self.client.force_authenticate(self.func1)
        res = self.client.patch(
            f"/clients/{self.client_a.id}",
            {"invoice_period_status": Client.InvoicePeriodStatus.RECEIVED},
            format="json",
        )
        self.assertEqual(res.status_code, 200)
        self.client_a.refresh_from_db()
        self.assertEqual(self.client_a.invoice_period_status, Client.InvoicePeriodStatus.RECEIVED)
        self.assertEqual(self.client_a.invoice_period_status_updated_by_id, self.func1.id)
        self.assertIsNotNone(self.client_a.invoice_period_status_updated_at)

    def test_funcionario_cannot_edit_unassigned_client(self):
        self.client.force_authenticate(self.func1)
        res = self.client.patch(f"/clients/{self.client_b.id}", {"phone": "0991-777000"}, format="json")
        self.assertEqual(res.status_code, 404)

    def test_funcionario_cannot_update_invoice_period_status_on_unassigned_client(self):
        self.client.force_authenticate(self.func1)
        res = self.client.patch(
            f"/clients/{self.client_b.id}",
            {"invoice_period_status": Client.InvoicePeriodStatus.RECEIVED},
            format="json",
        )
        self.assertEqual(res.status_code, 404)

    def test_admin_can_reassign_responsible_and_history_is_saved(self):
        self.client.force_authenticate(self.admin)
        res = self.client.patch(
            f"/clients/{self.client_a.id}",
            {"responsible": self.func2.id},
            format="json",
        )
        self.assertEqual(res.status_code, 200)
        self.client_a.refresh_from_db()
        self.assertEqual(self.client_a.responsible_id, self.func2.id)
        history = ClientResponsibilityHistory.objects.filter(client=self.client_a).first()
        self.assertIsNotNone(history)
        self.assertEqual(history.old_responsible_id, self.func1.id)
        self.assertEqual(history.new_responsible_id, self.func2.id)
        self.assertEqual(history.changed_by_id, self.admin.id)

    def test_admin_cannot_assign_admin_as_responsible(self):
        self.client.force_authenticate(self.admin)
        res = self.client.patch(
            f"/clients/{self.client_a.id}",
            {"responsible": self.admin.id},
            format="json",
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("responsible", res.data)


class ClientResponsibleWebAccessTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="admin_web_resp", password="secret123", role=User.Role.ADMIN)
        self.func1 = User.objects.create_user(username="func_web_resp_1", password="secret123", role=User.Role.FUNCIONARIO)
        self.func2 = User.objects.create_user(username="func_web_resp_2", password="secret123", role=User.Role.FUNCIONARIO)
        self.client_a = Client.objects.create(
            name="Cliente Web A",
            ruc="7001001-1",
            zone=Client.Zone.SANTA_RITA,
            status=Client.Status.ACTIVE,
            responsible=self.func1,
        )
        self.client_b = Client.objects.create(
            name="Cliente Web B",
            ruc="7001002-2",
            zone=Client.Zone.KM_32,
            status=Client.Status.ACTIVE,
            responsible=self.func2,
        )

    def test_funcionario_list_only_shows_assigned_clients(self):
        self.client.force_login(self.func1)
        res = self.client.get("/app/clients/")
        html = res.content.decode("utf-8")
        self.assertEqual(res.status_code, 200)
        self.assertIn("Cliente Web A", html)
        self.assertNotIn("Cliente Web B", html)

    def test_funcionario_cannot_open_unassigned_client_detail(self):
        self.client.force_login(self.func1)
        res = self.client.get(f"/app/clients/{self.client_b.id}/")
        self.assertEqual(res.status_code, 403)

    def test_funcionario_can_update_invoice_period_status_from_web_for_assigned_client(self):
        self.client.force_login(self.func1)
        res = self.client.post(
            reverse("app-client-invoice-period-status", kwargs={"client_id": self.client_a.id}),
            {"invoice_period_status": Client.InvoicePeriodStatus.RECEIVED, "next": f"/app/clients/{self.client_a.id}/"},
        )
        self.assertEqual(res.status_code, 302)
        self.client_a.refresh_from_db()
        self.assertEqual(self.client_a.invoice_period_status, Client.InvoicePeriodStatus.RECEIVED)
        self.assertEqual(self.client_a.invoice_period_status_updated_by_id, self.func1.id)

    def test_funcionario_cannot_update_invoice_period_status_for_unassigned_client_on_web(self):
        self.client.force_login(self.func1)
        res = self.client.post(
            reverse("app-client-invoice-period-status", kwargs={"client_id": self.client_b.id}),
            {"invoice_period_status": Client.InvoicePeriodStatus.RECEIVED},
        )
        self.assertEqual(res.status_code, 403)

    def test_funcionario_cannot_create_note_for_unassigned_client(self):
        self.client.force_login(self.func1)
        res = self.client.post(
            reverse("app-note-create", kwargs={"client_id": self.client_b.id}),
            {"note": "Intento sin permiso"},
        )
        self.assertEqual(res.status_code, 403)

    def test_funcionario_cannot_create_pending_for_unassigned_client(self):
        self.client.force_login(self.func1)
        before_count = self.client_b.pending_items.count()
        res = self.client.post(
            reverse("app-pending-create"),
            {
                "client": self.client_b.id,
                "description": "Pendencia sin permiso",
                "priority": "OK",
                "status": "OPEN",
            },
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.client_b.pending_items.count(), before_count)

    def test_funcionario_cannot_create_submission_for_unassigned_client(self):
        self.client.force_login(self.func1)
        before_count = self.client_b.submissions.count()
        res = self.client.post(
            reverse("app-submission-create"),
            {
                "client": self.client_b.id,
                "submission_type": "IVA General",
                "status": "PENDING",
            },
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.client_b.submissions.count(), before_count)

    def test_client_form_rejects_non_funcionario_as_responsible(self):
        form = ClientForm(
            data={
                "name": "Cliente Form",
                "ruc": "8001002-3",
                "ruc_dv": "",
                "responsible": str(self.admin.id),
                "phone": "",
                "address": "",
                "zone": Client.Zone.SANTA_RITA,
                "presentation_type": "",
                "due_date": "",
                "submission_date": "",
                "pending_notes": "",
                "observations": "",
                "monthly_amount_pyg": "0",
                "monthly_amount_usd": "0",
                "paid": False,
                "debt_amount": "0",
                "contract_until": "",
                "status": Client.Status.ACTIVE,
                "obligations": [],
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("responsible", form.errors)


class ClientInvoicePeriodMonthlyWebTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="admin_invoice_period", password="secret123", role=User.Role.ADMIN)
        self.func1 = User.objects.create_user(username="Dyan", password="secret123", role=User.Role.FUNCIONARIO)
        self.func2 = User.objects.create_user(username="otro_func", password="secret123", role=User.Role.FUNCIONARIO)

        self.client_pending = Client.objects.create(
            name="Cliente Pendiente Marzo",
            ruc="8003000",
            zone=Client.Zone.SANTA_RITA,
            status=Client.Status.ACTIVE,
            responsible=self.func1,
        )
        self.client_received = Client.objects.create(
            name="Cliente Recibido Marzo",
            ruc="8003001",
            zone=Client.Zone.SANTA_RITA,
            status=Client.Status.ACTIVE,
            responsible=self.func1,
        )
        self.client_other_responsible = Client.objects.create(
            name="Cliente Otro Responsable",
            ruc="8003002",
            zone=Client.Zone.SANTA_RITA,
            status=Client.Status.ACTIVE,
            responsible=self.func2,
        )

        ClientInvoicePeriodStatus.objects.create(
            client=self.client_received,
            year=2026,
            month=3,
            status=ClientInvoicePeriodStatus.Status.RECEIVED,
            updated_by=self.admin,
        )
        ClientInvoicePeriodStatus.objects.create(
            client=self.client_pending,
            year=2026,
            month=4,
            status=ClientInvoicePeriodStatus.Status.RECEIVED,
            updated_by=self.admin,
        )

    def test_clients_list_filters_month_year_and_invoice_status_pending(self):
        self.client.force_login(self.admin)
        res = self.client.get(
            "/app/clients/",
            {
                "responsible": str(self.func1.id),
                "month": "3",
                "year": "2026",
                "invoice_status": ClientInvoicePeriodStatus.Status.PENDING,
            },
        )
        html = res.content.decode("utf-8")
        self.assertEqual(res.status_code, 200)
        self.assertIn("Cliente Pendiente Marzo", html)
        self.assertNotIn("Cliente Recibido Marzo", html)
        self.assertNotIn("Cliente Otro Responsable", html)
        self.assertIn("Facturas del período activo:", html)
        self.assertIn("Marzo 2026", html)

    def test_clients_list_filters_month_year_and_invoice_status_received(self):
        self.client.force_login(self.admin)
        res = self.client.get(
            "/app/clients/",
            {
                "responsible": str(self.func1.id),
                "month": "3",
                "year": "2026",
                "invoice_status": ClientInvoicePeriodStatus.Status.RECEIVED,
            },
        )
        html = res.content.decode("utf-8")
        self.assertEqual(res.status_code, 200)
        self.assertNotIn("Cliente Pendiente Marzo", html)
        self.assertIn("Cliente Recibido Marzo", html)
        self.assertNotIn("Cliente Otro Responsable", html)

    def test_clients_list_filters_month_year_and_invoice_status_partial(self):
        ClientInvoicePeriodStatus.objects.create(
            client=self.client_pending,
            year=2026,
            month=3,
            status=ClientInvoicePeriodStatus.Status.PARTIAL,
            updated_by=self.admin,
        )
        self.client.force_login(self.admin)
        res = self.client.get(
            "/app/clients/",
            {
                "responsible": str(self.func1.id),
                "month": "3",
                "year": "2026",
                "invoice_status": ClientInvoicePeriodStatus.Status.PARTIAL,
            },
        )
        html = res.content.decode("utf-8")
        self.assertEqual(res.status_code, 200)
        self.assertIn("Cliente Pendiente Marzo", html)
        self.assertNotIn("Cliente Recibido Marzo", html)
        self.assertIn("Parc", html)

    def test_mark_invoice_status_updates_selected_period_without_breaking_other_month(self):
        self.client.force_login(self.func1)
        res = self.client.post(
            reverse("app-client-invoice-period-status", kwargs={"client_id": self.client_pending.id}),
            {
                "invoice_period_status": ClientInvoicePeriodStatus.Status.RECEIVED,
                "month": "3",
                "year": "2026",
                "next": "/app/clients/?month=3&year=2026",
            },
        )
        self.assertEqual(res.status_code, 302)

        march_record = ClientInvoicePeriodStatus.objects.get(
            client=self.client_pending,
            year=2026,
            month=3,
        )
        self.assertEqual(march_record.status, ClientInvoicePeriodStatus.Status.RECEIVED)
        self.assertEqual(march_record.updated_by_id, self.func1.id)

        april_record = ClientInvoicePeriodStatus.objects.get(
            client=self.client_pending,
            year=2026,
            month=4,
        )
        self.assertEqual(april_record.status, ClientInvoicePeriodStatus.Status.RECEIVED)

    def test_mark_non_current_period_does_not_overwrite_legacy_current_month_field(self):
        self.client.force_login(self.func1)
        self.client_pending.invoice_period_status = Client.InvoicePeriodStatus.PENDING
        self.client_pending.save(update_fields=["invoice_period_status", "updated_at"])

        res = self.client.post(
            reverse("app-client-invoice-period-status", kwargs={"client_id": self.client_pending.id}),
            {
                "invoice_period_status": ClientInvoicePeriodStatus.Status.RECEIVED,
                "month": "4",
                "year": "2026",
                "next": "/app/clients/?month=4&year=2026",
            },
        )
        self.assertEqual(res.status_code, 302)
        self.client_pending.refresh_from_db()
        self.assertEqual(self.client_pending.invoice_period_status, Client.InvoicePeriodStatus.PENDING)

    def test_reset_invoice_period_status_removes_selected_period_record(self):
        self.client.force_login(self.func1)
        record = ClientInvoicePeriodStatus.objects.create(
            client=self.client_pending,
            year=2026,
            month=3,
            status=ClientInvoicePeriodStatus.Status.PARTIAL,
            updated_by=self.func1,
        )
        res = self.client.post(
            reverse("app-client-invoice-period-status", kwargs={"client_id": self.client_pending.id}),
            {
                "invoice_period_status": "RESET",
                "month": "3",
                "year": "2026",
                "next": "/app/clients/?month=3&year=2026",
            },
        )
        self.assertEqual(res.status_code, 302)
        self.assertFalse(ClientInvoicePeriodStatus.objects.filter(id=record.id).exists())


class ResetClientsDataCommandTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="admin_reset_cmd", password="secret123", role=User.Role.ADMIN)
        self.func = User.objects.create_user(username="func_reset_cmd", password="secret123", role=User.Role.FUNCIONARIO)
        self.client_obj = Client.objects.create(
            name="Cliente Reset",
            ruc="8001234-5",
            zone=Client.Zone.SANTA_RITA,
            status=Client.Status.ACTIVE,
            responsible=self.func,
        )
        obligation = Obligation.objects.create(code="OBL_RESET_TEST", name="Obligacion Reset Test", is_active=True)
        ClientObligation.objects.create(client=self.client_obj, obligation=obligation)
        ClientResponsibilityHistory.objects.create(
            client=self.client_obj,
            old_responsible=None,
            new_responsible=self.func,
            changed_by=self.admin,
        )
        pending = PendingItem.objects.create(
            client=self.client_obj,
            description="Pendencia test reset",
            created_by=self.admin,
        )
        Deadline.objects.create(
            client=self.client_obj,
            description="Vencimiento test reset",
            due_date=date(2026, 3, 20),
            created_by=self.admin,
        )
        Submission.objects.create(
            client=self.client_obj,
            submission_type="IVA Marzo",
            due_date=date(2026, 3, 21),
            status=Submission.Status.PENDING,
            created_by=self.admin,
        )
        BankRequest.objects.create(
            client=self.client_obj,
            request_type=BankRequest.RequestType.PROVISORIO,
            status=BankRequest.Status.REQUESTED,
            responsible=self.func,
            requested_by=self.admin,
            receipts_pending_item=pending,
        )
        contract = Contract.objects.create(
            client=self.client_obj,
            monthly_amount=Decimal("500000"),
        )
        Charge.objects.create(
            client=self.client_obj,
            contract=contract,
            period_month=date(2026, 3, 1),
            amount=Decimal("500000"),
            debt_amount=Decimal("0"),
            payment_type=Charge.PaymentType.HONORARIOS,
        )
        self.initial_user_count = User.objects.count()

    def test_dry_run_reports_and_does_not_delete(self):
        output = StringIO()
        call_command("reset_clients_data", stdout=output)

        self.assertEqual(Client.objects.count(), 1)
        self.assertEqual(BankRequest.objects.count(), 1)
        self.assertEqual(Submission.objects.count(), 1)
        self.assertEqual(PendingItem.objects.count(), 1)
        self.assertEqual(Deadline.objects.count(), 1)
        self.assertEqual(Charge.objects.count(), 1)
        self.assertEqual(Contract.objects.count(), 1)
        self.assertEqual(ClientObligation.objects.count(), 1)
        self.assertEqual(ClientResponsibilityHistory.objects.count(), 1)
        self.assertEqual(User.objects.count(), self.initial_user_count)
        self.assertIn("DRY-RUN", output.getvalue())
        self.assertIn("clients.Client", output.getvalue())

    def test_apply_deletes_client_related_data_and_keeps_users(self):
        output = StringIO()
        call_command("reset_clients_data", "--apply", stdout=output)

        self.assertEqual(BankRequest.objects.count(), 0)
        self.assertEqual(Submission.objects.count(), 0)
        self.assertEqual(PendingItem.objects.count(), 0)
        self.assertEqual(Deadline.objects.count(), 0)
        self.assertEqual(Charge.objects.count(), 0)
        self.assertEqual(Contract.objects.count(), 0)
        self.assertEqual(ClientObligation.objects.count(), 0)
        self.assertEqual(ClientNote.objects.count(), 0)
        self.assertEqual(ClientResponsibilityHistory.objects.count(), 0)
        self.assertEqual(Client.objects.count(), 0)
        self.assertEqual(User.objects.count(), self.initial_user_count)
        self.assertIn("Limpieza completada", output.getvalue())


class ClientMaintenanceCommandsTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin_maintenance_cmd",
            password="secret123",
            role=User.Role.ADMIN,
        )

        self.client_hyphen = Client.objects.create(
            name="Cliente RUC Hyphen",
            ruc="1234567-8",
            ruc_dv="",
            zone=Client.Zone.SANTA_RITA,
            status=Client.Status.ACTIVE,
        )
        self.client_conflict = Client.objects.create(
            name="Cliente RUC Conflicto",
            ruc="7654321-3",
            ruc_dv="9",
            zone=Client.Zone.SANTA_RITA,
            status=Client.Status.ACTIVE,
        )
        self.client_due = Client.objects.create(
            name="Cliente Due Date",
            ruc="1111117",
            ruc_dv="",
            zone=Client.Zone.SANTA_RITA,
            status=Client.Status.ACTIVE,
            due_date=date(2026, 4, 16),
        )
        self.client_no_ruc = Client.objects.create(
            name="Cliente Sin RUC",
            ruc="",
            ruc_dv="",
            zone=Client.Zone.SANTA_RITA,
            status=Client.Status.ACTIVE,
        )

        old_dt = timezone.make_aware(datetime(2026, 2, 20, 10, 0, 0))
        current_dt = timezone.make_aware(datetime(2026, 3, 5, 10, 0, 0))
        self.client_invoice_old = Client.objects.create(
            name="Cliente Factura Vieja",
            ruc="8001000",
            zone=Client.Zone.SANTA_RITA,
            status=Client.Status.ACTIVE,
            invoice_period_status=Client.InvoicePeriodStatus.RECEIVED,
            invoice_period_status_updated_by=self.admin,
            invoice_period_status_updated_at=old_dt,
        )
        self.client_invoice_current = Client.objects.create(
            name="Cliente Factura Actual",
            ruc="8001001",
            zone=Client.Zone.SANTA_RITA,
            status=Client.Status.ACTIVE,
            invoice_period_status=Client.InvoicePeriodStatus.RECEIVED,
            invoice_period_status_updated_by=self.admin,
            invoice_period_status_updated_at=current_dt,
        )
        self.client_invoice_null = Client.objects.create(
            name="Cliente Factura Sin Fecha",
            ruc="8001002",
            zone=Client.Zone.SANTA_RITA,
            status=Client.Status.ACTIVE,
            invoice_period_status=Client.InvoicePeriodStatus.RECEIVED,
            invoice_period_status_updated_by=self.admin,
            invoice_period_status_updated_at=None,
        )

    def test_normalize_client_ruc_fields_dry_run_and_apply(self):
        output = StringIO()
        call_command("normalize_client_ruc_fields", stdout=output)

        self.client_hyphen.refresh_from_db()
        self.client_conflict.refresh_from_db()
        self.assertEqual(self.client_hyphen.ruc, "1234567-8")
        self.assertEqual(self.client_hyphen.ruc_dv, "")
        self.assertEqual(self.client_conflict.ruc_dv, "9")
        self.assertIn("Dry-run", output.getvalue())
        self.assertIn("Conflictos detectados: 1", output.getvalue())

        call_command("normalize_client_ruc_fields", "--apply", "--actor", self.admin.username)

        self.client_hyphen.refresh_from_db()
        self.client_conflict.refresh_from_db()
        self.assertEqual(self.client_hyphen.ruc, "1234567")
        self.assertEqual(self.client_hyphen.ruc_dv, "8")
        self.assertEqual(self.client_hyphen.ruc_base, "1234567")
        self.assertEqual(self.client_conflict.ruc, "7654321-3")
        self.assertEqual(self.client_conflict.ruc_dv, "9")

    def test_recalculate_client_due_dates_dry_run_and_apply(self):
        output = StringIO()
        call_command(
            "recalculate_client_due_dates",
            "--year",
            "2026",
            "--month",
            "4",
            stdout=output,
        )

        self.client_due.refresh_from_db()
        self.assertEqual(self.client_due.due_date, date(2026, 4, 16))
        self.assertIn("Dry-run", output.getvalue())

        call_command(
            "recalculate_client_due_dates",
            "--year",
            "2026",
            "--month",
            "4",
            "--apply",
            "--actor",
            self.admin.username,
        )

        self.client_due.refresh_from_db()
        self.client_no_ruc.refresh_from_db()
        self.assertEqual(self.client_due.due_date, dnit_due_date_for_month(self.client_due.ruc, 2026, 4))
        self.assertIsNone(self.client_no_ruc.due_date)

    def test_reset_invoice_period_status_dry_run_and_apply(self):
        output = StringIO()
        call_command(
            "reset_invoice_period_status",
            "--year",
            "2026",
            "--month",
            "3",
            stdout=output,
        )

        self.client_invoice_old.refresh_from_db()
        self.client_invoice_current.refresh_from_db()
        self.client_invoice_null.refresh_from_db()
        self.assertEqual(self.client_invoice_old.invoice_period_status, Client.InvoicePeriodStatus.RECEIVED)
        self.assertEqual(self.client_invoice_current.invoice_period_status, Client.InvoicePeriodStatus.RECEIVED)
        self.assertEqual(self.client_invoice_null.invoice_period_status, Client.InvoicePeriodStatus.RECEIVED)
        self.assertIn("Dry-run", output.getvalue())

        call_command(
            "reset_invoice_period_status",
            "--year",
            "2026",
            "--month",
            "3",
            "--apply",
            "--actor",
            self.admin.username,
        )

        self.client_invoice_old.refresh_from_db()
        self.client_invoice_current.refresh_from_db()
        self.client_invoice_null.refresh_from_db()

        self.assertEqual(self.client_invoice_old.invoice_period_status, Client.InvoicePeriodStatus.PENDING)
        self.assertIsNone(self.client_invoice_old.invoice_period_status_updated_by_id)
        self.assertIsNotNone(self.client_invoice_old.invoice_period_status_updated_at)

        self.assertEqual(self.client_invoice_null.invoice_period_status, Client.InvoicePeriodStatus.PENDING)
        self.assertIsNone(self.client_invoice_null.invoice_period_status_updated_by_id)
        self.assertIsNotNone(self.client_invoice_null.invoice_period_status_updated_at)

        self.assertEqual(self.client_invoice_current.invoice_period_status, Client.InvoicePeriodStatus.RECEIVED)
        self.assertEqual(self.client_invoice_current.invoice_period_status_updated_by_id, self.admin.id)
