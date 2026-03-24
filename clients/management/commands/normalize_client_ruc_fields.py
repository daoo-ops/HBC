from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import User
from auditing.services import get_instance_snapshot, log_model_event
from clients.models import Client
from clients.utils import calculate_ruc_dv_from_base, extract_ruc_digit, normalize_ruc, normalize_ruc_base


class Command(BaseCommand):
    help = (
        "Normaliza campos RUC/DV en clientes. "
        "Convierte ruc 'base-dv' a ruc='base' + ruc_dv='dv'."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--skip-calculate-missing-dv",
            action="store_true",
            help="No intenta autocalcular DV cuando falta.",
        )
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
        calculate_missing_dv = not options.get("skip_calculate_missing_dv", False)
        apply_changes = options.get("apply", False)
        actor_username = (options.get("actor") or "").strip()

        actor = None
        if actor_username:
            actor = User.objects.filter(username=actor_username).first()
            if actor is None:
                raise CommandError(f"No existe usuario actor: {actor_username}")

        candidates = []
        conflicts = []
        unchanged = 0
        calculated_dv_count = 0
        split_hyphen_count = 0

        for client in Client.objects.order_by("id").iterator():
            current_ruc = normalize_ruc(client.ruc or "")
            current_base = normalize_ruc_base(current_ruc)
            detected_dv = extract_ruc_digit(current_ruc)
            current_dv = normalize_ruc(client.ruc_dv or "").replace("-", "")

            if not current_ruc and not current_dv:
                unchanged += 1
                continue

            if detected_dv and current_dv and detected_dv != current_dv:
                conflicts.append(
                    {
                        "id": client.id,
                        "name": client.name,
                        "ruc": client.ruc,
                        "ruc_dv": client.ruc_dv,
                        "detected_dv": detected_dv,
                    }
                )
                continue

            new_ruc = current_base if current_base else current_ruc
            new_dv = current_dv
            change_reason = None

            if detected_dv:
                new_dv = detected_dv
                if current_ruc != new_ruc:
                    split_hyphen_count += 1
                    change_reason = "split_hyphenated_ruc"
            elif current_base and not current_dv and calculate_missing_dv:
                guessed = calculate_ruc_dv_from_base(current_base)
                if guessed:
                    new_dv = guessed
                    calculated_dv_count += 1
                    change_reason = "calculated_missing_dv"

            if client.ruc != new_ruc or (client.ruc_dv or "") != new_dv:
                candidates.append((client, new_ruc, new_dv, change_reason or "normalize_ruc_fields"))
            else:
                unchanged += 1

        self.stdout.write(self.style.NOTICE("Normalización de RUC/DV en clientes"))
        self.stdout.write(f"- Clientes evaluados: {Client.objects.count()}")
        self.stdout.write(f"- Cambios candidatos: {len(candidates)}")
        self.stdout.write(f"- Conflictos detectados: {len(conflicts)}")
        self.stdout.write(f"- Sin cambios: {unchanged}")
        self.stdout.write(f"- Candidatos por separación de guión: {split_hyphen_count}")
        self.stdout.write(f"- Candidatos por DV calculado: {calculated_dv_count}")

        if conflicts:
            self.stdout.write("Muestra de conflictos (máx. 20):")
            for item in conflicts[:20]:
                self.stdout.write(
                    f"  Client #{item['id']} | {item['name']} | "
                    f"ruc={item['ruc']} | ruc_dv={item['ruc_dv']} | dv_detectado={item['detected_dv']}"
                )

        if candidates:
            self.stdout.write("Muestra de cambios (máx. 20):")
            for client, new_ruc, new_dv, reason in candidates[:20]:
                self.stdout.write(
                    f"  Client #{client.id} | {client.name} | "
                    f"ruc: {client.ruc or '-'} -> {new_ruc or '-'} | "
                    f"dv: {client.ruc_dv or '-'} -> {new_dv or '-'} | {reason}"
                )

        if not apply_changes:
            self.stdout.write(
                self.style.WARNING("Dry-run: no se aplicaron cambios. Usá --apply para persistir.")
            )
            return

        updated = 0
        with transaction.atomic():
            for client, new_ruc, new_dv, reason in candidates:
                before = get_instance_snapshot(client)
                client.ruc = new_ruc
                client.ruc_dv = new_dv
                client.save(update_fields=["ruc", "ruc_dv", "ruc_base", "updated_at"])
                log_model_event(
                    actor=actor,
                    action="normalize_ruc_fields_command",
                    instance=client,
                    before_data=before,
                    after_data=get_instance_snapshot(client),
                    metadata={
                        "command": "normalize_client_ruc_fields",
                        "reason": reason,
                        "calculate_missing_dv": calculate_missing_dv,
                    },
                )
                updated += 1

        self.stdout.write(self.style.SUCCESS(f"Actualizados: {updated}"))
        self.stdout.write(self.style.SUCCESS(f"Conflictos sin tocar: {len(conflicts)}"))
