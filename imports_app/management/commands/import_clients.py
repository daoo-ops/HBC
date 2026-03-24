from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from accounts.models import User
from imports_app.services import build_preview, commit_preview


class Command(BaseCommand):
    help = "Importa clientes desde Excel/CSV usando el flujo preview/commit del sistema."

    def add_arguments(self, parser):
        parser.add_argument(
            "--master-file",
            default="CLIENTES HBC - VALOR.xlsx",
            help="Ruta al archivo maestro (.xlsx/.csv)",
        )
        parser.add_argument(
            "--aux-file",
            action="append",
            default=[],
            help="Ruta a archivo auxiliar de RUC (.xls/.csv). Repetible.",
        )
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Persiste cambios. Si no se pasa, solo hace preview.",
        )
        parser.add_argument(
            "--actor",
            default="",
            help="Username del usuario que quedará como actor en auditoría.",
        )

    def handle(self, *args, **options):
        base_dir = Path(__file__).resolve().parents[3]

        master_file = Path(options["master_file"])
        if not master_file.is_absolute():
            master_file = base_dir / master_file
        if not master_file.exists():
            raise CommandError(f"Archivo maestro no encontrado: {master_file}")

        aux_files = options["aux_file"]
        if aux_files:
            aux_sources = []
            for raw in aux_files:
                path = Path(raw)
                if not path.is_absolute():
                    path = base_dir / path
                if not path.exists():
                    raise CommandError(f"Archivo auxiliar no encontrado: {path}")
                aux_sources.append(path)
        else:
            aux_sources = [
                base_dir / "BASE 1.xls",
                base_dir / "BASE 2.xls",
                base_dir / "BASE 3.xls",
            ]
            aux_sources = [f for f in aux_sources if f.exists()]

        preview = build_preview(master_file, aux_sources)
        self.stdout.write(self.style.NOTICE("=== PREVIEW ==="))
        self.stdout.write(f"Total filas: {preview.total_rows}")
        self.stdout.write(f"Filas válidas: {preview.valid_rows}")
        self.stdout.write(f"Duplicadas descartadas: {preview.duplicates_count}")
        self.stdout.write(f"Sin nombre: {preview.missing_name_count}")
        self.stdout.write(f"Sin RUC: {preview.missing_ruc_count}")
        for warning in preview.warnings:
            self.stdout.write(self.style.WARNING(f"- {warning}"))

        if not options["commit"]:
            self.stdout.write(self.style.SUCCESS("Preview finalizado (sin persistencia)."))
            return

        actor = None
        actor_username = options["actor"].strip()
        if actor_username:
            actor = User.objects.filter(username=actor_username).first()
            if actor is None:
                raise CommandError(f"No existe usuario actor: {actor_username}")

        result = commit_preview(preview, actor)
        self.stdout.write(self.style.SUCCESS("=== COMMIT ==="))
        self.stdout.write(f"Creados: {result['created']}")
        self.stdout.write(f"Actualizados: {result['updated']}")
        self.stdout.write(f"Procesados: {result['processed']}")
