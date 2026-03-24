from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import User
from auditing.models import AuditLog
from banks.services import create_or_link_document_pending
from banks.models import BankRequest
from clients.models import Client
from notifications.models import UserNotification
from operations.models import PendingItem


class BankRequestModelTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="admin_bank_model", password="secret123", role=User.Role.ADMIN)
        self.funcionario = User.objects.create_user(
            username="func_bank_model",
            password="secret123",
            role=User.Role.FUNCIONARIO,
        )
        self.client_obj = Client.objects.create(
            name="Cliente Bank Model",
            ruc="8012345-6",
            zone=Client.Zone.SANTA_RITA,
            status=Client.Status.ACTIVE,
        )

    def test_can_create_bank_request_with_defaults(self):
        item = BankRequest.objects.create(
            client=self.client_obj,
            requested_by=self.admin,
            responsible=self.funcionario,
        )

        self.assertEqual(item.status, BankRequest.Status.REQUESTED)
        self.assertEqual(item.request_type, BankRequest.RequestType.PROVISORIO)
        self.assertEqual(item.receipts_status, BankRequest.DocumentStatus.PENDING)

    def test_other_request_type_requires_description(self):
        item = BankRequest(
            client=self.client_obj,
            request_type=BankRequest.RequestType.OTRO,
            requested_by=self.admin,
            responsible=self.funcionario,
        )

        with self.assertRaises(ValidationError):
            item.full_clean()

    def test_responsible_must_be_funcionario(self):
        item = BankRequest(
            client=self.client_obj,
            requested_by=self.admin,
            responsible=self.admin,
        )

        with self.assertRaises(ValidationError):
            item.full_clean()


class BankRequestApiTests(APITestCase):
    def setUp(self):
        self.master = User.objects.create_user(username="master_bank_api", password="secret123", role=User.Role.MASTER)
        self.admin = User.objects.create_user(username="admin_bank_api", password="secret123", role=User.Role.ADMIN)
        self.func1 = User.objects.create_user(username="func_bank_api_1", password="secret123", role=User.Role.FUNCIONARIO)
        self.func2 = User.objects.create_user(username="func_bank_api_2", password="secret123", role=User.Role.FUNCIONARIO)

        self.client1 = Client.objects.create(
            name="Cliente Banco 1",
            ruc="8011111-1",
            zone=Client.Zone.SANTA_RITA,
            status=Client.Status.ACTIVE,
            responsible=self.func1,
        )
        self.client2 = Client.objects.create(
            name="Cliente Banco 2",
            ruc="8022222-2",
            zone=Client.Zone.KM_32,
            status=Client.Status.ACTIVE,
            responsible=self.func2,
        )

        self.request1 = BankRequest.objects.create(
            client=self.client1,
            request_type=BankRequest.RequestType.PROVISORIO,
            status=BankRequest.Status.REQUESTED,
            responsible=self.func1,
            requested_by=self.admin,
        )
        self.request2 = BankRequest.objects.create(
            client=self.client2,
            request_type=BankRequest.RequestType.FLUJO_CAJA,
            status=BankRequest.Status.REQUESTED,
            responsible=self.func2,
            requested_by=self.admin,
        )

    def test_funcionario_only_sees_assigned_requests(self):
        self.client.force_authenticate(self.func1)

        res_list = self.client.get("/bank-requests")
        self.assertEqual(res_list.status_code, 200)
        self.assertEqual(len(res_list.data), 1)
        self.assertEqual(res_list.data[0]["id"], self.request1.id)

        res_forbidden_detail = self.client.get(f"/bank-requests/{self.request2.id}")
        self.assertEqual(res_forbidden_detail.status_code, 404)

    def test_funcionario_cannot_create_or_patch_bank_request(self):
        self.client.force_authenticate(self.func1)

        res_create = self.client.post(
            "/bank-requests",
            {
                "client": self.client1.id,
                "request_type": BankRequest.RequestType.PROVISORIO,
            },
        )
        res_patch = self.client.patch(
            f"/bank-requests/{self.request1.id}",
            {"status": BankRequest.Status.COMPLETED},
            format="json",
        )

        self.assertEqual(res_create.status_code, 403)
        self.assertEqual(res_patch.status_code, 403)

    def test_admin_creates_receipts_pending_and_funcionario_marks_loaded(self):
        self.client.force_authenticate(self.admin)
        create_pending = self.client.post(
            f"/bank-requests/{self.request1.id}/create-receipts-pending",
            {
                "missing_documents": "Recibos de marzo",
                "description": "Contactar cliente por recibos",
            },
            format="json",
        )
        self.assertEqual(create_pending.status_code, 200)
        linked_pending_id = create_pending.data["linked_pending_item"]

        self.request1.refresh_from_db()
        self.assertEqual(self.request1.receipts_status, BankRequest.DocumentStatus.PENDING)
        self.assertEqual(self.request1.receipts_pending_item_id, linked_pending_id)

        pending = PendingItem.objects.get(id=linked_pending_id)
        self.assertEqual(pending.status, PendingItem.Status.OPEN)
        self.assertEqual(pending.priority, PendingItem.Priority.OK)

        self.client.force_authenticate(self.func1)
        mark_loaded = self.client.post(f"/bank-requests/{self.request1.id}/mark-receipts-loaded", {}, format="json")
        self.assertEqual(mark_loaded.status_code, 200)

        self.request1.refresh_from_db()
        pending.refresh_from_db()
        self.assertEqual(self.request1.receipts_status, BankRequest.DocumentStatus.LOADED)
        self.assertEqual(self.request1.receipts_loaded_by_id, self.func1.id)
        self.assertEqual(pending.status, PendingItem.Status.RESOLVED)
        self.assertIsNotNone(pending.resolved_at)

    def test_admin_can_create_non_urgent_receipts_pending_via_api(self):
        self.client.force_authenticate(self.admin)
        create_pending = self.client.post(
            f"/bank-requests/{self.request1.id}/create-receipts-pending",
            {
                "description": "Recibos seguimiento normal",
                "priority": PendingItem.Priority.OK,
            },
            format="json",
        )
        self.assertEqual(create_pending.status_code, 200)
        linked_pending_id = create_pending.data["linked_pending_item"]

        pending = PendingItem.objects.get(id=linked_pending_id)
        self.assertEqual(pending.priority, PendingItem.Priority.OK)
        self.assertEqual(pending.status, PendingItem.Status.OPEN)

    def test_admin_creates_urgent_pending_by_default_when_request_priority_is_urgent(self):
        self.request1.request_priority = BankRequest.Priority.URGENT
        self.request1.save(update_fields=["request_priority", "updated_at"])

        self.client.force_authenticate(self.admin)
        create_pending = self.client.post(
            f"/bank-requests/{self.request1.id}/create-receipts-pending",
            {
                "description": "Recibos seguimiento",
            },
            format="json",
        )
        self.assertEqual(create_pending.status_code, 200)
        linked_pending_id = create_pending.data["linked_pending_item"]
        pending = PendingItem.objects.get(id=linked_pending_id)
        self.assertEqual(pending.priority, PendingItem.Priority.URGENT)

    def test_cannot_move_in_progress_or_complete_until_documents_are_loaded(self):
        self.client.force_authenticate(self.admin)

        blocked = self.client.post(f"/bank-requests/{self.request1.id}/mark-in-progress", {}, format="json")
        self.assertEqual(blocked.status_code, 400)
        self.assertIn("Recibos", blocked.data["detail"])

        self.client.post(f"/bank-requests/{self.request1.id}/mark-receipts-loaded", {}, format="json")
        can_start = self.client.post(f"/bank-requests/{self.request1.id}/mark-in-progress", {}, format="json")
        self.assertEqual(can_start.status_code, 200)
        self.assertEqual(can_start.data["status"], BankRequest.Status.IN_PROGRESS)

        can_complete = self.client.post(f"/bank-requests/{self.request1.id}/mark-completed", {}, format="json")
        self.assertEqual(can_complete.status_code, 200)
        self.assertEqual(can_complete.data["status"], BankRequest.Status.COMPLETED)

    def test_archive_and_reopen_flow_for_manager(self):
        self.request1.receipts_status = BankRequest.DocumentStatus.LOADED
        self.request1.status = BankRequest.Status.COMPLETED
        self.request1.save(update_fields=["receipts_status", "status", "updated_at"])

        self.client.force_authenticate(self.master)

        archived = self.client.post(f"/bank-requests/{self.request1.id}/archive", {}, format="json")
        self.assertEqual(archived.status_code, 200)
        self.assertEqual(archived.data["status"], BankRequest.Status.ARCHIVED)

        reopened = self.client.post(f"/bank-requests/{self.request1.id}/reopen", {}, format="json")
        self.assertEqual(reopened.status_code, 200)
        self.assertEqual(reopened.data["status"], BankRequest.Status.IN_PROGRESS)

    def test_funcionario_can_add_note_only_to_assigned_request(self):
        self.client.force_authenticate(self.func1)

        own_note = self.client.post(
            f"/bank-requests/{self.request1.id}/add-note",
            {"note": "Cliente confirmó entrega mañana."},
            format="json",
        )
        other_note = self.client.post(
            f"/bank-requests/{self.request2.id}/add-note",
            {"note": "No debería poder."},
            format="json",
        )

        self.assertEqual(own_note.status_code, 200)
        self.assertEqual(other_note.status_code, 404)

        self.request1.refresh_from_db()
        self.assertEqual(self.request1.last_note, "Cliente confirmó entrega mañana.")
        self.assertEqual(self.request1.last_note_by_id, self.func1.id)
        self.assertIsNotNone(self.request1.last_note_at)

    def test_audit_log_is_created_for_bank_request_actions(self):
        self.client.force_authenticate(self.admin)
        self.client.post(f"/bank-requests/{self.request1.id}/mark-receipts-loaded", {}, format="json")

        has_bank_log = AuditLog.objects.filter(
            entity="bankrequest",
            entity_id=str(self.request1.id),
            action="mark_receipts_loaded",
        ).exists()

        self.assertTrue(has_bank_log)


class BankRequestWebTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="admin_bank_web", password="secret123", role=User.Role.ADMIN)
        self.func1 = User.objects.create_user(username="func_bank_web_1", password="secret123", role=User.Role.FUNCIONARIO)
        self.func2 = User.objects.create_user(username="func_bank_web_2", password="secret123", role=User.Role.FUNCIONARIO)

        self.client1 = Client.objects.create(
            name="Cliente Banco Web 1",
            ruc="8033333-3",
            zone=Client.Zone.SANTA_RITA,
            status=Client.Status.ACTIVE,
            responsible=self.func1,
        )
        self.client2 = Client.objects.create(
            name="Cliente Banco Web 2",
            ruc="8044444-4",
            zone=Client.Zone.KM_32,
            status=Client.Status.ACTIVE,
            responsible=self.func2,
        )

        self.request1 = BankRequest.objects.create(
            client=self.client1,
            request_type=BankRequest.RequestType.PROVISORIO,
            status=BankRequest.Status.REQUESTED,
            responsible=self.func1,
            requested_by=self.admin,
        )
        self.request2 = BankRequest.objects.create(
            client=self.client2,
            request_type=BankRequest.RequestType.FLUJO_CAJA,
            status=BankRequest.Status.REQUESTED,
            responsible=self.func2,
            requested_by=self.admin,
        )

    def test_funcionario_sees_only_assigned_bank_requests_on_web(self):
        self.client.force_login(self.func1)

        response = self.client.get("/app/banks/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cliente Banco Web 1")
        self.assertNotContains(response, "Cliente Banco Web 2")

    def test_funcionario_can_mark_receipts_loaded_and_resolve_pending(self):
        create_or_link_document_pending(
            item=self.request1,
            actor=self.admin,
            document_kind="receipts",
            description="Recibos pendientes",
            missing_documents="Recibo marzo",
        )
        pending = self.request1.receipts_pending_item

        self.client.force_login(self.func1)
        response = self.client.post(
            f"/app/banks/{self.request1.id}/mark-receipts-loaded/",
            {"next": "/app/banks/"},
        )

        self.assertEqual(response.status_code, 302)
        self.request1.refresh_from_db()
        pending.refresh_from_db()
        self.assertEqual(self.request1.receipts_status, BankRequest.DocumentStatus.LOADED)
        self.assertEqual(self.request1.receipts_loaded_by_id, self.func1.id)
        self.assertEqual(pending.status, PendingItem.Status.RESOLVED)

    def test_funcionario_cannot_access_note_form_for_other_assignment(self):
        self.client.force_login(self.func1)
        response = self.client.get(f"/app/banks/{self.request2.id}/note/")
        self.assertEqual(response.status_code, 403)

    def test_admin_can_move_bank_request_to_completed_from_web_actions(self):
        self.client.force_login(self.admin)

        self.client.post(f"/app/banks/{self.request1.id}/create-receipts-pending/", {"next": "/app/banks/"})
        self.client.post(f"/app/banks/{self.request1.id}/mark-receipts-loaded/", {"next": "/app/banks/"})
        in_progress = self.client.post(f"/app/banks/{self.request1.id}/mark-in-progress/", {"next": "/app/banks/"})
        completed = self.client.post(f"/app/banks/{self.request1.id}/mark-completed/", {"next": "/app/banks/"})

        self.assertEqual(in_progress.status_code, 302)
        self.assertEqual(completed.status_code, 302)
        self.request1.refresh_from_db()
        self.assertEqual(self.request1.status, BankRequest.Status.COMPLETED)

    def test_admin_can_create_normal_receipts_pending_from_web_action(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            f"/app/banks/{self.request1.id}/create-receipts-pending/",
            {"next": "/app/banks/", "priority": PendingItem.Priority.OK},
        )
        self.assertEqual(response.status_code, 302)
        self.request1.refresh_from_db()
        self.assertIsNotNone(self.request1.receipts_pending_item_id)
        self.assertEqual(self.request1.receipts_pending_item.priority, PendingItem.Priority.OK)

    def test_funcionario_can_reset_receipts_to_pending(self):
        self.request1.receipts_status = BankRequest.DocumentStatus.LOADED
        self.request1.receipts_loaded_by = self.admin
        self.request1.receipts_loaded_at = timezone.now()
        self.request1.receipts_client_notified = True
        self.request1.receipts_notified_by = self.func1
        self.request1.receipts_notified_at = timezone.now()
        self.request1.save(
            update_fields=[
                "receipts_status",
                "receipts_loaded_by",
                "receipts_loaded_at",
                "receipts_client_notified",
                "receipts_notified_by",
                "receipts_notified_at",
                "updated_at",
            ]
        )

        self.client.force_login(self.func1)
        response = self.client.post(
            f"/app/banks/{self.request1.id}/mark-receipts-pending/",
            {"next": "/app/banks/"},
        )

        self.assertEqual(response.status_code, 302)
        self.request1.refresh_from_db()
        self.assertEqual(self.request1.receipts_status, BankRequest.DocumentStatus.PENDING)
        self.assertIsNone(self.request1.receipts_loaded_by_id)
        self.assertIsNone(self.request1.receipts_loaded_at)
        self.assertFalse(self.request1.receipts_client_notified)
        self.assertIsNone(self.request1.receipts_notified_by_id)
        self.assertIsNone(self.request1.receipts_notified_at)

    def test_funcionario_can_mark_client_notified_for_pending_receipts(self):
        self.client.force_login(self.func1)
        response = self.client.post(
            f"/app/banks/{self.request1.id}/mark-receipts-notified/",
            {"next": "/app/banks/"},
        )

        self.assertEqual(response.status_code, 302)
        self.request1.refresh_from_db()
        self.assertTrue(self.request1.receipts_client_notified)
        self.assertEqual(self.request1.receipts_notified_by_id, self.func1.id)
        self.assertIsNotNone(self.request1.receipts_notified_at)
        self.assertTrue(
            UserNotification.objects.filter(
                recipient_id=self.admin.id,
                event_key="bank_request_receipts_notified",
                source_ref=f"bank_request:{self.request1.id}",
            ).exists()
        )
        self.assertFalse(
            UserNotification.objects.filter(
                recipient_id=self.func1.id,
                event_key="bank_request_receipts_notified",
                source_ref=f"bank_request:{self.request1.id}",
            ).exists()
        )

    def test_bank_list_shows_notified_badge_and_hides_action_after_mark(self):
        self.client.force_login(self.func1)
        before = self.client.get("/app/banks/")
        self.assertEqual(before.status_code, 200)
        self.assertContains(before, f"/app/banks/{self.request1.id}/mark-receipts-notified/")

        self.client.post(
            f"/app/banks/{self.request1.id}/mark-receipts-notified/",
            {"next": "/app/banks/"},
        )

        after = self.client.get("/app/banks/")
        html = after.content.decode("utf-8")
        self.assertEqual(after.status_code, 200)
        self.assertIn("Avisado", html)
        self.assertNotIn(f"/app/banks/{self.request1.id}/mark-receipts-notified/", html)

    def test_web_note_updates_last_observation_fields(self):
        self.client.force_login(self.func1)
        response = self.client.post(
            f"/app/banks/{self.request1.id}/note/",
            {"note": "Cliente traerá recibos mañana."},
        )
        self.assertEqual(response.status_code, 302)
        self.request1.refresh_from_db()
        self.assertEqual(self.request1.last_note, "Cliente traerá recibos mañana.")
        self.assertEqual(self.request1.last_note_by_id, self.func1.id)
        self.assertIsNotNone(self.request1.last_note_at)

    def test_bank_list_shows_last_observation_without_extra_column(self):
        self.request1.last_note = "Falta recibo de febrero."
        self.request1.last_note_by = self.admin
        self.request1.last_note_at = timezone.now()
        self.request1.save(update_fields=["last_note", "last_note_by", "last_note_at", "updated_at"])

        self.client.force_login(self.func1)
        response = self.client.get("/app/banks/")
        html = response.content.decode("utf-8")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Obs: Falta recibo de febrero.", html)
        self.assertIn("admin_bank_web", html)

    def test_pending_list_shows_bank_origin_and_link_to_request(self):
        create_or_link_document_pending(
            item=self.request1,
            actor=self.admin,
            document_kind="receipts",
            description="Recibos pendientes origen banco",
            missing_documents="Recibos abril",
        )

        self.client.force_login(self.func1)
        response = self.client.get("/app/pending-items/")
        html = response.content.decode("utf-8")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Bancos y recibos / Recibos", html)
        self.assertIn(
            f"/app/banks/?scope=active&amp;focus={self.request1.id}#bank-request-{self.request1.id}",
            html,
        )

    def test_mark_bank_document_loaded_from_pending_list_auto_resolves(self):
        create_or_link_document_pending(
            item=self.request1,
            actor=self.admin,
            document_kind="receipts",
            description="Recibos pendientes origen banco",
            missing_documents="Recibos abril",
        )
        pending = self.request1.receipts_pending_item

        self.client.force_login(self.func1)
        response = self.client.post(
            f"/app/pending-items/{pending.id}/bank-document-loaded/receipts/",
            {"next": "/app/pending-items/"},
        )

        self.assertEqual(response.status_code, 302)
        self.request1.refresh_from_db()
        pending.refresh_from_db()
        self.assertEqual(self.request1.receipts_status, BankRequest.DocumentStatus.LOADED)
        self.assertEqual(self.request1.receipts_loaded_by_id, self.func1.id)
        self.assertEqual(pending.status, PendingItem.Status.RESOLVED)

    def test_pending_document_loaded_action_forbidden_for_other_assignment(self):
        create_or_link_document_pending(
            item=self.request2,
            actor=self.admin,
            document_kind="receipts",
            description="Recibos de otro responsable",
            missing_documents="Recibos mayo",
        )
        pending = self.request2.receipts_pending_item

        self.client.force_login(self.func1)
        response = self.client.post(
            f"/app/pending-items/{pending.id}/bank-document-loaded/receipts/",
            {"next": "/app/pending-items/"},
        )
        self.assertEqual(response.status_code, 403)
