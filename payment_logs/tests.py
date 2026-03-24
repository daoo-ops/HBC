from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from auditing.models import AuditLog
from clients.models import Client
from payment_logs.models import PaymentReceptionLog


class PaymentLogModelValidationTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="admin_paylog_model", password="secret123", role=User.Role.ADMIN)
        self.client_obj = Client.objects.create(
            name="Cliente Modelo Pago",
            ruc="7001001",
            zone=Client.Zone.OTHER,
            status=Client.Status.ACTIVE,
        )

    def test_requires_concept_other_when_type_is_otros(self):
        item = PaymentReceptionLog(
            client=self.client_obj,
            payment_date=date.today(),
            paid_by="Persona",
            concept_type=PaymentReceptionLog.ConceptType.OTROS,
            payment_method=PaymentReceptionLog.PaymentMethod.TRANSFERENCIA,
            recorded_by=self.admin,
        )
        with self.assertRaises(ValidationError):
            item.full_clean()

    def test_requires_third_party_name_for_cheque_tercero(self):
        item = PaymentReceptionLog(
            client=self.client_obj,
            payment_date=date.today(),
            paid_by="Persona",
            concept_type=PaymentReceptionLog.ConceptType.HONORARIOS,
            payment_method=PaymentReceptionLog.PaymentMethod.CHEQUE_TERCERO,
            recorded_by=self.admin,
        )
        with self.assertRaises(ValidationError):
            item.full_clean()

    def test_cleans_non_applicable_fields(self):
        item = PaymentReceptionLog.objects.create(
            client=self.client_obj,
            payment_date=date.today(),
            paid_by="Persona",
            concept_type=PaymentReceptionLog.ConceptType.IMPUESTOS,
            concept_other="No aplica",
            payment_method=PaymentReceptionLog.PaymentMethod.EFECTIVO,
            third_party_check_name="No aplica",
            recorded_by=self.admin,
        )
        self.assertEqual(item.concept_other, "")
        self.assertEqual(item.third_party_check_name, "")


class PaymentLogWebAccessTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="admin_paylog_web", password="secret123", role=User.Role.ADMIN)
        self.func_1 = User.objects.create_user(username="func_paylog_web_1", password="secret123", role=User.Role.FUNCIONARIO)
        self.func_2 = User.objects.create_user(username="func_paylog_web_2", password="secret123", role=User.Role.FUNCIONARIO)

        self.client_1 = Client.objects.create(
            name="Cliente Responsable 1",
            ruc="7001002",
            zone=Client.Zone.OTHER,
            status=Client.Status.ACTIVE,
            responsible=self.func_1,
        )
        self.client_2 = Client.objects.create(
            name="Cliente Responsable 2",
            ruc="7001003",
            zone=Client.Zone.OTHER,
            status=Client.Status.ACTIVE,
            responsible=self.func_2,
        )

        self.log_1 = PaymentReceptionLog.objects.create(
            client=self.client_1,
            payment_date=date.today() - timedelta(days=1),
            paid_by="Pago 1",
            concept_type=PaymentReceptionLog.ConceptType.HONORARIOS,
            payment_method=PaymentReceptionLog.PaymentMethod.TRANSFERENCIA,
            recorded_by=self.admin,
        )
        self.log_2 = PaymentReceptionLog.objects.create(
            client=self.client_2,
            payment_date=date.today(),
            paid_by="Pago 2",
            concept_type=PaymentReceptionLog.ConceptType.IMPUESTOS,
            payment_method=PaymentReceptionLog.PaymentMethod.EFECTIVO,
            recorded_by=self.admin,
        )

    def test_funcionario_only_sees_assigned_client_logs(self):
        self.client.force_login(self.func_1)
        response = self.client.get(reverse("app-payment-log-list"))
        html = response.content.decode("utf-8")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Cliente Responsable 1", html)
        self.assertNotIn("Cliente Responsable 2", html)

    def test_funcionario_direct_url_is_blocked_for_other_client(self):
        self.client.force_login(self.func_1)

        response_edit = self.client.get(reverse("app-payment-log-edit", args=[self.log_2.id]))
        response_archive = self.client.post(reverse("app-payment-log-archive", args=[self.log_2.id]))
        response_create_other = self.client.post(
            reverse("app-payment-log-create"),
            data={
                "client": self.client_2.id,
                "payment_date": date.today().isoformat(),
                "paid_by": "Intento inválido",
                "concept_type": PaymentReceptionLog.ConceptType.HONORARIOS,
                "concept_other": "",
                "payment_method": PaymentReceptionLog.PaymentMethod.TRANSFERENCIA,
                "third_party_check_name": "",
                "observation": "x",
            },
        )

        self.assertEqual(response_edit.status_code, 403)
        self.assertEqual(response_archive.status_code, 403)
        self.assertEqual(response_create_other.status_code, 403)

    def test_admin_can_create_update_archive_unarchive_with_audit(self):
        self.client.force_login(self.admin)
        base_count = AuditLog.objects.filter(entity="paymentreceptionlog").count()

        create_response = self.client.post(
            reverse("app-payment-log-create"),
            data={
                "client": self.client_1.id,
                "payment_date": date.today().isoformat(),
                "paid_by": "Pago Admin",
                "concept_type": PaymentReceptionLog.ConceptType.ANTICIPOS,
                "concept_other": "",
                "payment_method": PaymentReceptionLog.PaymentMethod.OTRO,
                "third_party_check_name": "",
                "observation": "Creado por test",
            },
        )
        self.assertEqual(create_response.status_code, 302)

        item = PaymentReceptionLog.objects.filter(paid_by="Pago Admin").latest("id")

        update_response = self.client.post(
            reverse("app-payment-log-edit", args=[item.id]),
            data={
                "client": self.client_1.id,
                "payment_date": date.today().isoformat(),
                "paid_by": "Pago Admin Editado",
                "concept_type": PaymentReceptionLog.ConceptType.ANTICIPOS,
                "concept_other": "",
                "payment_method": PaymentReceptionLog.PaymentMethod.TRANSFERENCIA,
                "third_party_check_name": "",
                "observation": "Editado por test",
            },
        )
        self.assertEqual(update_response.status_code, 302)

        archive_response = self.client.post(reverse("app-payment-log-archive", args=[item.id]))
        self.assertEqual(archive_response.status_code, 302)
        item.refresh_from_db()
        self.assertTrue(item.is_archived)

        unarchive_response = self.client.post(reverse("app-payment-log-unarchive", args=[item.id]))
        self.assertEqual(unarchive_response.status_code, 302)
        item.refresh_from_db()
        self.assertFalse(item.is_archived)

        new_count = AuditLog.objects.filter(entity="paymentreceptionlog").count()
        self.assertEqual(new_count, base_count + 4)
        actions = list(
            AuditLog.objects.filter(entity="paymentreceptionlog").order_by("-id").values_list("action", flat=True)[:4]
        )
        self.assertEqual(actions, ["unarchive_ui", "archive_ui", "update_ui", "create_ui"])

    def test_list_filters_scope_date_concept_and_method(self):
        self.client.force_login(self.admin)
        self.log_2.is_archived = True
        self.log_2.save(update_fields=["is_archived", "updated_at"])

        response_scope_active = self.client.get(reverse("app-payment-log-list"), {"scope": "active"})
        html_scope_active = response_scope_active.content.decode("utf-8")
        self.assertIn("Pago 1", html_scope_active)
        self.assertNotIn("Pago 2", html_scope_active)

        response_scope_archived = self.client.get(reverse("app-payment-log-list"), {"scope": "archived"})
        html_scope_archived = response_scope_archived.content.decode("utf-8")
        self.assertNotIn("Pago 1", html_scope_archived)
        self.assertIn("Pago 2", html_scope_archived)

        response_date = self.client.get(
            reverse("app-payment-log-list"),
            {
                "scope": "all",
                "date_from": self.log_1.payment_date.isoformat(),
                "date_to": self.log_1.payment_date.isoformat(),
            },
        )
        html_date = response_date.content.decode("utf-8")
        self.assertIn("Pago 1", html_date)
        self.assertNotIn("Pago 2", html_date)

        response_concept = self.client.get(
            reverse("app-payment-log-list"),
            {"scope": "all", "concept_type": PaymentReceptionLog.ConceptType.IMPUESTOS},
        )
        html_concept = response_concept.content.decode("utf-8")
        self.assertNotIn("Pago 1", html_concept)
        self.assertIn("Pago 2", html_concept)

        response_method = self.client.get(
            reverse("app-payment-log-list"),
            {"scope": "all", "payment_method": PaymentReceptionLog.PaymentMethod.TRANSFERENCIA},
        )
        html_method = response_method.content.decode("utf-8")
        self.assertIn("Pago 1", html_method)
        self.assertNotIn("Pago 2", html_method)
