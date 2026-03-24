from rest_framework.test import APITestCase
from django.core.files.uploadedfile import SimpleUploadedFile

from accounts.models import User
from auditing.models import AuditLog
from clients.models import Client


class ImportWorkflowTests(APITestCase):
    def setUp(self):
        self.master = User.objects.create_user(username="master2", password="secret123", role=User.Role.MASTER)
        self.funcionario = User.objects.create_user(
            username="func2", password="secret123", role=User.Role.FUNCIONARIO
        )

        self.master_csv = b"nombre,ruc,zona,estado,monto mensual,tipo presentacion\n"
        self.master_csv += b"CLIENTE A,1234567,SANTA RITA,ACTIVO,100000,IVA\n"
        self.master_csv += b"CLIENTE A,1234567,SANTA RITA,ACTIVO,100000,IVA\n"
        self.master_csv += b"CLIENTE B,7654321,KM 32,ACTIVO,0,IRE\n"

        self.aux_csv = b"RUC\n1234567-8\n7654321-4\n"

    def test_funcionario_cannot_import(self):
        self.client.force_authenticate(self.funcionario)
        res = self.client.post("/imports/clients/preview", {}, format="multipart")
        self.assertEqual(res.status_code, 403)

    def test_preview_and_commit_create_audit(self):
        self.client.force_authenticate(self.master)
        master_file = SimpleUploadedFile("clientes.csv", self.master_csv, content_type="text/csv")
        aux_file = SimpleUploadedFile("aux.csv", self.aux_csv, content_type="text/csv")
        res_preview = self.client.post(
            "/imports/clients/preview",
            {"master_file": master_file, "aux_files": [aux_file]},
            format="multipart",
        )
        self.assertEqual(res_preview.status_code, 200)
        self.assertEqual(res_preview.data["valid_rows"], 2)
        self.assertEqual(res_preview.data["duplicates_count"], 1)

        master_file_2 = SimpleUploadedFile("clientes.csv", self.master_csv, content_type="text/csv")
        aux_file_2 = SimpleUploadedFile("aux.csv", self.aux_csv, content_type="text/csv")
        res_commit = self.client.post(
            "/imports/clients/commit",
            {"master_file": master_file_2, "aux_files": [aux_file_2]},
            format="multipart",
        )
        self.assertEqual(res_commit.status_code, 200)
        self.assertEqual(Client.objects.count(), 2)
        self.assertTrue(AuditLog.objects.filter(action="import_create", entity="client").exists())
