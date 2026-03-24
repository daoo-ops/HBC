from django.core.management.base import BaseCommand
from django.db import transaction

from banks.models import BankRequest
from billing.models import Charge, Contract
from clients.models import Client, ClientNote, ClientObligation, ClientResponsibilityHistory
from operations.models import Deadline, PendingItem, Submission


class Command(BaseCommand):
    help = "Limpia datos operativos vinculados a clientes y luego elimina clientes."

    MODEL_SEQUENCE = [
        ("banks.BankRequest", BankRequest),
        ("operations.Submission", Submission),
        ("operations.PendingItem", PendingItem),
        ("operations.Deadline", Deadline),
        ("billing.Charge", Charge),
        ("billing.Contract", Contract),
        ("clients.ClientObligation", ClientObligation),
        ("clients.ClientNote", ClientNote),
        ("clients.ClientResponsibilityHistory", ClientResponsibilityHistory),
        ("clients.Client", Client),
    ]

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Ejecuta el borrado real. Sin este flag, corre en modo dry-run.",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        mode_label = "APPLY" if apply_changes else "DRY-RUN"
        self.stdout.write(self.style.NOTICE(f"=== reset_clients_data ({mode_label}) ==="))

        summaries = []

        with transaction.atomic():
            for model_label, model in self.MODEL_SEQUENCE:
                detected = model.objects.count()
                deleted = 0
                if apply_changes and detected:
                    model.objects.all().delete()
                    deleted = detected - model.objects.count()

                summaries.append(
                    {
                        "model": model_label,
                        "detected": detected,
                        "deleted": deleted,
                    }
                )

            if not apply_changes:
                transaction.set_rollback(True)

        for row in summaries:
            self.stdout.write(
                f"- {row['model']}: detectados={row['detected']} | borrados={row['deleted']}"
            )

        if apply_changes:
            self.stdout.write(self.style.SUCCESS("Limpieza completada."))
        else:
            self.stdout.write(
                self.style.SUCCESS("Dry-run completado. No se borraron registros (usa --apply para ejecutar).")
            )
