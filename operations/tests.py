from datetime import date
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.test.utils import override_settings
from rest_framework.test import APITestCase

from accounts.models import User
from auditing.models import AuditLog
from auditing.services import get_instance_snapshot
from clients.models import Client, ClientObligation, Obligation
from operations.models import PendingItem, Submission
from operations.services import (
    build_automatic_deadline_payload,
    dnit_due_date_for_month,
    dnit_due_day_for_ruc,
    ensure_period_submissions_for_clients,
)


class DNITServiceTests(TestCase):
    def test_due_day_from_ruc(self):
        self.assertEqual(dnit_due_day_for_ruc("80146792-6"), 11)
        self.assertEqual(dnit_due_day_for_ruc("80146790"), 7)

    def test_due_date_for_month(self):
        due = dnit_due_date_for_month("80146792-6", 2026, 3)
        self.assertEqual(due, date(2026, 3, 11))

    @override_settings(HBC_HOLIDAYS=["2026-03-11"])
    def test_due_date_moves_to_next_business_day_on_holiday(self):
        due = dnit_due_date_for_month("80146792-6", 2026, 3)
        self.assertEqual(due, date(2026, 3, 12))

    @override_settings(HBC_RUC_DUE_DAY_MAP={0: 20, 1: 21, 2: 22, 3: 23, 4: 24, 5: 25, 6: 26, 7: 27, 8: 28, 9: 29})
    def test_due_day_map_is_centralized_and_configurable(self):
        self.assertEqual(dnit_due_day_for_ruc("80146792-6"), 22)


class AuditSnapshotSerializationTests(TestCase):
    def test_pending_item_snapshot_serializes_client_as_id(self):
        user = User.objects.create_user(username="auditor", password="secret123", role=User.Role.ADMIN)
        client = Client.objects.create(
            name="Cliente Snapshot",
            ruc="1234567-8",
            zone=Client.Zone.SANTA_RITA,
            status=Client.Status.ACTIVE,
        )
        item = PendingItem.objects.create(
            client=client,
            description="Falta documento",
            status=PendingItem.Status.OPEN,
            created_by=user,
        )

        snap = get_instance_snapshot(item)

        self.assertEqual(snap["client"], client.id)
        self.assertEqual(snap["created_by"], user.id)

    def test_pending_create_ui_does_not_fail_json_serialization(self):
        user = User.objects.create_user(username="operador", password="secret123", role=User.Role.ADMIN)
        client = Client.objects.create(
            name="Cliente UI",
            ruc="7002001-9",
            zone=Client.Zone.SANTA_RITA,
            status=Client.Status.ACTIVE,
        )
        self.client.force_login(user)

        resp = self.client.post(
            f"/app/pending-items/new/?client={client.id}",
            {
                "client": client.id,
                "description": "Pendencia desde ficha",
                "missing_documents": "Factura pendiente",
                "expected_date": "2026-03-15",
                "priority": PendingItem.Priority.URGENT,
                "status": PendingItem.Status.OPEN,
            },
        )

        self.assertEqual(resp.status_code, 302)
        self.assertTrue(PendingItem.objects.filter(client=client, description="Pendencia desde ficha").exists())


class PendingItemSoftDeleteTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="admin_pending", password="secret123", role=User.Role.ADMIN)
        self.client.force_authenticate(self.admin)
        self.business = Client.objects.create(
            name="Empresa Pendencias",
            ruc="8901234-1",
            zone=Client.Zone.SANTA_RITA,
            status=Client.Status.ACTIVE,
        )
        self.item = PendingItem.objects.create(
            client=self.business,
            description="Pendencia para borrar",
            status=PendingItem.Status.OPEN,
            created_by=self.admin,
        )

    def test_destroy_pending_item_is_soft_delete(self):
        response = self.client.delete(f"/pending-items/{self.item.id}")

        self.assertEqual(response.status_code, 204)
        self.item.refresh_from_db()
        self.assertTrue(self.item.is_deleted)
        self.assertIsNotNone(self.item.deleted_at)
        self.assertEqual(self.item.deleted_by_id, self.admin.id)

    def test_deleted_pending_item_does_not_appear_in_api_list(self):
        self.item.is_deleted = True
        self.item.deleted_by = self.admin
        self.item.save(update_fields=["is_deleted", "deleted_by", "updated_at"])

        response = self.client.get("/pending-items")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 0)


class AutomaticDeadlineByObligationTests(TestCase):
    def test_build_payload_uses_only_auto_calendar_obligations(self):
        client = Client.objects.create(
            name="Cliente Calendario",
            ruc="80146792-6",
            zone=Client.Zone.SANTA_RITA,
            status=Client.Status.ACTIVE,
        )
        iva, _ = Obligation.objects.get_or_create(
            code="IVA_GENERAL",
            defaults={"name": "IVA General", "uses_ruc_calendar": True},
        )
        if not iva.uses_ruc_calendar:
            iva.uses_ruc_calendar = True
            iva.save(update_fields=["uses_ruc_calendar", "updated_at"])
        marangatu, _ = Obligation.objects.get_or_create(
            code="REGISTRO_COMPROBANTES_MARANGATU",
            defaults={
                "name": "Registro Anual de Comprobantes (IRP-RSP / obligación 715)",
                "uses_ruc_calendar": False,
            },
        )
        ClientObligation.objects.create(
            client=client,
            obligation=iva,
            status=ClientObligation.Status.ACTIVE,
            due_mode=ClientObligation.DueMode.AUTO,
            needs_manual_review=False,
        )
        ClientObligation.objects.create(
            client=client,
            obligation=marangatu,
            status=ClientObligation.Status.ACTIVE,
            due_mode=ClientObligation.DueMode.AUTO,
            needs_manual_review=False,
        )

        payload = build_automatic_deadline_payload([client], year=2026, month=3)

        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["obligation_type"], "IVA_GENERAL")
        self.assertIn("IVA General", payload[0]["description"])
        self.assertEqual(payload[0]["due_date"], "2026-03-11")


class AutomaticSubmissionGenerationTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="admin_auto_submission", password="secret123", role=User.Role.ADMIN)
        self.client_obj = Client.objects.create(
            name="Cliente Auto Sub",
            ruc="80146792-6",
            zone=Client.Zone.SANTA_RITA,
            status=Client.Status.ACTIVE,
        )
        self.iva = Obligation.objects.create(
            code="IVA_AUTO_SUBMISSION_TEST",
            name="IVA Auto Submission Test",
            uses_ruc_calendar=True,
            is_active=True,
            default_periodicity="MONTHLY",
            default_due_mode="AUTO",
        )
        self.ire_anual = Obligation.objects.create(
            code="IRE_ANUAL_AUTO_SUBMISSION_TEST",
            name="IRE Anual Auto Submission Test",
            uses_ruc_calendar=True,
            is_active=True,
            default_periodicity="ANNUAL",
            default_due_mode="AUTO",
        )
        ClientObligation.objects.create(
            client=self.client_obj,
            obligation=self.iva,
            status=ClientObligation.Status.ACTIVE,
            due_mode=ClientObligation.DueMode.AUTO,
            needs_manual_review=False,
        )
        ClientObligation.objects.create(
            client=self.client_obj,
            obligation=self.ire_anual,
            status=ClientObligation.Status.ACTIVE,
            due_mode=ClientObligation.DueMode.AUTO,
            needs_manual_review=False,
        )

    def test_generates_current_period_submission_idempotently(self):
        first = ensure_period_submissions_for_clients([self.client_obj], year=2026, month=3)
        second = ensure_period_submissions_for_clients([self.client_obj], year=2026, month=3)

        self.assertEqual(first["created"], 1)
        self.assertEqual(second["created"], 0)

        items = Submission.objects.filter(client=self.client_obj, obligation=self.iva, period_year=2026, period_month=3)
        self.assertEqual(items.count(), 1)
        item = items.first()
        self.assertEqual(item.period_kind, Submission.PeriodKind.MONTHLY)
        self.assertFalse(item.needs_manual_review)
        self.assertEqual(item.due_date, date(2026, 3, 11))

    def test_skips_generation_when_due_date_cannot_be_inferred(self):
        self.client_obj.ruc = ""
        self.client_obj.ruc_base = ""
        self.client_obj.save(update_fields=["ruc", "ruc_base", "updated_at"])

        result = ensure_period_submissions_for_clients([self.client_obj], year=2026, month=3)
        self.assertEqual(result["created"], 0)
        self.assertGreaterEqual(result["skipped"], 1)
        self.assertFalse(Submission.objects.filter(client=self.client_obj).exists())


class OperationalAssignmentPermissionTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="admin_ops_assign", password="secret123", role=User.Role.ADMIN)
        self.func1 = User.objects.create_user(username="func_ops_assign_1", password="secret123", role=User.Role.FUNCIONARIO)
        self.func2 = User.objects.create_user(username="func_ops_assign_2", password="secret123", role=User.Role.FUNCIONARIO)

        self.client1 = Client.objects.create(
            name="Cliente Ops 1",
            ruc="8001000-1",
            zone=Client.Zone.SANTA_RITA,
            status=Client.Status.ACTIVE,
            responsible=self.func1,
        )
        self.client2 = Client.objects.create(
            name="Cliente Ops 2",
            ruc="8002000-2",
            zone=Client.Zone.KM_32,
            status=Client.Status.ACTIVE,
            responsible=self.func2,
        )

        self.pending1 = PendingItem.objects.create(
            client=self.client1,
            description="Pendencia 1",
            status=PendingItem.Status.OPEN,
            created_by=self.admin,
        )
        self.pending2 = PendingItem.objects.create(
            client=self.client2,
            description="Pendencia 2",
            status=PendingItem.Status.OPEN,
            created_by=self.admin,
        )
        self.submission1 = Submission.objects.create(
            client=self.client1,
            submission_type="IVA",
            status=Submission.Status.PENDING,
            created_by=self.admin,
        )

    def test_funcionario_only_lists_assigned_pending_items(self):
        self.client.force_authenticate(self.func1)
        response = self.client.get("/pending-items")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["id"], self.pending1.id)

    def test_funcionario_cannot_create_pending_for_unassigned_client(self):
        self.client.force_authenticate(self.func1)
        response = self.client.post(
            "/pending-items",
            {
                "client": self.client2.id,
                "description": "Intento sin permiso",
                "priority": PendingItem.Priority.OK,
                "status": PendingItem.Status.OPEN,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_funcionario_cannot_move_pending_to_unassigned_client(self):
        self.client.force_authenticate(self.func1)
        response = self.client.patch(
            f"/pending-items/{self.pending1.id}",
            {"client": self.client2.id},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_funcionario_cannot_move_submission_to_unassigned_client(self):
        self.client.force_authenticate(self.func1)
        response = self.client.patch(
            f"/submissions/{self.submission1.id}",
            {"client": self.client2.id},
            format="json",
        )
        self.assertEqual(response.status_code, 403)


class SubmissionFiscalNormalizationApiTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="admin_submission_fiscal", password="secret123", role=User.Role.ADMIN)
        self.client.force_authenticate(self.admin)
        self.business = Client.objects.create(
            name="Cliente Fiscal",
            ruc="80146792-6",
            zone=Client.Zone.SANTA_RITA,
            status=Client.Status.ACTIVE,
        )
        self.iva = Obligation.objects.create(
            code="IVA_GENERAL_TEST",
            name="IVA General Test",
            form_code="120",
            default_periodicity="MONTHLY",
            default_due_mode="AUTO",
            is_active=True,
        )

    def test_create_submission_with_obligation_infers_monthly_period(self):
        response = self.client.post(
            "/submissions",
            {
                "client": self.business.id,
                "obligation": self.iva.id,
                "submission_type": "IVA Marzo 2026",
                "due_date": "2026-03-09",
                "status": Submission.Status.PENDING,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        payload = response.data
        self.assertEqual(payload["period_kind"], Submission.PeriodKind.MONTHLY)
        self.assertEqual(payload["period_year"], 2026)
        self.assertEqual(payload["period_month"], 3)
        self.assertFalse(payload["needs_manual_review"])

    def test_create_submission_without_obligation_sets_manual_review(self):
        response = self.client.post(
            "/submissions",
            {
                "client": self.business.id,
                "submission_type": "Presentacion sin clasificar",
                "status": Submission.Status.PENDING,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        payload = response.data
        self.assertIsNone(payload["obligation"])
        self.assertTrue(payload["needs_manual_review"])

    def test_list_submission_can_filter_by_obligation_and_period(self):
        Submission.objects.create(
            client=self.business,
            obligation=self.iva,
            submission_type="IVA Marzo",
            period_kind=Submission.PeriodKind.MONTHLY,
            period_year=2026,
            period_month=3,
            status=Submission.Status.PENDING,
            created_by=self.admin,
            needs_manual_review=False,
        )
        Submission.objects.create(
            client=self.business,
            obligation=self.iva,
            submission_type="IVA Abril",
            period_kind=Submission.PeriodKind.MONTHLY,
            period_year=2026,
            period_month=4,
            status=Submission.Status.PENDING,
            created_by=self.admin,
            needs_manual_review=False,
        )

        response = self.client.get(
            f"/submissions?obligation={self.iva.id}&period_year=2026&period_month=3"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["period_month"], 3)


class SubmissionArchiveApiTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="admin_submission_arch", password="secret123", role=User.Role.ADMIN)
        self.func1 = User.objects.create_user(
            username="func_submission_arch_1",
            password="secret123",
            role=User.Role.FUNCIONARIO,
        )
        self.func2 = User.objects.create_user(
            username="func_submission_arch_2",
            password="secret123",
            role=User.Role.FUNCIONARIO,
        )
        self.client1 = Client.objects.create(
            name="Cliente Arch 1",
            ruc="7005001-1",
            zone=Client.Zone.SANTA_RITA,
            status=Client.Status.ACTIVE,
            responsible=self.func1,
        )
        self.client2 = Client.objects.create(
            name="Cliente Arch 2",
            ruc="7005002-2",
            zone=Client.Zone.KM_32,
            status=Client.Status.ACTIVE,
            responsible=self.func2,
        )
        self.sub_submitted = Submission.objects.create(
            client=self.client1,
            submission_type="IVA General",
            status=Submission.Status.SUBMITTED,
            created_by=self.admin,
        )
        self.sub_pending = Submission.objects.create(
            client=self.client1,
            submission_type="IRE General",
            status=Submission.Status.PENDING,
            created_by=self.admin,
        )
        self.sub_other_client = Submission.objects.create(
            client=self.client2,
            submission_type="IRP",
            status=Submission.Status.SUBMITTED,
            created_by=self.admin,
        )

    def test_funcionario_can_archive_submitted_submission(self):
        self.client.force_authenticate(self.func1)
        response = self.client.post(f"/submissions/{self.sub_submitted.id}/archive")
        self.assertEqual(response.status_code, 200)
        self.sub_submitted.refresh_from_db()
        self.assertTrue(self.sub_submitted.is_archived)
        self.assertIsNotNone(self.sub_submitted.archived_at)
        self.assertEqual(self.sub_submitted.archived_by_id, self.func1.id)

    def test_archive_rejects_non_submitted_status(self):
        self.client.force_authenticate(self.func1)
        response = self.client.post(f"/submissions/{self.sub_pending.id}/archive")
        self.assertEqual(response.status_code, 400)
        self.sub_pending.refresh_from_db()
        self.assertFalse(self.sub_pending.is_archived)

    def test_funcionario_cannot_archive_submission_from_unassigned_client(self):
        self.client.force_authenticate(self.func1)
        response = self.client.post(f"/submissions/{self.sub_other_client.id}/archive")
        self.assertEqual(response.status_code, 404)

    def test_reopen_archived_submission(self):
        self.sub_submitted.is_archived = True
        self.sub_submitted.archived_by = self.admin
        self.sub_submitted.save(update_fields=["is_archived", "archived_by", "updated_at"])
        self.client.force_authenticate(self.func1)
        response = self.client.post(f"/submissions/{self.sub_submitted.id}/reopen")
        self.assertEqual(response.status_code, 200)
        self.sub_submitted.refresh_from_db()
        self.assertFalse(self.sub_submitted.is_archived)
        self.assertIsNone(self.sub_submitted.archived_at)
        self.assertIsNone(self.sub_submitted.archived_by_id)

    def test_submissions_list_can_filter_archived(self):
        self.sub_submitted.is_archived = True
        self.sub_submitted.archived_by = self.admin
        self.sub_submitted.save(update_fields=["is_archived", "archived_by", "updated_at"])

        self.client.force_authenticate(self.func1)

        archived = self.client.get("/submissions?archived=true")
        active = self.client.get("/submissions?archived=false")

        self.assertEqual(archived.status_code, 200)
        self.assertEqual(active.status_code, 200)
        self.assertEqual(len(archived.data), 1)
        self.assertEqual(archived.data[0]["id"], self.sub_submitted.id)
        self.assertEqual(len(active.data), 1)
        self.assertEqual(active.data[0]["id"], self.sub_pending.id)

    def test_funcionario_can_mark_submission_as_submitted(self):
        self.client.force_authenticate(self.func1)
        response = self.client.post(f"/submissions/{self.sub_pending.id}/mark-submitted")
        self.assertEqual(response.status_code, 200)
        self.sub_pending.refresh_from_db()
        self.assertEqual(self.sub_pending.status, Submission.Status.SUBMITTED)
        self.assertIsNotNone(self.sub_pending.submitted_at)

    def test_mark_submission_rejects_archived(self):
        self.sub_pending.is_archived = True
        self.sub_pending.save(update_fields=["is_archived", "updated_at"])
        self.client.force_authenticate(self.func1)
        response = self.client.post(f"/submissions/{self.sub_pending.id}/mark-submitted")
        self.assertEqual(response.status_code, 400)

    def test_funcionario_can_reactivate_submitted_submission(self):
        self.client.force_authenticate(self.func1)
        response = self.client.post(f"/submissions/{self.sub_submitted.id}/reactivate")
        self.assertEqual(response.status_code, 200)
        self.sub_submitted.refresh_from_db()
        self.assertEqual(self.sub_submitted.status, Submission.Status.PENDING)
        self.assertIsNone(self.sub_submitted.submitted_at)

    def test_reactivate_rejects_archived_submission(self):
        self.sub_submitted.is_archived = True
        self.sub_submitted.archived_by = self.admin
        self.sub_submitted.save(update_fields=["is_archived", "archived_by", "updated_at"])

        self.client.force_authenticate(self.func1)
        response = self.client.post(f"/submissions/{self.sub_submitted.id}/reactivate")
        self.assertEqual(response.status_code, 400)


class SubmissionArchiveWebTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="admin_submission_web", password="secret123", role=User.Role.ADMIN)
        self.func1 = User.objects.create_user(username="func_submission_web_1", password="secret123", role=User.Role.FUNCIONARIO)
        self.func2 = User.objects.create_user(username="func_submission_web_2", password="secret123", role=User.Role.FUNCIONARIO)
        self.client1 = Client.objects.create(
            name="Cliente Web Arch 1",
            ruc="7999001-1",
            zone=Client.Zone.SANTA_RITA,
            status=Client.Status.ACTIVE,
            responsible=self.func1,
        )
        self.client2 = Client.objects.create(
            name="Cliente Web Arch 2",
            ruc="7999002-2",
            zone=Client.Zone.KM_32,
            status=Client.Status.ACTIVE,
            responsible=self.func2,
        )
        self.submission = Submission.objects.create(
            client=self.client1,
            submission_type="IVA",
            status=Submission.Status.SUBMITTED,
            created_by=self.admin,
        )
        self.pending_submission = Submission.objects.create(
            client=self.client1,
            submission_type="IVA Pendiente",
            status=Submission.Status.PENDING,
            created_by=self.admin,
        )
        self.other_submission = Submission.objects.create(
            client=self.client2,
            submission_type="IRE",
            status=Submission.Status.SUBMITTED,
            created_by=self.admin,
        )

    def test_funcionario_can_archive_own_submission_via_web(self):
        self.client.force_login(self.func1)
        response = self.client.post(
            f"/app/submissions/{self.submission.id}/archive/",
            {"next": "/app/submissions/"},
        )
        self.assertEqual(response.status_code, 302)
        self.submission.refresh_from_db()
        self.assertTrue(self.submission.is_archived)

    def test_funcionario_cannot_archive_unassigned_submission_via_web(self):
        self.client.force_login(self.func1)
        response = self.client.post(
            f"/app/submissions/{self.other_submission.id}/archive/",
            {"next": "/app/submissions/"},
        )
        self.assertEqual(response.status_code, 403)

    def test_funcionario_can_mark_own_submission_as_submitted_via_web(self):
        self.client.force_login(self.func1)
        response = self.client.post(
            f"/app/submissions/{self.pending_submission.id}/mark-submitted/",
            {"next": "/app/submissions/"},
        )
        self.assertEqual(response.status_code, 302)
        self.pending_submission.refresh_from_db()
        self.assertEqual(self.pending_submission.status, Submission.Status.SUBMITTED)
        self.assertIsNotNone(self.pending_submission.submitted_at)

    def test_funcionario_can_reactivate_submitted_submission_via_web(self):
        self.client.force_login(self.func1)
        response = self.client.post(
            f"/app/submissions/{self.submission.id}/reactivate/",
            {"next": "/app/submissions/?scope=submitted"},
        )
        self.assertEqual(response.status_code, 302)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.status, Submission.Status.PENDING)
        self.assertIsNone(self.submission.submitted_at)


class SubmissionListWebViewTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="admin_submission_list", password="secret123", role=User.Role.ADMIN)
        self.business = Client.objects.create(
            name="Cliente Lista Submissions",
            ruc="7777001-1",
            zone=Client.Zone.SANTA_RITA,
            status=Client.Status.ACTIVE,
        )
        self.iva = Obligation.objects.create(
            code="IVA_LIST_TEST",
            name="IVA List Test",
            form_code="120",
            default_periodicity="MONTHLY",
            default_due_mode="AUTO",
            is_active=True,
        )
        Submission.objects.create(
            client=self.business,
            obligation=self.iva,
            submission_type="IVA Marzo 2026",
            period_kind=Submission.PeriodKind.MONTHLY,
            period_year=2026,
            period_month=3,
            status=Submission.Status.PENDING,
            created_by=self.admin,
            needs_manual_review=False,
        )

    def test_submission_list_page_loads(self):
        self.client.force_login(self.admin)
        response = self.client.get("/app/submissions/")
        self.assertEqual(response.status_code, 200)

    def test_submission_list_page_loads_with_fiscal_filters(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            f"/app/submissions/?obligation={self.iva.id}&period_year=2026&period_month=3&scope=all"
        )
        self.assertEqual(response.status_code, 200)


class SubmissionListAutoGenerationWebTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="admin_submission_auto_list", password="secret123", role=User.Role.ADMIN)
        self.client_obj = Client.objects.create(
            name="Cliente Auto List",
            ruc="80146792-6",
            zone=Client.Zone.SANTA_RITA,
            status=Client.Status.ACTIVE,
        )
        self.iva = Obligation.objects.create(
            code="IVA_AUTO_LIST_TEST",
            name="IVA Auto List Test",
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

    def test_submission_list_generates_current_period_obligation(self):
        self.client.force_login(self.admin)
        self.assertEqual(Submission.objects.filter(client=self.client_obj, obligation=self.iva).count(), 0)

        response = self.client.get("/app/submissions/?scope=main")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Submission.objects.filter(client=self.client_obj, obligation=self.iva).count(), 1)


class SubmissionListActionVisibilityWebTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="admin_submission_actions", password="secret123", role=User.Role.ADMIN)
        self.client_obj = Client.objects.create(
            name="Cliente Lista Acciones",
            ruc="80146792-6",
            zone=Client.Zone.SANTA_RITA,
            status=Client.Status.ACTIVE,
        )
        self.iva = Obligation.objects.create(
            code="IVA_ACTIONS_TEST",
            name="IVA Actions Test",
            uses_ruc_calendar=True,
            is_active=True,
            default_periodicity="MONTHLY",
            default_due_mode="AUTO",
        )
        self.active_submission = Submission.objects.create(
            client=self.client_obj,
            obligation=self.iva,
            submission_type="IVA Activo",
            period_kind=Submission.PeriodKind.MONTHLY,
            period_year=2026,
            period_month=3,
            status=Submission.Status.PENDING,
        )
        self.submitted_submission = Submission.objects.create(
            client=self.client_obj,
            obligation=self.iva,
            submission_type="IVA Finalizado",
            period_kind=Submission.PeriodKind.MONTHLY,
            period_year=2026,
            period_month=2,
            status=Submission.Status.SUBMITTED,
            submitted_at=date(2026, 2, 10),
        )
        self.archived_submission = Submission.objects.create(
            client=self.client_obj,
            obligation=self.iva,
            submission_type="IVA Archivado",
            period_kind=Submission.PeriodKind.MONTHLY,
            period_year=2026,
            period_month=1,
            status=Submission.Status.SUBMITTED,
            submitted_at=date(2026, 1, 10),
            is_archived=True,
        )

    def test_submission_list_shows_quick_actions_by_state(self):
        self.client.force_login(self.admin)
        response = self.client.get("/app/submissions/?scope=all")
        html = response.content.decode("utf-8")

        self.assertEqual(response.status_code, 200)
        self.assertIn(f"/app/submissions/{self.active_submission.id}/mark-submitted/", html)
        self.assertIn(f"/app/submissions/{self.submitted_submission.id}/reactivate/", html)
        self.assertIn(f"/app/submissions/{self.submitted_submission.id}/archive/", html)
        self.assertIn(f"/app/submissions/{self.archived_submission.id}/reopen/", html)


class RecalculateSubmissionDueDatesCommandTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="admin_due_recalc", password="secret123", role=User.Role.ADMIN)
        self.client_obj = Client.objects.create(
            name="Cliente Recalc",
            ruc="80146792-6",
            zone=Client.Zone.SANTA_RITA,
            status=Client.Status.ACTIVE,
        )
        self.iva = Obligation.objects.create(
            code="IVA_RECALC_TEST",
            name="IVA Recalc Test",
            uses_ruc_calendar=True,
            is_active=True,
            default_periodicity="MONTHLY",
            default_due_mode="AUTO",
        )

    def test_dry_run_does_not_persist_changes(self):
        submission = Submission.objects.create(
            client=self.client_obj,
            obligation=self.iva,
            submission_type="IVA Marzo 2026",
            period_kind=Submission.PeriodKind.MONTHLY,
            period_year=2026,
            period_month=3,
            due_date=date(2026, 3, 9),
            status=Submission.Status.PENDING,
            created_by=None,
            needs_manual_review=False,
        )

        output = StringIO()
        call_command(
            "recalculate_submission_due_dates",
            "--year",
            "2026",
            "--month",
            "3",
            stdout=output,
        )

        submission.refresh_from_db()
        self.assertEqual(submission.due_date, date(2026, 3, 9))
        self.assertIn("Dry-run", output.getvalue())

    def test_apply_updates_auto_generated_active_submission_only(self):
        auto_item = Submission.objects.create(
            client=self.client_obj,
            obligation=self.iva,
            submission_type="IVA Marzo 2026",
            period_kind=Submission.PeriodKind.MONTHLY,
            period_year=2026,
            period_month=3,
            due_date=date(2026, 3, 9),
            status=Submission.Status.PENDING,
            created_by=None,
            needs_manual_review=False,
        )
        manual_item = Submission.objects.create(
            client=self.client_obj,
            obligation=self.iva,
            submission_type="IVA Marzo 2026 Manual",
            period_kind=Submission.PeriodKind.MONTHLY,
            period_year=2026,
            period_month=3,
            due_date=date(2026, 3, 9),
            status=Submission.Status.PENDING,
            created_by=self.admin,
            needs_manual_review=False,
        )
        submitted_item = Submission.objects.create(
            client=self.client_obj,
            obligation=self.iva,
            submission_type="IVA Marzo 2026 Presentado",
            period_kind=Submission.PeriodKind.MONTHLY,
            period_year=2026,
            period_month=3,
            due_date=date(2026, 3, 9),
            status=Submission.Status.SUBMITTED,
            created_by=None,
            needs_manual_review=False,
        )

        call_command(
            "recalculate_submission_due_dates",
            "--year",
            "2026",
            "--month",
            "3",
            "--apply",
        )

        auto_item.refresh_from_db()
        manual_item.refresh_from_db()
        submitted_item.refresh_from_db()

        self.assertEqual(auto_item.due_date, date(2026, 3, 11))
        self.assertEqual(manual_item.due_date, date(2026, 3, 9))
        self.assertEqual(submitted_item.due_date, date(2026, 3, 9))

        has_audit = AuditLog.objects.filter(
            entity="submission",
            entity_id=str(auto_item.id),
            action="recalculate_due_date_command",
        ).exists()
        self.assertTrue(has_audit)
