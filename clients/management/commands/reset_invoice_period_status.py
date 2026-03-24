from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from accounts.models import User
from auditing.services import get_instance_snapshot, log_model_event
from clients.models import Client


class Command(BaseCommand):
    help = (
        "Reinicia el estado mensual de facturas del período. "
        "Clientes en RECEIVED de meses anteriores vuelven a PENDING."
    )

    def add_arguments(self, parser):
        parser.add_argument("--year", type=int, help="Año de referencia (default: mes actual).")
        parser.add_argument("--month", type=int, help="Mes de referencia (default: mes actual).")
        parser.add_argument("--limit", type=int, default=0, help="Límite de clientes a actualizar (0 = sin límite).")
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Aplica cambios. Sin esta opción, solo ejecuta dry-run.",
        )
        parser.add_argument(
            "--actor",
            default="",
            help="Username para auditoría (opcional).",
        )

    def handle(self, *args, **options):
        today = timezone.localdate()
        year = options.get("year") or today.year
        month = options.get("month") or today.month
        limit = int(options.get("limit") or 0)
        apply_changes = options.get("apply", False)
        actor_username = (options.get("actor") or "").strip()

        if month < 1 or month > 12:
            raise CommandError("--month debe estar entre 1 y 12.")

        actor = None
        if actor_username:
            actor = User.objects.filter(username=actor_username).first()
            if actor is None:
                raise CommandError(f"No existe usuario actor: {actor_username}")

        reference_first_day = date(year, month, 1)
        now = timezone.now()

        candidates = []
        received_clients = Client.objects.filter(invoice_period_status=Client.InvoicePeriodStatus.RECEIVED).order_by("id")

        for client in received_clients.iterator():
            updated_at = client.invoice_period_status_updated_at
            is_stale = updated_at is None or updated_at.date() < reference_first_day
            if is_stale:
                candidates.append(client)

        total_candidates = len(candidates)
        if limit > 0:
            candidates = candidates[:limit]

        self.stdout.write(self.style.NOTICE("Reset mensual de facturas del período"))
        self.stdout.write(f"- Referencia: {month:02d}/{year}")
        self.stdout.write(f"- Clientes RECEIVED evaluados: {received_clients.count()}")
        self.stdout.write(f"- Candidatos a reset: {total_candidates}")
        self.stdout.write(f"- Alcance de esta corrida: {len(candidates)}")

        if candidates:
            self.stdout.write("Muestra de reseteo (máx. 20):")
            for client in candidates[:20]:
                self.stdout.write(
                    f"  Client #{client.id} | {client.name} | "
                    f"updated_at={client.invoice_period_status_updated_at or '-'}"
                )

        if not apply_changes:
            self.stdout.write(
                self.style.WARNING("Dry-run: no se aplicaron cambios. Usá --apply para persistir.")
            )
            return

        reset_count = 0
        with transaction.atomic():
            for client in candidates:
                before = get_instance_snapshot(client)
                client.invoice_period_status = Client.InvoicePeriodStatus.PENDING
                client.invoice_period_status_updated_by = None
                client.invoice_period_status_updated_at = now
                client.save(
                    update_fields=[
                        "invoice_period_status",
                        "invoice_period_status_updated_by",
                        "invoice_period_status_updated_at",
                        "updated_at",
                    ]
                )
                log_model_event(
                    actor=actor,
                    action="reset_invoice_period_status_command",
                    instance=client,
                    before_data=before,
                    after_data=get_instance_snapshot(client),
                    metadata={
                        "command": "reset_invoice_period_status",
                        "reference_year": year,
                        "reference_month": month,
                    },
                )
                reset_count += 1

        self.stdout.write(self.style.SUCCESS(f"Clientes reseteados: {reset_count}"))
