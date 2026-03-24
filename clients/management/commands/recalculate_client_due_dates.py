from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import User
from auditing.services import get_instance_snapshot, log_model_event
from clients.models import Client
from operations.services import dnit_due_date_for_month


class Command(BaseCommand):
    help = (
        "Recalcula due_date de clientes según terminación de RUC base "
        "con el calendario DNIT oficial."
    )

    def add_arguments(self, parser):
        parser.add_argument("--year", type=int, help="Año objetivo para el vencimiento.")
        parser.add_argument("--month", type=int, help="Mes objetivo para el vencimiento (1-12).")
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
        year = options.get("year")
        month = options.get("month")
        limit = int(options.get("limit") or 0)
        apply_changes = options.get("apply", False)
        actor_username = (options.get("actor") or "").strip()

        if not year or not month:
            raise CommandError("Debés indicar --year y --month.")
        if month < 1 or month > 12:
            raise CommandError("--month debe estar entre 1 y 12.")

        actor = None
        if actor_username:
            actor = User.objects.filter(username=actor_username).first()
            if actor is None:
                raise CommandError(f"No existe usuario actor: {actor_username}")

        candidates = []
        ambiguous = []

        for client in Client.objects.order_by("id").iterator():
            calculated = dnit_due_date_for_month(client.ruc_base or client.ruc, year, month)
            if calculated is None:
                ambiguous.append(client)
                continue
            if client.due_date != calculated:
                candidates.append((client, calculated))

        total_candidates = len(candidates)
        if limit > 0:
            candidates = candidates[:limit]

        self.stdout.write(self.style.NOTICE("Recalcular vencimientos en clientes"))
        self.stdout.write(f"- Mes objetivo: {month:02d}/{year}")
        self.stdout.write(f"- Clientes evaluados: {Client.objects.count()}")
        self.stdout.write(f"- Candidatos con cambio: {total_candidates}")
        self.stdout.write(f"- Ambiguos (sin cálculo seguro): {len(ambiguous)}")
        self.stdout.write(f"- Alcance de esta corrida: {len(candidates)}")

        if candidates:
            self.stdout.write("Muestra de cambios (máx. 20):")
            for client, new_due_date in candidates[:20]:
                self.stdout.write(
                    f"  Client #{client.id} | {client.name} | "
                    f"actual={client.due_date or '-'} -> nuevo={new_due_date}"
                )

        if ambiguous:
            self.stdout.write("Muestra de ambiguos (máx. 20):")
            for client in ambiguous[:20]:
                self.stdout.write(
                    f"  Client #{client.id} | {client.name} | ruc={client.ruc or '-'} | ruc_base={client.ruc_base or '-'}"
                )

        if not apply_changes:
            self.stdout.write(
                self.style.WARNING("Dry-run: no se aplicaron cambios. Usá --apply para persistir.")
            )
            return

        updated = 0
        with transaction.atomic():
            for client, new_due_date in candidates:
                before = get_instance_snapshot(client)
                client.due_date = new_due_date
                client.save(update_fields=["due_date", "updated_at"])
                log_model_event(
                    actor=actor,
                    action="recalculate_client_due_date_command",
                    instance=client,
                    before_data=before,
                    after_data=get_instance_snapshot(client),
                    metadata={
                        "command": "recalculate_client_due_dates",
                        "year": year,
                        "month": month,
                    },
                )
                updated += 1

        self.stdout.write(self.style.SUCCESS(f"Actualizados: {updated}"))
        self.stdout.write(self.style.SUCCESS(f"Ambiguos sin tocar: {len(ambiguous)}"))
