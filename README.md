# HBC Sistema (MVP Django Monolito)

Sistema interno para gestión contable con roles, clientes, vencimientos, operaciones, cobros, contratos, auditoría e importación de Excel/CSV.

## Stack
- Django 4.2
- Django REST Framework
- PostgreSQL (producción) / SQLite (desarrollo local)

## Módulos implementados
- `accounts`: login, logout, `auth/me`, roles (`MASTER`, `ADMIN`, `FUNCIONARIO`) y gestión de usuarios por permisos.
- `clients`: ficha completa de cliente, filtros, búsqueda y soft-delete.
- `operations`: vencimientos (manual + DNIT automático), presentaciones, pendencias y resolver pendencias.
- `billing`: cobros, marcar pagado, contratos.
- `auditing`: historial de cambios por entidad.
- `imports_app`: preview/commit de importación de clientes desde Excel/CSV + enriquecimiento opcional de RUC DV.

## Endpoints principales
- `POST /auth/login`
- `POST /auth/logout`
- `GET /auth/me`
- `GET/POST /users`
- `GET/POST /clients`, `GET/PATCH/DELETE /clients/{id}`
- `GET/POST /deadlines`
- `GET/POST /submissions`
- `GET/POST /pending-items`, `PATCH /pending-items/{id}/resolve`
- `GET/POST /charges`, `POST /charges/{id}/mark-paid`
- `GET/POST /contracts`
- `GET /audit-log?entity=client&entity_id=...`
- `POST /imports/clients/preview`
- `POST /imports/clients/commit`

## Configuración
1) Copiar variables de entorno:

```bash
cp .env.example .env
```

2) Instalar dependencias:

```bash
python3 -m pip install --user -r requirements.txt
```

Variables de entorno importantes (en `.env`):
- `DB_ENGINE=postgres` para PostgreSQL (si no, usa SQLite)
- `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`
- `DJANGO_DEBUG=1|0`
- `DJANGO_ALLOWED_HOSTS=host1,host2`

## Inicialización
```bash
python3 manage.py migrate
python3 manage.py createsuperuser
python3 manage.py runserver
```

UI web:
- `http://127.0.0.1:8000/login/`
- `http://127.0.0.1:8000/dashboard/`
- `http://127.0.0.1:8000/app/clients/`
- `http://127.0.0.1:8000/app/pending-items/`
- `http://127.0.0.1:8000/app/submissions/`
- `http://127.0.0.1:8000/app/charges/`
- `http://127.0.0.1:8000/app/contracts/`

## Importación inicial de clientes
Preview (sin persistir):

```bash
python3 manage.py import_clients
```

Commit (persistir):

```bash
python3 manage.py import_clients --commit --actor <usuario_master>
```

También podés usar API:
- `POST /imports/clients/preview`
- `POST /imports/clients/commit`

Si no se envían archivos, usa por defecto:
- `CLIENTES HBC - VALOR.xlsx`
- `BASE 1.xls`, `BASE 2.xls`, `BASE 3.xls`

## Tests
```bash
python3 manage.py test
```
