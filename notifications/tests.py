from django.test import TestCase

from accounts.models import User
from clients.models import Client
from notifications.models import UserNotification
from notifications.services import notify_users, recipients_for_client


class NotificationServiceTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="admin_notif", password="secret123", role=User.Role.ADMIN)
        self.func = User.objects.create_user(username="func_notif", password="secret123", role=User.Role.FUNCIONARIO)
        self.client_obj = Client.objects.create(name="Cliente Notif", responsible=self.func)

    def test_notify_users_excludes_actor_and_dedups_recent_event(self):
        recipients = recipients_for_client(client=self.client_obj, extras=[self.admin])

        created_first = notify_users(
            actor=self.admin,
            recipient_ids=recipients,
            client=self.client_obj,
            message="Prueba",
            severity=UserNotification.Severity.NORMAL,
            event_key="evt_test",
            source_ref="pending:1",
            target_url="/app/pending-items/",
        )
        created_second = notify_users(
            actor=self.admin,
            recipient_ids=recipients,
            client=self.client_obj,
            message="Prueba",
            severity=UserNotification.Severity.NORMAL,
            event_key="evt_test",
            source_ref="pending:1",
            target_url="/app/pending-items/",
        )

        self.assertEqual(created_first, 1)
        self.assertEqual(created_second, 0)
        self.assertEqual(UserNotification.objects.filter(recipient=self.func).count(), 1)
        self.assertEqual(UserNotification.objects.filter(recipient=self.admin).count(), 0)


class NotificationPanelViewTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="admin_notif_view", password="secret123", role=User.Role.ADMIN)
        self.func = User.objects.create_user(username="func_notif_view", password="secret123", role=User.Role.FUNCIONARIO)
        self.client_obj = Client.objects.create(name="Cliente Panel", responsible=self.func)
        UserNotification.objects.create(
            recipient=self.func,
            actor=self.admin,
            client=self.client_obj,
            severity=UserNotification.Severity.URGENT,
            message="Pendiente urgente creado",
            target_url="/app/pending-items/",
            event_key="pending_created",
            source_ref="pending:99",
        )

    def test_panel_returns_only_current_user_notifications(self):
        self.client.force_login(self.func)
        response = self.client.get("/app/notifications/panel/", HTTP_X_REQUESTED_WITH="XMLHttpRequest")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["unread_count"], 1)
        self.assertIn("Pendiente urgente creado", payload["html"])

    def test_mark_read_updates_unread_for_current_user(self):
        self.client.force_login(self.func)
        response = self.client.post("/app/notifications/mark-read/", HTTP_X_REQUESTED_WITH="XMLHttpRequest")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["marked"], 1)
        self.assertFalse(UserNotification.objects.filter(recipient=self.func, is_read=False).exists())
