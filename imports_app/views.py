from pathlib import Path
from decimal import Decimal

from rest_framework import permissions, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsMasterOrAdmin
from imports_app.models import ImportBatch
from imports_app.services import build_preview, commit_preview


def _json_safe(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    return value


class _BaseImportAPIView(APIView):
    parser_classes = (MultiPartParser, FormParser, JSONParser)
    permission_classes = [permissions.IsAuthenticated, IsMasterOrAdmin]

    def _collect_sources(self, request):
        workspace = Path(__file__).resolve().parent.parent

        master_file = request.FILES.get("master_file")
        if master_file:
            master_source = (master_file.name, master_file.read())
        else:
            master_source = workspace / "CLIENTES HBC - VALOR.xlsx"

        uploaded_aux = request.FILES.getlist("aux_files")
        if uploaded_aux:
            aux_sources = [(f.name, f.read()) for f in uploaded_aux]
        else:
            aux_sources = [
                workspace / "BASE 1.xls",
                workspace / "BASE 2.xls",
                workspace / "BASE 3.xls",
            ]
        return master_source, aux_sources

    @staticmethod
    def _source_name(source):
        if isinstance(source, tuple):
            return source[0]
        return str(source)


class ClientsImportPreviewAPIView(_BaseImportAPIView):
    def post(self, request):
        master_source, aux_sources = self._collect_sources(request)
        preview = build_preview(master_source, aux_sources)

        payload = {
            "total_rows": preview.total_rows,
            "valid_rows": preview.valid_rows,
            "duplicates_count": preview.duplicates_count,
            "missing_name_count": preview.missing_name_count,
            "missing_ruc_count": preview.missing_ruc_count,
            "warnings": preview.warnings,
            "sample": _json_safe(preview.rows[:50]),
        }

        ImportBatch.objects.create(
            created_by=request.user,
            status=ImportBatch.Status.PREVIEW,
            source_name=self._source_name(master_source),
            summary=_json_safe(payload),
        )

        return Response(payload, status=status.HTTP_200_OK)


class ClientsImportCommitAPIView(_BaseImportAPIView):
    def post(self, request):
        master_source, aux_sources = self._collect_sources(request)
        preview = build_preview(master_source, aux_sources)
        result = commit_preview(preview, request.user)

        payload = {
            **result,
            "total_rows": preview.total_rows,
            "duplicates_count": preview.duplicates_count,
            "warnings": preview.warnings,
        }

        ImportBatch.objects.create(
            created_by=request.user,
            status=ImportBatch.Status.COMMITTED,
            source_name=self._source_name(master_source),
            summary=_json_safe(payload),
        )

        return Response(payload, status=status.HTTP_200_OK)
