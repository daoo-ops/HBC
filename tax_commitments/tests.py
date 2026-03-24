from datetime import timedelta
from decimal import Decimal
import uuid

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from clients.models import Client
from tax_commitments.models import TaxCommitment


class TaxCommitmentWebTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="admin_tax", password="secret123", role=User.Role.ADMIN)
        self.func_1 = User.objects.create_user(username="func_tax_1", password="secret123", role=User.Role.FUNCIONARIO)
        self.func_2 = User.objects.create_user(username="func_tax_2", password="secret123", role=User.Role.FUNCIONARIO)
        self.client_1 = Client.objects.create(
            name="Cliente Tax 1",
            ruc="80011111",
            zone=Client.Zone.SANTA_RITA,
            status=Client.Status.ACTIVE,
            responsible=self.func_1,
        )
        self.client_2 = Client.objects.create(
            name="Cliente Tax 2",
            ruc="80022222",
            zone=Client.Zone.SANTA_RITA,
            status=Client.Status.ACTIVE,
            responsible=self.func_2,
        )

    def _create_pending_item(self, client, due_date):
        return TaxCommitment.objects.create(
            client=client,
            commitment_type=TaxCommitment.CommitmentType.IRE,
            due_date=due_date,
            amount=Decimal("1000000"),
            currency=TaxCommitment.Currency.PYG,
            status=TaxCommitment.Status.PENDING,
            source=TaxCommitment.Source.MANUAL,
            created_by=self.admin,
        )

    def test_funcionario_can_create_for_assigned_client(self):
        self.client.force_login(self.func_1)
        response = self.client.post(
            reverse("app-tax-commitment-create"),
            data={
                "client": str(self.client_1.id),
                "commitment_type": TaxCommitment.CommitmentType.IRE,
                "reference_number": "REF-123",
                "period_reference": "03/2026",
                "due_date": "2026-03-21",
                "amount": "500000",
                "currency": TaxCommitment.Currency.PYG,
                "notes": "Carga manual",
                "installments_count": "1",
            },
        )
        self.assertEqual(response.status_code, 302)
        item = TaxCommitment.objects.get(reference_number="REF-123")
        self.assertEqual(item.client_id, self.client_1.id)
        self.assertEqual(item.created_by_id, self.func_1.id)
        self.assertEqual(item.status, TaxCommitment.Status.PENDING)

    def test_funcionario_cannot_create_for_unassigned_client(self):
        self.client.force_login(self.func_1)
        response = self.client.post(
            reverse("app-tax-commitment-create"),
            data={
                "client": str(self.client_2.id),
                "commitment_type": TaxCommitment.CommitmentType.IRE,
                "due_date": "2026-03-21",
                "amount": "500000",
                "currency": TaxCommitment.Currency.PYG,
                "installments_count": "1",
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(TaxCommitment.objects.count(), 0)

    def test_create_installments_distributes_last_difference(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("app-tax-commitment-create"),
            data={
                "client": str(self.client_1.id),
                "commitment_type": TaxCommitment.CommitmentType.ANTICIPO,
                "installment_mode": TaxCommitment.InstallmentMode.AUTO,
                "reference_number": "ANT-001",
                "period_reference": "Ejercicio 2026",
                "due_date": "2026-03-21",
                "amount": "1000000",
                "currency": TaxCommitment.Currency.PYG,
                "installments_count": "3",
                "notes": "Plan en cuotas",
            },
        )
        self.assertEqual(response.status_code, 302)
        items = list(TaxCommitment.objects.filter(reference_number="ANT-001").order_by("installment_number"))
        self.assertEqual(len(items), 3)
        self.assertTrue(all(item.installment_group_id for item in items))
        self.assertEqual(items[0].installment_group_id, items[1].installment_group_id)
        self.assertEqual(items[1].installment_group_id, items[2].installment_group_id)
        self.assertTrue(all(item.installment_mode == TaxCommitment.InstallmentMode.AUTO for item in items))
        self.assertEqual([item.amount for item in items], [Decimal("333333"), Decimal("333333"), Decimal("333334")])
        self.assertEqual([item.installment_number for item in items], [1, 2, 3])
        self.assertEqual([item.installment_total for item in items], [3, 3, 3])
        self.assertEqual([item.due_date.isoformat() for item in items], ["2026-03-21", "2026-04-21", "2026-05-21"])

    def test_create_facilidad_manual_installments_with_custom_amounts(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("app-tax-commitment-create"),
            data={
                "client": str(self.client_1.id),
                "commitment_type": TaxCommitment.CommitmentType.FACILIDAD,
                "installment_mode": TaxCommitment.InstallmentMode.MANUAL,
                "reference_number": "FAC-100",
                "period_reference": "Plan especial",
                "due_date": "2026-03-12",
                "amount": "1000000",
                "currency": TaxCommitment.Currency.PYG,
                "installments_count": "3",
                "manual_amounts": ["250000", "300000", "450000"],
                "notes": "Facilidad en montos variables",
            },
        )
        self.assertEqual(response.status_code, 302)
        items = list(TaxCommitment.objects.filter(reference_number="FAC-100").order_by("installment_number"))
        self.assertEqual(len(items), 3)
        self.assertEqual([item.amount for item in items], [Decimal("250000"), Decimal("300000"), Decimal("450000")])
        self.assertTrue(all(item.installment_mode == TaxCommitment.InstallmentMode.MANUAL for item in items))
        self.assertEqual([item.due_date.isoformat() for item in items], ["2026-03-12", "2026-04-12", "2026-05-12"])

    def test_create_auto_installments_with_custom_due_dates(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("app-tax-commitment-create"),
            data={
                "client": str(self.client_1.id),
                "commitment_type": TaxCommitment.CommitmentType.ANTICIPO,
                "installment_mode": TaxCommitment.InstallmentMode.AUTO,
                "reference_number": "AUTO-DUE-1",
                "period_reference": "Ejercicio 2026",
                "due_date": "2026-03-21",
                "amount": "1000000",
                "currency": TaxCommitment.Currency.PYG,
                "installments_count": "3",
                "customize_installment_dates": "on",
                "manual_due_dates": ["2026-03-21", "2026-04-30", "2026-06-05"],
            },
        )
        self.assertEqual(response.status_code, 302)
        items = list(TaxCommitment.objects.filter(reference_number="AUTO-DUE-1").order_by("installment_number"))
        self.assertEqual(len(items), 3)
        self.assertEqual([item.due_date.isoformat() for item in items], ["2026-03-21", "2026-04-30", "2026-06-05"])
        self.assertEqual([item.amount for item in items], [Decimal("333333"), Decimal("333333"), Decimal("333334")])

    def test_create_manual_installments_with_custom_due_dates(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("app-tax-commitment-create"),
            data={
                "client": str(self.client_1.id),
                "commitment_type": TaxCommitment.CommitmentType.FACILIDAD,
                "installment_mode": TaxCommitment.InstallmentMode.MANUAL,
                "reference_number": "MANUAL-DUE-1",
                "period_reference": "Plan especial",
                "due_date": "2026-03-12",
                "amount": "1000000",
                "currency": TaxCommitment.Currency.PYG,
                "installments_count": "3",
                "manual_amounts": ["250000", "300000", "450000"],
                "customize_installment_dates": "on",
                "manual_due_dates": ["2026-03-12", "2026-05-02", "2026-07-17"],
            },
        )
        self.assertEqual(response.status_code, 302)
        items = list(TaxCommitment.objects.filter(reference_number="MANUAL-DUE-1").order_by("installment_number"))
        self.assertEqual(len(items), 3)
        self.assertEqual([item.due_date.isoformat() for item in items], ["2026-03-12", "2026-05-02", "2026-07-17"])
        self.assertEqual([item.amount for item in items], [Decimal("250000"), Decimal("300000"), Decimal("450000")])

    def test_create_installments_requires_exact_due_dates_when_customized(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("app-tax-commitment-create"),
            data={
                "client": str(self.client_1.id),
                "commitment_type": TaxCommitment.CommitmentType.ANTICIPO,
                "installment_mode": TaxCommitment.InstallmentMode.AUTO,
                "reference_number": "AUTO-DUE-ERR",
                "due_date": "2026-03-21",
                "amount": "900000",
                "currency": TaxCommitment.Currency.PYG,
                "installments_count": "3",
                "customize_installment_dates": "on",
                "manual_due_dates": ["2026-03-21", "2026-04-21"],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "debe completar exactamente 3 fechas de cuota")
        self.assertEqual(TaxCommitment.objects.filter(reference_number="AUTO-DUE-ERR").count(), 0)

    def test_create_single_installment_creates_one_row(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("app-tax-commitment-create"),
            data={
                "client": str(self.client_1.id),
                "commitment_type": TaxCommitment.CommitmentType.IDU,
                "installment_mode": TaxCommitment.InstallmentMode.AUTO,
                "reference_number": "IDU-1",
                "due_date": "2026-03-30",
                "amount": "345000",
                "currency": TaxCommitment.Currency.PYG,
                "installments_count": "1",
            },
        )
        self.assertEqual(response.status_code, 302)
        items = list(TaxCommitment.objects.filter(reference_number="IDU-1"))
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].amount, Decimal("345000"))
        self.assertEqual(items[0].installment_number, None)
        self.assertEqual(items[0].installment_total, None)

    def test_quick_actions_notify_paid_archive(self):
        item = self._create_pending_item(self.client_1, due_date=timezone.localdate() + timedelta(days=1))
        self.client.force_login(self.admin)

        response = self.client.post(reverse("app-tax-commitment-notify", args=[item.id]), data={"next": "/app/tax-commitments/"})
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.status, TaxCommitment.Status.NOTIFIED)
        self.assertEqual(item.notified_by_id, self.admin.id)
        self.assertIsNotNone(item.notified_at)

        response = self.client.post(
            reverse("app-tax-commitment-mark-paid", args=[item.id]),
            data={"next": "/app/tax-commitments/"},
        )
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.status, TaxCommitment.Status.PAID)
        self.assertEqual(item.paid_by_id, self.admin.id)
        self.assertIsNotNone(item.paid_at)

        response = self.client.post(
            reverse("app-tax-commitment-archive", args=[item.id]),
            data={"next": "/app/tax-commitments/"},
        )
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.status, TaxCommitment.Status.ARCHIVED)

    def test_funcionario_cannot_access_unassigned_commitment_urls(self):
        item = self._create_pending_item(self.client_2, due_date=timezone.localdate() + timedelta(days=5))
        self.client.force_login(self.func_1)

        edit_response = self.client.get(reverse("app-tax-commitment-edit", args=[item.id]))
        notify_response = self.client.post(reverse("app-tax-commitment-notify", args=[item.id]))
        paid_response = self.client.post(reverse("app-tax-commitment-mark-paid", args=[item.id]))

        self.assertEqual(edit_response.status_code, 403)
        self.assertEqual(notify_response.status_code, 403)
        self.assertEqual(paid_response.status_code, 403)

    def test_funcionario_list_only_shows_assigned_commitments(self):
        self._create_pending_item(self.client_1, due_date=timezone.localdate() + timedelta(days=2))
        self._create_pending_item(self.client_2, due_date=timezone.localdate() + timedelta(days=2))
        self.client.force_login(self.func_1)

        response = self.client.get(reverse("app-tax-commitment-list"))
        self.assertEqual(response.status_code, 200)
        items = list(response.context["items"])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].client_id, self.client_1.id)

    def test_list_defaults_to_activos_grouped_filter(self):
        today = timezone.localdate()
        pending = self._create_pending_item(self.client_1, due_date=today + timedelta(days=2))
        paid = TaxCommitment.objects.create(
            client=self.client_1,
            commitment_type=TaxCommitment.CommitmentType.IRE,
            due_date=today + timedelta(days=1),
            amount=Decimal("1000000"),
            currency=TaxCommitment.Currency.PYG,
            status=TaxCommitment.Status.PAID,
            source=TaxCommitment.Source.MANUAL,
            created_by=self.admin,
        )
        archived = TaxCommitment.objects.create(
            client=self.client_1,
            commitment_type=TaxCommitment.CommitmentType.IRE,
            due_date=today + timedelta(days=1),
            amount=Decimal("1000000"),
            currency=TaxCommitment.Currency.PYG,
            status=TaxCommitment.Status.ARCHIVED,
            source=TaxCommitment.Source.MANUAL,
            created_by=self.admin,
        )
        self.client.force_login(self.admin)
        response = self.client.get(reverse("app-tax-commitment-list"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["status_filter"], "ACTIVOS")
        ids = {item.id for item in response.context["items"]}
        self.assertIn(pending.id, ids)
        self.assertNotIn(paid.id, ids)
        self.assertNotIn(archived.id, ids)

    def test_list_status_filter_all_includes_paid_and_archived(self):
        today = timezone.localdate()
        pending = self._create_pending_item(self.client_1, due_date=today + timedelta(days=2))
        paid = TaxCommitment.objects.create(
            client=self.client_1,
            commitment_type=TaxCommitment.CommitmentType.IRE,
            due_date=today + timedelta(days=1),
            amount=Decimal("1000000"),
            currency=TaxCommitment.Currency.PYG,
            status=TaxCommitment.Status.PAID,
            source=TaxCommitment.Source.MANUAL,
            created_by=self.admin,
        )
        archived = TaxCommitment.objects.create(
            client=self.client_1,
            commitment_type=TaxCommitment.CommitmentType.IRE,
            due_date=today + timedelta(days=1),
            amount=Decimal("1000000"),
            currency=TaxCommitment.Currency.PYG,
            status=TaxCommitment.Status.ARCHIVED,
            source=TaxCommitment.Source.MANUAL,
            created_by=self.admin,
        )
        self.client.force_login(self.admin)
        response = self.client.get(reverse("app-tax-commitment-list"), {"status": "ALL"})
        self.assertEqual(response.status_code, 200)
        ids = {item.id for item in response.context["items"]}
        self.assertIn(pending.id, ids)
        self.assertIn(paid.id, ids)
        self.assertIn(archived.id, ids)

    def test_dashboard_includes_tax_commitment_blocks(self):
        today = timezone.localdate()
        self._create_pending_item(self.client_1, due_date=today)
        self._create_pending_item(self.client_1, due_date=today + timedelta(days=3))
        self._create_pending_item(self.client_1, due_date=today - timedelta(days=2))
        self.client.force_login(self.func_1)

        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Compromisos tributarios")
        self.assertGreaterEqual(response.context["tax_due_today_count"], 1)
        self.assertGreaterEqual(response.context["tax_due_week_count"], 1)
        self.assertGreaterEqual(response.context["tax_overdue_count"], 1)

    def test_tax_commitment_list_groups_installments_in_single_summary_row(self):
        group_id = uuid.uuid4()
        base_due = timezone.localdate() + timedelta(days=2)
        TaxCommitment.objects.create(
            client=self.client_1,
            commitment_type=TaxCommitment.CommitmentType.FACILIDAD,
            reference_number="GRP-001",
            period_reference="Plan 2026",
            installment_group_id=group_id,
            installment_number=1,
            installment_total=3,
            installment_mode=TaxCommitment.InstallmentMode.MANUAL,
            due_date=base_due,
            amount=Decimal("100000"),
            currency=TaxCommitment.Currency.PYG,
            status=TaxCommitment.Status.PAID,
            source=TaxCommitment.Source.MANUAL,
            created_by=self.admin,
        )
        TaxCommitment.objects.create(
            client=self.client_1,
            commitment_type=TaxCommitment.CommitmentType.FACILIDAD,
            reference_number="GRP-001",
            period_reference="Plan 2026",
            installment_group_id=group_id,
            installment_number=2,
            installment_total=3,
            installment_mode=TaxCommitment.InstallmentMode.MANUAL,
            due_date=base_due + timedelta(days=30),
            amount=Decimal("100000"),
            currency=TaxCommitment.Currency.PYG,
            status=TaxCommitment.Status.PENDING,
            source=TaxCommitment.Source.MANUAL,
            created_by=self.admin,
        )
        TaxCommitment.objects.create(
            client=self.client_1,
            commitment_type=TaxCommitment.CommitmentType.FACILIDAD,
            reference_number="GRP-001",
            period_reference="Plan 2026",
            installment_group_id=group_id,
            installment_number=3,
            installment_total=3,
            installment_mode=TaxCommitment.InstallmentMode.MANUAL,
            due_date=base_due + timedelta(days=60),
            amount=Decimal("100000"),
            currency=TaxCommitment.Currency.PYG,
            status=TaxCommitment.Status.PENDING,
            source=TaxCommitment.Source.MANUAL,
            created_by=self.admin,
        )
        TaxCommitment.objects.create(
            client=self.client_1,
            commitment_type=TaxCommitment.CommitmentType.IRE,
            reference_number="SINGLE-1",
            due_date=base_due,
            amount=Decimal("500000"),
            currency=TaxCommitment.Currency.PYG,
            status=TaxCommitment.Status.PENDING,
            source=TaxCommitment.Source.MANUAL,
            created_by=self.admin,
        )
        self.client.force_login(self.admin)
        response = self.client.get(reverse("app-tax-commitment-list"))
        self.assertEqual(response.status_code, 200)
        groups = list(response.context["groups"])
        self.assertEqual(len(groups), 2)
        grouped = next(group for group in groups if group["reference_number"] == "GRP-001")
        self.assertEqual(grouped["cuota_context"], "1/3 pagadas")
        self.assertEqual(grouped["toggle_label"], "3 cuotas")
        self.assertEqual(grouped["total_amount"], Decimal("300000"))
        self.assertEqual(len(grouped["items"]), 3)

    def test_tax_commitment_group_general_status_prefers_overdue(self):
        group_id = uuid.uuid4()
        today = timezone.localdate()
        TaxCommitment.objects.create(
            client=self.client_1,
            commitment_type=TaxCommitment.CommitmentType.ANTICIPO,
            reference_number="GRP-OVD",
            installment_group_id=group_id,
            installment_number=1,
            installment_total=2,
            installment_mode=TaxCommitment.InstallmentMode.AUTO,
            due_date=today - timedelta(days=4),
            amount=Decimal("100000"),
            currency=TaxCommitment.Currency.PYG,
            status=TaxCommitment.Status.PENDING,
            source=TaxCommitment.Source.MANUAL,
            created_by=self.admin,
        )
        TaxCommitment.objects.create(
            client=self.client_1,
            commitment_type=TaxCommitment.CommitmentType.ANTICIPO,
            reference_number="GRP-OVD",
            installment_group_id=group_id,
            installment_number=2,
            installment_total=2,
            installment_mode=TaxCommitment.InstallmentMode.AUTO,
            due_date=today + timedelta(days=5),
            amount=Decimal("100000"),
            currency=TaxCommitment.Currency.PYG,
            status=TaxCommitment.Status.NOTIFIED,
            source=TaxCommitment.Source.MANUAL,
            created_by=self.admin,
        )
        self.client.force_login(self.admin)
        response = self.client.get(reverse("app-tax-commitment-list"))
        self.assertEqual(response.status_code, 200)
        groups = list(response.context["groups"])
        grouped = next(group for group in groups if group["reference_number"] == "GRP-OVD")
        self.assertEqual(grouped["general_status"], "OVERDUE")
        self.assertEqual(grouped["general_label"], "Vencido")
        self.assertEqual(grouped["general_badge"], "overdue")

    def test_group_row_hides_operational_next_actions_and_single_row_keeps_them(self):
        group_id = uuid.uuid4()
        base_due = timezone.localdate() + timedelta(days=5)
        TaxCommitment.objects.create(
            client=self.client_1,
            commitment_type=TaxCommitment.CommitmentType.FACILIDAD,
            reference_number="GRP-UI-1",
            installment_group_id=group_id,
            installment_number=1,
            installment_total=2,
            installment_mode=TaxCommitment.InstallmentMode.MANUAL,
            due_date=base_due,
            amount=Decimal("100000"),
            currency=TaxCommitment.Currency.PYG,
            status=TaxCommitment.Status.PENDING,
            source=TaxCommitment.Source.MANUAL,
            created_by=self.admin,
        )
        TaxCommitment.objects.create(
            client=self.client_1,
            commitment_type=TaxCommitment.CommitmentType.FACILIDAD,
            reference_number="GRP-UI-1",
            installment_group_id=group_id,
            installment_number=2,
            installment_total=2,
            installment_mode=TaxCommitment.InstallmentMode.MANUAL,
            due_date=base_due + timedelta(days=30),
            amount=Decimal("100000"),
            currency=TaxCommitment.Currency.PYG,
            status=TaxCommitment.Status.PENDING,
            source=TaxCommitment.Source.MANUAL,
            created_by=self.admin,
        )
        single = TaxCommitment.objects.create(
            client=self.client_1,
            commitment_type=TaxCommitment.CommitmentType.IRE,
            reference_number="SINGLE-UI-1",
            due_date=base_due,
            amount=Decimal("500000"),
            currency=TaxCommitment.Currency.PYG,
            status=TaxCommitment.Status.PENDING,
            source=TaxCommitment.Source.MANUAL,
            created_by=self.admin,
        )
        self.client.force_login(self.admin)
        response = self.client.get(reverse("app-tax-commitment-list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Editar grupo")
        self.assertNotContains(response, "Avisar prox.")
        self.assertNotContains(response, "Pagar prox.")
        self.assertContains(response, f'/app/tax-commitments/{single.id}/notify/')

    def test_archive_group_archives_all_paid_installments(self):
        group_id = uuid.uuid4()
        due_date = timezone.localdate() - timedelta(days=1)
        first = TaxCommitment.objects.create(
            client=self.client_1,
            commitment_type=TaxCommitment.CommitmentType.FACILIDAD,
            reference_number="GRP-ARCH-1",
            installment_group_id=group_id,
            installment_number=1,
            installment_total=2,
            installment_mode=TaxCommitment.InstallmentMode.MANUAL,
            due_date=due_date,
            amount=Decimal("100000"),
            currency=TaxCommitment.Currency.PYG,
            status=TaxCommitment.Status.PAID,
            source=TaxCommitment.Source.MANUAL,
            created_by=self.admin,
        )
        second = TaxCommitment.objects.create(
            client=self.client_1,
            commitment_type=TaxCommitment.CommitmentType.FACILIDAD,
            reference_number="GRP-ARCH-1",
            installment_group_id=group_id,
            installment_number=2,
            installment_total=2,
            installment_mode=TaxCommitment.InstallmentMode.MANUAL,
            due_date=due_date + timedelta(days=30),
            amount=Decimal("100000"),
            currency=TaxCommitment.Currency.PYG,
            status=TaxCommitment.Status.PAID,
            source=TaxCommitment.Source.MANUAL,
            created_by=self.admin,
        )
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("app-tax-commitment-archive-group", args=[group_id]),
            data={"next": "/app/tax-commitments/"},
        )
        self.assertEqual(response.status_code, 302)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.status, TaxCommitment.Status.ARCHIVED)
        self.assertEqual(second.status, TaxCommitment.Status.ARCHIVED)
