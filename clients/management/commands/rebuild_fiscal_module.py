from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from clients.models import Client, ClientObligation, Obligation
from clients.services_fiscal_restore import (
    rebuild_client_obligations,
    regenerate_submissions_for_period,
    restore_obligation_catalog,
)
from operations.models import Submission


class Command(BaseCommand):
    help = (
        "Reconstruye el módulo fiscal en 3 pasos: "
        "1) catálogo Obligation, 2) ClientObligation, 3) Submission del período."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Ejecuta cambios reales. Sin este flag corre en dry-run.",
        )
        parser.add_argument(
            "--year",
            type=int,
            help="Año para regeneración de submissions (default: año actual).",
        )
        parser.add_argument(
            "--month",
            type=int,
            help="Mes para regeneración de submissions (1-12, default: mes actual).",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        today = timezone.localdate()
        year = options.get("year") or today.year
        month = options.get("month") or today.month
        if month < 1 or month > 12:
            raise CommandError("El parámetro --month debe estar entre 1 y 12.")

        mode_label = "APPLY" if apply_changes else "DRY-RUN"
        self.stdout.write(self.style.NOTICE(f"=== rebuild_fiscal_module ({mode_label}) ==="))
        self.stdout.write(f"- Período objetivo submissions: {month:02d}/{year}")

        counts_before = {
            "clients": Client.objects.filter(is_deleted=False).count(),
            "obligations": Obligation.objects.count(),
            "client_obligations": ClientObligation.objects.count(),
            "submissions": Submission.objects.count(),
        }

        with transaction.atomic():
            catalog_stats, obligation_by_code = restore_obligation_catalog()
            client_obligation_stats = rebuild_client_obligations(
                Client.objects.filter(is_deleted=False),
                obligation_by_code=obligation_by_code,
            )
            submission_stats = regenerate_submissions_for_period(year=year, month=month)

            if not apply_changes:
                transaction.set_rollback(True)

        counts_after = {
            "clients": Client.objects.filter(is_deleted=False).count(),
            "obligations": Obligation.objects.count(),
            "client_obligations": ClientObligation.objects.count(),
            "submissions": Submission.objects.count(),
        }

        self.stdout.write("\n[1] Catálogo Obligation")
        self.stdout.write(f"- creados: {catalog_stats['created']}")
        self.stdout.write(f"- actualizados: {catalog_stats['updated']}")
        self.stdout.write(f"- total esperado tras paso: {catalog_stats['total_after']}")

        self.stdout.write("\n[2] Reconstrucción ClientObligation")
        self.stdout.write(f"- clientes evaluados: {client_obligation_stats['clients_evaluated']}")
        self.stdout.write(f"- con presentation_type: {client_obligation_stats['clients_with_presentation_type']}")
        self.stdout.write(f"- sin presentation_type: {client_obligation_stats['clients_without_presentation_type']}")
        self.stdout.write(f"- links creados: {client_obligation_stats['links_created']}")
        self.stdout.write(f"- links actualizados: {client_obligation_stats['links_updated']}")
        self.stdout.write(
            f"- placeholders revisión creados: {client_obligation_stats['review_placeholders_created']}"
        )
        self.stdout.write(
            f"- placeholders revisión actualizados: {client_obligation_stats['review_placeholders_updated']}"
        )
        self.stdout.write(f"- clientes ambiguos: {client_obligation_stats['ambiguous_clients']}")
        self.stdout.write(f"- clientes sin mapeo: {client_obligation_stats['unmapped_clients']}")

        self.stdout.write(f"\n[3] Regeneración Submission ({month:02d}/{year})")
        self.stdout.write(f"- submissions creadas: {submission_stats['created']}")
        self.stdout.write(f"- submissions omitidas por falta de datos seguros: {submission_stats['skipped']}")
        self.stdout.write(f"- total esperado tras paso: {submission_stats['total_after']}")

        self.stdout.write("\nConteos base")
        self.stdout.write(
            f"- antes: obligations={counts_before['obligations']}, "
            f"client_obligations={counts_before['client_obligations']}, "
            f"submissions={counts_before['submissions']}"
        )
        self.stdout.write(
            f"- ahora: obligations={counts_after['obligations']}, "
            f"client_obligations={counts_after['client_obligations']}, "
            f"submissions={counts_after['submissions']}"
        )

        if apply_changes:
            self.stdout.write(self.style.SUCCESS("Reconstrucción fiscal completada."))
        else:
            self.stdout.write(
                self.style.SUCCESS("Dry-run completado. No se persistieron cambios (usa --apply para ejecutar).")
            )

