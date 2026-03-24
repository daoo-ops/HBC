from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from auditing.services import get_instance_snapshot, log_model_event
from operations.models import Submission
from operations.services import dnit_due_date_for_month


class Command(BaseCommand):
    help = (
        "Recalcula vencimientos automáticos de obligaciones (Submission) "
        "según terminación de RUC base y período fiscal."
    )

    def add_arguments(self, parser):
        parser.add_argument("--year", type=int, help="Filtrar por año de período.")
        parser.add_argument("--month", type=int, help="Filtrar por mes de período (1-12).")
        parser.add_argument(
            "--include-submitted",
            action="store_true",
            help="Incluir obligaciones ya presentadas (SUBMITTED).",
        )
        parser.add_argument(
            "--include-archived",
            action="store_true",
            help="Incluir obligaciones archivadas.",
        )
        parser.add_argument(
            "--include-manual",
            action="store_true",
            help="Incluir obligaciones creadas manualmente (created_by no nulo).",
        )
        parser.add_argument(
            "--mark-ambiguous-review",
            action="store_true",
            help="Marcar needs_manual_review=True en casos ambiguos.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Limitar cantidad de registros actualizados (0 = sin límite).",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Aplica cambios. Sin esta opción, solo ejecuta dry-run.",
        )

    def handle(self, *args, **options):
        year = options.get("year")
        month = options.get("month")
        include_submitted = options.get("include_submitted", False)
        include_archived = options.get("include_archived", False)
        include_manual = options.get("include_manual", False)
        mark_ambiguous_review = options.get("mark_ambiguous_review", False)
        limit = int(options.get("limit") or 0)
        apply_changes = options.get("apply", False)

        queryset = Submission.objects.select_related("client", "obligation").filter(
            obligation__isnull=False,
            obligation__uses_ruc_calendar=True,
            period_kind=Submission.PeriodKind.MONTHLY,
            period_year__isnull=False,
            period_month__isnull=False,
            needs_manual_review=False,
        )
        if year:
            queryset = queryset.filter(period_year=year)
        if month:
            queryset = queryset.filter(period_month=month)
        if not include_submitted:
            queryset = queryset.exclude(status=Submission.Status.SUBMITTED)
        if not include_archived:
            queryset = queryset.filter(is_archived=False)
        if not include_manual:
            queryset = queryset.filter(created_by__isnull=True)

        mismatched = []
        ambiguous = []

        for item in queryset.iterator():
            expected_due = dnit_due_date_for_month(
                item.client.ruc_base or item.client.ruc,
                item.period_year,
                item.period_month,
            )
            if expected_due is None:
                ambiguous.append(item)
                continue
            if item.due_date != expected_due:
                mismatched.append((item, expected_due))

        total_mismatched = len(mismatched)
        total_ambiguous = len(ambiguous)
        if limit > 0:
            mismatched = mismatched[:limit]

        self.stdout.write(self.style.NOTICE("Auditoría de vencimientos automáticos"))
        self.stdout.write(f"- Candidatos con vencimiento distinto: {total_mismatched}")
        self.stdout.write(f"- Casos ambiguos (sin cálculo seguro): {total_ambiguous}")
        self.stdout.write(f"- Alcance a procesar en esta corrida: {len(mismatched)}")

        if total_mismatched:
            self.stdout.write("Muestra de cambios (máx. 20):")
            for item, expected in mismatched[:20]:
                self.stdout.write(
                    f"  Submission #{item.id} | cliente={item.client_id} | "
                    f"periodo={item.period_month:02d}/{item.period_year} | "
                    f"actual={item.due_date} -> esperado={expected} | status={item.status} | archived={item.is_archived}"
                )

        if total_ambiguous:
            self.stdout.write("Muestra de ambiguos (máx. 20):")
            for item in ambiguous[:20]:
                self.stdout.write(
                    f"  Submission #{item.id} | cliente={item.client_id} | "
                    f"periodo={item.period_month:02d}/{item.period_year} | ruc={item.client.ruc or '-'}"
                )

        if not apply_changes:
            self.stdout.write(
                self.style.WARNING("Dry-run: no se aplicaron cambios. Usá --apply para persistir.")
            )
            return

        today = timezone.localdate()
        updated = 0
        reviewed = 0
        with transaction.atomic():
            for item, expected in mismatched:
                before = get_instance_snapshot(item)
                item.due_date = expected
                if item.status in {Submission.Status.PENDING, Submission.Status.LATE}:
                    item.status = Submission.Status.LATE if expected < today else Submission.Status.PENDING
                item.save(update_fields=["due_date", "status", "updated_at"])
                log_model_event(
                    actor=None,
                    action="recalculate_due_date_command",
                    instance=item,
                    before_data=before,
                    after_data=get_instance_snapshot(item),
                    metadata={
                        "command": "recalculate_submission_due_dates",
                        "reason": "sync_with_ruc_calendar",
                    },
                )
                updated += 1

            if mark_ambiguous_review:
                for item in ambiguous:
                    before = get_instance_snapshot(item)
                    item.needs_manual_review = True
                    item.save(update_fields=["needs_manual_review", "updated_at"])
                    log_model_event(
                        actor=None,
                        action="mark_manual_review_command",
                        instance=item,
                        before_data=before,
                        after_data=get_instance_snapshot(item),
                        metadata={
                            "command": "recalculate_submission_due_dates",
                            "reason": "ambiguous_due_date",
                        },
                    )
                    reviewed += 1

        self.stdout.write(self.style.SUCCESS(f"Actualizados: {updated}"))
        if mark_ambiguous_review:
            self.stdout.write(self.style.SUCCESS(f"Marcados para revisión manual: {reviewed}"))
