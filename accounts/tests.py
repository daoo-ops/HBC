from django.urls import reverse
from rest_framework.test import APITestCase

from accounts.models import User
from clients.models import Client, ClientObligation, Obligation
from operations.models import PendingItem, Submission


class AuthAndRoleTests(APITestCase):
    def setUp(self):
        self.master = User.objects.create_user(username="master", password="secret123", role=User.Role.MASTER)
        self.admin = User.objects.create_user(username="admin", password="secret123", role=User.Role.ADMIN)
        self.funcionario = User.objects.create_user(
            username="funcionario", password="secret123", role=User.Role.FUNCIONARIO
        )

    def test_login_and_me(self):
        res = self.client.post("/auth/login", {"username": "master", "password": "secret123"}, format="json")
        self.assertEqual(res.status_code, 200)
        me = self.client.get("/auth/me")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.data["username"], "master")

    def test_funcionario_cannot_create_client(self):
        self.client.force_authenticate(self.funcionario)
        payload = {
            "name": "Cliente Func",
            "ruc": "1234567",
            "zone": Client.Zone.SANTA_RITA,
            "status": Client.Status.ACTIVE,
        }
        res = self.client.post("/clients", payload, format="json")
        self.assertEqual(res.status_code, 403)

    def test_admin_can_create_client(self):
        self.client.force_authenticate(self.admin)
        payload = {
            "name": "Cliente Admin",
            "ruc": "9876543",
            "zone": Client.Zone.KM_32,
            "status": Client.Status.ACTIVE,
        }
        res = self.client.post("/clients", payload, format="json")
        self.assertEqual(res.status_code, 201)
        self.assertEqual(Client.objects.filter(name="Cliente Admin").count(), 1)

    def test_admin_cannot_create_admin_user(self):
        self.client.force_authenticate(self.admin)
        payload = {
            "username": "nuevo_admin",
            "password": "secret1234",
            "role": User.Role.ADMIN,
        }
        res = self.client.post("/users", payload, format="json")
        self.assertEqual(res.status_code, 403)


class RoleBasedUiVisibilityTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="admin_ui", password="secret123", role=User.Role.ADMIN)
        self.funcionario = User.objects.create_user(
            username="func_ui",
            password="secret123",
            role=User.Role.FUNCIONARIO,
        )
        self.client_obj = Client.objects.create(
            name="Cliente UI",
            ruc="80146792-6",
            zone=Client.Zone.SANTA_RITA,
            status=Client.Status.ACTIVE,
            responsible=self.funcionario,
        )

    def test_dashboard_hides_financial_blocks_for_funcionario(self):
        self.client.force_login(self.funcionario)
        res = self.client.get("/dashboard/")
        html = res.content.decode("utf-8")

        self.assertEqual(res.status_code, 200)
        self.assertNotIn("Cobros pendientes", html)
        self.assertNotIn("/app/charges/", html)
        self.assertNotIn("/app/contracts/", html)

    def test_dashboard_shows_financial_blocks_for_admin(self):
        self.client.force_login(self.admin)
        res = self.client.get("/dashboard/")
        html = res.content.decode("utf-8")

        self.assertEqual(res.status_code, 200)
        self.assertNotIn("Cobros pendientes", html)
        # El sidebar muestra el link de contratos para admins/master
        self.assertIn("/app/contracts/", html)

    def test_dashboard_shows_non_urgent_pending_for_assigned_funcionario(self):
        PendingItem.objects.create(
            client=self.client_obj,
            description="Seguimiento recibos normal",
            priority=PendingItem.Priority.OK,
            status=PendingItem.Status.OPEN,
        )

        self.client.force_login(self.funcionario)
        res = self.client.get("/dashboard/")
        html = res.content.decode("utf-8")

        self.assertEqual(res.status_code, 200)
        self.assertIn("Pendientes", html)
        self.assertIn("Seguimiento recibos normal", html)

    def test_client_detail_hides_financial_sections_for_funcionario(self):
        self.client.force_login(self.funcionario)
        res = self.client.get(f"/app/clients/{self.client_obj.id}/")
        html = res.content.decode("utf-8")

        self.assertEqual(res.status_code, 200)
        self.assertNotIn("Cobros y pagos", html)
        self.assertNotIn("Deuda actual", html)
        self.assertNotIn("Honorario PYG", html)
        self.assertNotIn("tab-charges", html)
        self.assertNotIn("tab-contracts", html)

    def test_client_detail_shows_financial_sections_for_admin(self):
        self.client.force_login(self.admin)
        res = self.client.get(f"/app/clients/{self.client_obj.id}/")
        html = res.content.decode("utf-8")

        self.assertEqual(res.status_code, 200)
        self.assertIn("Deuda actual", html)
        self.assertIn("Honorario PYG", html)
        self.assertIn("tab-contracts", html)


class DashboardAutoGenerationTests(APITestCase):
    def setUp(self):
        self.funcionario = User.objects.create_user(
            username="func_dash_auto",
            password="secret123",
            role=User.Role.FUNCIONARIO,
        )
        self.client_obj = Client.objects.create(
            name="Cliente Dash Auto",
            ruc="80146792-6",
            zone=Client.Zone.SANTA_RITA,
            status=Client.Status.ACTIVE,
            responsible=self.funcionario,
        )
        self.iva = Obligation.objects.create(
            code="IVA_DASH_AUTO_TEST",
            name="IVA Dash Auto Test",
            uses_ruc_calendar=True,
            is_active=True,
            default_periodicity="MONTHLY",
            default_due_mode="AUTO",
        )
        ClientObligation.objects.create(
            client=self.client_obj,
            obligation=self.iva,
            status=ClientObligation.Status.ACTIVE,
            due_mode=ClientObligation.DueMode.AUTO,
            needs_manual_review=False,
        )

    def test_dashboard_generates_current_period_submission_for_assigned_client(self):
        self.client.force_login(self.funcionario)
        self.assertEqual(Submission.objects.filter(client=self.client_obj, obligation=self.iva).count(), 0)

        response = self.client.get("/dashboard/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Submission.objects.filter(client=self.client_obj, obligation=self.iva).count(), 1)


class DashboardSubmissionActionsVisibilityTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="admin_dash_actions", password="secret123", role=User.Role.ADMIN)
        self.client_obj = Client.objects.create(
            name="Cliente Dash Actions",
            ruc="80146792-6",
            zone=Client.Zone.SANTA_RITA,
            status=Client.Status.ACTIVE,
        )
        self.iva = Obligation.objects.create(
            code="IVA_DASH_ACTIONS_TEST",
            name="IVA Dash Actions Test",
            uses_ruc_calendar=True,
            is_active=True,
            default_periodicity="MONTHLY",
            default_due_mode="AUTO",
        )
        self.pending_today = Submission.objects.create(
            client=self.client_obj,
            obligation=self.iva,
            submission_type="IVA Pendiente Hoy",
            period_kind=Submission.PeriodKind.MONTHLY,
            period_year=2026,
            period_month=3,
            due_date=None,
            status=Submission.Status.PENDING,
        )
        self.finalized_item = Submission.objects.create(
            client=self.client_obj,
            obligation=self.iva,
            submission_type="IVA Finalizado",
            period_kind=Submission.PeriodKind.MONTHLY,
            period_year=2026,
            period_month=2,
            status=Submission.Status.SUBMITTED,
        )

    def test_dashboard_shows_quick_actions_for_submissions(self):
        self.client.force_login(self.admin)
        response = self.client.get("/dashboard/")
        html = response.content.decode("utf-8")

        self.assertEqual(response.status_code, 200)
        self.assertIn(f"/app/submissions/{self.pending_today.id}/mark-submitted/", html)
        self.assertIn(f"/app/submissions/{self.pending_today.id}/edit/", html)
