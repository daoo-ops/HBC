from datetime import date, timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import CharField, Count, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone

from accounts.models import User
from auditing.models import AuditLog
from auditing.services import get_instance_snapshot, log_model_event
from banks.forms import BankRequestForm, BankRequestNoteForm
from banks.models import BankRequest
from banks.services import (
    create_or_link_document_pending,
    mark_archived,
    mark_completed,
    mark_document_loaded,
    mark_in_progress,
    reopen_archived,
)
from billing.forms import ChargeForm, ContractForm
from billing.models import Charge, Contract
from billing.services import sync_client_billing_snapshot
from clients.forms import ClientForm, ClientNoteForm, ClientOperationalForm
from clients.models import Client, ClientInvoicePeriodStatus, ClientNote, ClientObligation, Obligation
from clients.services import track_responsible_change
from notifications.models import UserNotification
from notifications.services import notify_users, recipients_for_bank_request, recipients_for_client
from operations.forms import PendingItemForm, SubmissionForm
from operations.models import PendingItem, Submission
from operations.services import ensure_period_submissions_for_clients
from tax_commitments.forms import TaxCommitmentForm, TaxCommitmentInstallmentForm
from tax_commitments.models import TaxCommitment
from payment_logs.models import PaymentReceptionLog


MONTH_CHOICES = [
    (1, "Enero"),
    (2, "Febrero"),
    (3, "Marzo"),
    (4, "Abril"),
    (5, "Mayo"),
    (6, "Junio"),
    (7, "Julio"),
    (8, "Agosto"),
    (9, "Septiembre"),
    (10, "Octubre"),
    (11, "Noviembre"),
    (12, "Diciembre"),
]


def _is_manager(user):
    return user.role in {User.Role.MASTER, User.Role.ADMIN}


def _manager_required(request):
    if _is_manager(request.user):
        return None
    return HttpResponseForbidden("No tenés permisos para esta acción.")


def _can_access_client(user, client):
    if _is_manager(user):
        return True
    return bool(client and client.responsible_id == user.id)


def _client_access_required(request, client):
    if _can_access_client(request.user, client):
        return None
    return HttpResponseForbidden("No tenés permisos para acceder a este cliente.")


def _can_access_bank_request(user, item):
    if _is_manager(user):
        return True
    return item.responsible_id == user.id


def _normalize_pending_priority_value(value: str) -> str:
    normalized = str(value or "").strip().upper()
    if normalized == "SOON":
        return PendingItem.Priority.OK
    if normalized in {PendingItem.Priority.OK, PendingItem.Priority.URGENT}:
        return normalized
    return ""


def _pending_priority_for_bank_request(item: BankRequest) -> str:
    if item.request_priority == BankRequest.Priority.URGENT:
        return PendingItem.Priority.URGENT
    return PendingItem.Priority.OK


def _bank_access_required(request, item):
    if _can_access_bank_request(request.user, item):
        return None
    return HttpResponseForbidden("No tenés permisos para acceder a esta solicitud.")


def _bank_scope_for_status(status_value):
    if status_value in {BankRequest.Status.REQUESTED, BankRequest.Status.IN_PROGRESS}:
        return "active"
    if status_value == BankRequest.Status.COMPLETED:
        return "completed"
    return "archived"


def _tax_commitment_is_effective_overdue(item, reference_date):
    if item.status in {TaxCommitment.Status.PAID, TaxCommitment.Status.ARCHIVED}:
        return False
    return bool(item.due_date and item.due_date < reference_date)


def _attach_pending_bank_origin_metadata(request, open_items, resolved_items):
    all_items = list(open_items) + list(resolved_items)
    if not all_items:
        return list(open_items), list(resolved_items)

    pending_ids = [item.id for item in all_items]
    bank_requests = (
        BankRequest.objects.select_related("client", "responsible", "requested_by")
        .filter(receipts_pending_item_id__in=pending_ids)
        .all()
    )

    pending_origin_map = {}
    for bank_item in bank_requests:
        if bank_item.receipts_pending_item_id:
            pending_origin_map[bank_item.receipts_pending_item_id] = {
                "bank_request_id": bank_item.id,
                "kind": "receipts",
                "label": "Bancos y recibos / Recibos",
                "can_access": _can_access_bank_request(request.user, bank_item),
                "can_mark": bank_item.receipts_status != BankRequest.DocumentStatus.LOADED,
                "scope": _bank_scope_for_status(bank_item.status),
            }

    def _enrich(item):
        origin = pending_origin_map.get(item.id)
        if not origin:
            item.origin_label = "Manual"
            item.bank_request_id = None
            item.bank_origin_kind = ""
            item.bank_can_mark = False
            item.bank_request_url = ""
            return item

        item.origin_label = origin["label"]
        item.bank_request_id = origin["bank_request_id"]
        item.bank_origin_kind = origin["kind"]
        item.bank_can_mark = bool(origin["can_access"] and origin["can_mark"] and item.status == PendingItem.Status.OPEN)
        item.bank_request_url = (
            f"/app/banks/?scope={origin['scope']}&focus={origin['bank_request_id']}#bank-request-{origin['bank_request_id']}"
            if origin["can_access"]
            else ""
        )
        return item

    open_list = [_enrich(item) for item in list(open_items)]
    resolved_list = [_enrich(item) for item in list(resolved_items)]
    return open_list, resolved_list


def _next_or_default(request, default_url):
    next_url = (request.POST.get("next") or "").strip()
    if next_url.startswith("/"):
        return next_url
    return default_url


def _notify_pending_created(*, actor, item):
    severity = UserNotification.Severity.URGENT if item.priority == PendingItem.Priority.URGENT else UserNotification.Severity.NORMAL
    notify_users(
        actor=actor,
        recipient_ids=recipients_for_client(client=item.client),
        client=item.client,
        severity=severity,
        message=f"Nuevo pendiente: {item.description}",
        target_url="/app/pending-items/",
        event_key="pending_created",
        source_ref=f"pending:{item.id}",
    )


def _notify_submission_event(*, actor, item, message, event_key):
    notify_users(
        actor=actor,
        recipient_ids=recipients_for_client(client=item.client),
        client=item.client,
        severity=UserNotification.Severity.INFO,
        message=message,
        target_url="/app/submissions/",
        event_key=event_key,
        source_ref=f"submission:{item.id}",
    )


def _notify_tax_commitment_event(*, actor, item, message, event_key, severity=UserNotification.Severity.NORMAL):
    notify_users(
        actor=actor,
        recipient_ids=recipients_for_client(client=item.client),
        client=item.client,
        severity=severity,
        message=message,
        target_url="/app/tax-commitments/",
        event_key=event_key,
        source_ref=f"tax_commitment:{item.id}",
    )


def _notify_bank_event(*, actor, item, message, event_key, severity=UserNotification.Severity.NORMAL):
    notify_users(
        actor=actor,
        recipient_ids=recipients_for_bank_request(bank_request=item),
        client=item.client,
        severity=severity,
        message=message,
        target_url=f"/app/banks/?focus={item.id}#bank-request-{item.id}",
        event_key=event_key,
        source_ref=f"bank_request:{item.id}",
    )


def _normalize_submission_record(item):
    if item.obligation_id and not item.period_kind:
        periodicity = (item.obligation.default_periodicity or "").upper()
        if periodicity == "MONTHLY":
            item.period_kind = Submission.PeriodKind.MONTHLY
        elif periodicity == "ANNUAL":
            item.period_kind = Submission.PeriodKind.ANNUAL
        else:
            item.period_kind = Submission.PeriodKind.OTHER

    reference_date = item.due_date or item.submitted_at
    if item.period_kind == Submission.PeriodKind.MONTHLY:
        if not item.period_year and reference_date:
            item.period_year = reference_date.year
        if not item.period_month and reference_date:
            item.period_month = reference_date.month

    if item.period_kind == Submission.PeriodKind.ANNUAL:
        if not item.period_year and reference_date:
            item.period_year = reference_date.year
        item.period_month = None

    if item.period_kind == Submission.PeriodKind.OTHER:
        if not item.period_year and reference_date:
            item.period_year = reference_date.year
        if not item.period_month and reference_date:
            item.period_month = reference_date.month

    item.needs_manual_review = not bool(item.obligation_id and item.period_year)


@login_required
def app_clients_list(request):
    clients = Client.objects.filter(is_deleted=False)
    today = timezone.localdate()
    soon_cutoff = today + timedelta(days=3)
    is_funcionario = request.user.role == User.Role.FUNCIONARIO
    mine_filter = request.GET.get("mine", "")
    responsible_filter = request.GET.get("responsible", "")

    if is_funcionario:
        clients = clients.filter(responsible_id=request.user.id)
        mine_filter = "1"
    else:
        if responsible_filter:
            clients = clients.filter(responsible_id=responsible_filter)
        elif mine_filter == "1":
            clients = clients.filter(responsible_id=request.user.id)

    q = request.GET.get("q", "").strip()
    if q:
        clients = clients.filter(Q(name__icontains=q) | Q(ruc__icontains=q))

    zone = request.GET.get("zone", "")
    if zone:
        clients = clients.filter(zone=zone)

    status_filter = request.GET.get("status", "")
    if status_filter:
        clients = clients.filter(status=status_filter)
    raw_month = (request.GET.get("month") or "").strip()
    raw_year = (request.GET.get("year") or "").strip()
    try:
        selected_month = int(raw_month) if raw_month else today.month
    except ValueError:
        selected_month = today.month
    try:
        selected_year = int(raw_year) if raw_year else today.year
    except ValueError:
        selected_year = today.year
    if selected_month < 1 or selected_month > 12:
        selected_month = today.month
    if selected_year < 2000 or selected_year > 2100:
        selected_year = today.year

    period_records_for_selected_period = ClientInvoicePeriodStatus.objects.filter(
        client_id=OuterRef("pk"),
        year=selected_year,
        month=selected_month,
    )
    period_status_subquery = period_records_for_selected_period.values("status")[:1]
    period_record_id_subquery = period_records_for_selected_period.values("id")[:1]
    clients = clients.annotate(
        invoice_period_status_selected=Coalesce(
            Subquery(period_status_subquery),
            Value(ClientInvoicePeriodStatus.Status.PENDING),
            output_field=CharField(),
        ),
        invoice_period_record_id_selected=Subquery(period_record_id_subquery),
    )

    invoice_status_filter = request.GET.get("invoice_status", "")
    if invoice_status_filter in {
        ClientInvoicePeriodStatus.Status.PENDING,
        ClientInvoicePeriodStatus.Status.PARTIAL,
        ClientInvoicePeriodStatus.Status.RECEIVED,
    }:
        clients = clients.filter(invoice_period_status_selected=invoice_status_filter)

    month_label = dict(MONTH_CHOICES).get(selected_month, f"Mes {selected_month}")
    year_choices = [today.year - 1, today.year, today.year + 1, today.year + 2]

    context = {
        "clients": clients.order_by("name"),
        "q": q,
        "zone": zone,
        "status_filter": status_filter,
        "invoice_status_filter": invoice_status_filter,
        "selected_month": selected_month,
        "selected_year": selected_year,
        "selected_period_label": f"{month_label} {selected_year}",
        "month_choices": MONTH_CHOICES,
        "year_choices": year_choices,
        "mine_filter": mine_filter,
        "responsible_filter": responsible_filter,
        "zone_choices": Client.Zone.choices,
        "status_choices": Client.Status.choices,
        "invoice_status_choices": ClientInvoicePeriodStatus.Status.choices,
        "responsible_choices": User.objects.filter(role=User.Role.FUNCIONARIO).order_by("username"),
        "obligation_choices": Obligation.objects.filter(is_active=True).order_by("name"),
        "is_funcionario": is_funcionario,
        "can_manage_clients": _is_manager(request.user),
        "can_view_financial": _is_manager(request.user),
        "today": today,
        "soon_cutoff": soon_cutoff,
    }
    return render(request, "app/clients_list.html", context)


@login_required
def app_client_create(request):
    forbidden = _manager_required(request)
    if forbidden:
        return forbidden

    if request.method == "POST":
        form = ClientForm(request.POST)
        if form.is_valid():
            old_responsible_id = None
            client = form.save()
            log_model_event(
                actor=request.user,
                action="create_ui",
                instance=client,
                after_data=get_instance_snapshot(client),
            )
            track_responsible_change(
                client=client,
                old_responsible_id=old_responsible_id,
                actor=request.user,
                reason="Asignación inicial desde UI",
            )
            messages.success(request, "Cliente creado correctamente.")
            return redirect("app-client-detail", client_id=client.id)
    else:
        form = ClientForm()

    return render(
        request,
        "app/form_page.html",
        {
            "title": "Nuevo cliente",
            "form": form,
            "back_url": "/app/clients/",
            "enable_ruc_dv_helper": True,
            "enable_client_form_helper": True,
        },
    )


@login_required
def app_client_edit(request, client_id):
    client = get_object_or_404(Client, id=client_id, is_deleted=False)
    forbidden = _client_access_required(request, client)
    if forbidden:
        return forbidden

    form_class = ClientForm if _is_manager(request.user) else ClientOperationalForm

    if request.method == "POST":
        old_responsible_id = client.responsible_id
        before = get_instance_snapshot(client)
        form = form_class(request.POST, instance=client)
        if form.is_valid():
            updated = form.save()
            log_model_event(
                actor=request.user,
                action="update_ui",
                instance=updated,
                before_data=before,
                after_data=get_instance_snapshot(updated),
            )
            track_responsible_change(
                client=updated,
                old_responsible_id=old_responsible_id,
                actor=request.user,
                reason="Reasignación desde UI",
            )
            messages.success(request, "Cliente actualizado.")
            return redirect("app-client-detail", client_id=client.id)
    else:
        form = form_class(instance=client)

    return render(
        request,
        "app/form_page.html",
        {
            "title": f"Editar cliente: {client.name}",
            "form": form,
            "back_url": f"/app/clients/{client.id}/",
            "enable_ruc_dv_helper": True,
            "enable_client_form_helper": True,
        },
    )


@login_required
def app_client_invoice_period_status_update(request, client_id):
    client = get_object_or_404(Client, id=client_id, is_deleted=False)
    forbidden = _client_access_required(request, client)
    if forbidden:
        return forbidden
    if request.method != "POST":
        return redirect("app-client-detail", client_id=client.id)

    requested_status = (request.POST.get("invoice_period_status") or "").strip()
    valid_statuses = {choice[0] for choice in ClientInvoicePeriodStatus.Status.choices}
    if requested_status not in valid_statuses and requested_status != "RESET":
        messages.error(request, "Estado de facturas del período inválido.")
        return redirect(_next_or_default(request, f"/app/clients/{client.id}/"))

    today = timezone.localdate()
    raw_month = (request.POST.get("month") or "").strip()
    raw_year = (request.POST.get("year") or "").strip()
    try:
        selected_month = int(raw_month) if raw_month else today.month
    except ValueError:
        selected_month = today.month
    try:
        selected_year = int(raw_year) if raw_year else today.year
    except ValueError:
        selected_year = today.year
    if selected_month < 1 or selected_month > 12:
        selected_month = today.month
    if selected_year < 2000 or selected_year > 2100:
        selected_year = today.year

    before = get_instance_snapshot(client)

    period_record = ClientInvoicePeriodStatus.objects.filter(
        client=client,
        year=selected_year,
        month=selected_month,
    ).first()

    if requested_status == "RESET":
        if period_record:
            period_record.delete()
        if selected_year == today.year and selected_month == today.month:
            client.invoice_period_status = Client.InvoicePeriodStatus.PENDING
            client.invoice_period_status_updated_by = None
            client.invoice_period_status_updated_at = timezone.now()
            client.save(
                update_fields=[
                    "invoice_period_status",
                    "invoice_period_status_updated_by",
                    "invoice_period_status_updated_at",
                    "updated_at",
                ]
            )
        log_model_event(
            actor=request.user,
            action="reset_invoice_period_status_ui",
            instance=client,
            before_data=before,
            after_data=get_instance_snapshot(client),
            metadata={
                "period_year": selected_year,
                "period_month": selected_month,
                "status": "RESET",
            },
        )
        messages.success(request, f"Estado de facturas reiniciado para {selected_month:02d}/{selected_year}.")
        return redirect(_next_or_default(request, f"/app/clients/{client.id}/"))

    if period_record is None:
        period_record = ClientInvoicePeriodStatus.objects.create(
            client=client,
            year=selected_year,
            month=selected_month,
            status=requested_status,
            updated_by=request.user,
        )
    elif period_record.status != requested_status or period_record.updated_by_id != request.user.id:
        period_record.status = requested_status
        period_record.updated_by = request.user
        period_record.save(update_fields=["status", "updated_by", "updated_at"])
    else:
        return redirect(_next_or_default(request, f"/app/clients/{client.id}/"))

    # Compatibilidad: el campo legado de Client refleja el estado del mes actual.
    if selected_year == today.year and selected_month == today.month:
        legacy_status = (
            Client.InvoicePeriodStatus.RECEIVED
            if requested_status == ClientInvoicePeriodStatus.Status.RECEIVED
            else Client.InvoicePeriodStatus.PENDING
        )
        client.invoice_period_status = legacy_status
        client.invoice_period_status_updated_by = request.user
        client.invoice_period_status_updated_at = timezone.now()
        client.save(
            update_fields=[
                "invoice_period_status",
                "invoice_period_status_updated_by",
                "invoice_period_status_updated_at",
                "updated_at",
            ]
        )

    log_model_event(
        actor=request.user,
        action="update_invoice_period_status_ui",
        instance=client,
        before_data=before,
        after_data=get_instance_snapshot(client),
        metadata={
            "period_year": selected_year,
            "period_month": selected_month,
            "status": requested_status,
        },
    )
    messages.success(request, f"Estado de facturas actualizado para {selected_month:02d}/{selected_year}.")
    return redirect(_next_or_default(request, f"/app/clients/{client.id}/"))


@login_required
def app_client_detail(request, client_id):
    client = get_object_or_404(Client, id=client_id, is_deleted=False)
    forbidden = _client_access_required(request, client)
    if forbidden:
        return forbidden
    can_view_financial = _is_manager(request.user)

    notes = client.notes.all()
    pending_items = client.pending_items.filter(is_deleted=False)
    open_pending_items = pending_items.filter(status=PendingItem.Status.OPEN)
    resolved_pending_items = pending_items.filter(status=PendingItem.Status.RESOLVED)
    submissions = list(
        client.submissions.select_related("obligation", "archived_by", "created_by").all()
    )
    for item in submissions:
        _normalize_submission_record(item)
    today = timezone.localdate()
    soon_cutoff = today + timedelta(days=3)
    submission_active_items = [
        item for item in submissions if not item.is_archived and item.status != Submission.Status.SUBMITTED
    ]
    submission_submitted_items = [
        item for item in submissions if not item.is_archived and item.status == Submission.Status.SUBMITTED
    ]
    submission_archived_items = [item for item in submissions if item.is_archived]
    charges = client.charges.select_related("contract").all() if can_view_financial else Charge.objects.none()
    contracts = client.contracts.all() if can_view_financial else Contract.objects.none()
    payment_logs = client.payment_reception_logs.select_related("recorded_by", "archived_by").all()
    obligations = client.client_obligations.select_related("obligation").filter(
        obligation__isnull=False,
        status=ClientObligation.Status.ACTIVE,
    )
    obligations_pending_review = client.client_obligations.filter(needs_manual_review=True)
    responsibility_history = client.responsibility_history.select_related(
        "old_responsible",
        "new_responsible",
        "changed_by",
    )[:10]

    if can_view_financial:
        debt_summary = charges.filter(status=Charge.Status.PENDING).aggregate(total=Sum("debt_amount"))
        current_debt = debt_summary["total"] or Decimal("0")
    else:
        current_debt = Decimal("0")

    recent_activity = []
    for note in notes.order_by("-updated_at")[:6]:
        recent_activity.append(
            {
                "date": note.updated_at,
                "kind": "Nota",
                "title": (note.note[:90] + "...") if len(note.note) > 90 else note.note,
                "badge": "pending",
                "state_label": "Nota",
                "link": f"/app/notes/{note.id}/edit/",
                "actor": note.updated_by.username if note.updated_by else "sistema",
            }
        )

    for item in pending_items.order_by("-updated_at")[:6]:
        recent_activity.append(
            {
                "date": item.updated_at,
                "kind": "Pendiente",
                "title": item.description,
                "badge": "resolved" if item.status == PendingItem.Status.RESOLVED else "urgent" if item.priority == PendingItem.Priority.URGENT else "pending",
                "state_label": item.get_status_display(),
                "link": f"/app/pending-items/{item.id}/edit/",
                "actor": item.created_by.username if item.created_by else "sistema",
            }
        )

    for item in sorted(submissions, key=lambda row: row.updated_at, reverse=True)[:6]:
        if item.is_archived:
            badge = "archived"
        elif item.status == Submission.Status.SUBMITTED:
            badge = "submitted"
        elif item.status == Submission.Status.LATE or (item.due_date and item.due_date < today):
            badge = "overdue"
        else:
            badge = "pending"
        title = item.obligation_name_display
        if item.period_display != "-":
            title = f"{title} ({item.period_display})"
        recent_activity.append(
            {
                "date": item.updated_at,
                "kind": "Obligación fiscal",
                "title": title,
                "badge": badge,
                "state_label": "Archivado" if item.is_archived else item.get_status_display(),
                "link": f"/app/submissions/{item.id}/edit/",
                "actor": item.created_by.username if item.created_by else "sistema",
            }
        )

    for item in AuditLog.objects.filter(entity="client", entity_id=str(client.id)).select_related("actor")[:8]:
        action_label = {
            "create": "Creado",
            "create_ui": "Creado",
            "update": "Actualizado",
            "update_ui": "Actualizado",
            "reassign_responsible": "Reasignación",
            "archive": "Archivado",
            "archive_ui": "Archivado",
            "reopen": "Reabierto",
            "reopen_ui": "Reabierto",
        }.get(item.action, item.action)
        recent_activity.append(
            {
                "date": item.created_at,
                "kind": "Cambio",
                "title": f"{action_label} de cliente",
                "badge": "warning",
                "state_label": action_label,
                "link": f"/app/clients/{client.id}/",
                "actor": item.actor.username if item.actor else "sistema",
            }
        )

    recent_activity.sort(key=lambda row: row["date"], reverse=True)
    recent_activity = recent_activity[:20]

    context = {
        "client": client,
        "notes": notes,
        "pending_items": pending_items,
        "open_pending_items": open_pending_items,
        "resolved_pending_items": resolved_pending_items,
        "submissions": submissions,
        "submission_active_items": submission_active_items,
        "submission_submitted_items": submission_submitted_items,
        "submission_archived_items": submission_archived_items,
        "charges": charges,
        "contracts": contracts,
        "payment_logs": payment_logs,
        "obligations": obligations,
        "obligations_pending_review": obligations_pending_review,
        "responsibility_history": responsibility_history,
        "recent_activity": recent_activity,
        "current_debt": current_debt,
        "note_form": ClientNoteForm(),
        "can_manage_clients": _is_manager(request.user),
        "can_manage_contracts": _is_manager(request.user),
        "can_view_financial": can_view_financial,
        "today": today,
        "soon_cutoff": soon_cutoff,
    }
    return render(request, "app/client_detail.html", context)


@login_required
def app_note_create(request, client_id):
    client = get_object_or_404(Client, id=client_id, is_deleted=False)
    forbidden = _client_access_required(request, client)
    if forbidden:
        return forbidden
    if request.method != "POST":
        return redirect("app-client-detail", client_id=client.id)

    form = ClientNoteForm(request.POST)
    if form.is_valid():
        note = form.save(commit=False)
        note.client = client
        note.created_by = request.user
        note.updated_by = request.user
        note.save()
        log_model_event(
            actor=request.user,
            action="create_ui",
            instance=note,
            after_data=get_instance_snapshot(note),
        )
        messages.success(request, "Nota agregada.")
    else:
        messages.error(request, "No se pudo guardar la nota.")
    return redirect(_next_or_default(request, f"/app/clients/{client.id}/"))


@login_required
def app_note_edit(request, note_id):
    note = get_object_or_404(ClientNote, id=note_id)
    forbidden = _client_access_required(request, note.client)
    if forbidden:
        return forbidden

    if request.method == "POST":
        before = get_instance_snapshot(note)
        form = ClientNoteForm(request.POST, instance=note)
        if form.is_valid():
            note_obj = form.save(commit=False)
            note_obj.updated_by = request.user
            note_obj.save()
            log_model_event(
                actor=request.user,
                action="update_ui",
                instance=note_obj,
                before_data=before,
                after_data=get_instance_snapshot(note_obj),
            )
            messages.success(request, "Nota actualizada.")
            return redirect("app-client-detail", client_id=note.client_id)
    else:
        form = ClientNoteForm(instance=note)

    return render(
        request,
        "app/form_page.html",
        {
            "title": f"Editar nota - {note.client.name}",
            "form": form,
            "back_url": f"/app/clients/{note.client_id}/",
        },
    )


@login_required
def app_pending_list(request):
    items = PendingItem.objects.select_related("client").filter(is_deleted=False)
    if not _is_manager(request.user):
        items = items.filter(client__responsible_id=request.user.id)

    q = request.GET.get("q", "").strip()
    if q:
        items = items.filter(Q(description__icontains=q) | Q(client__name__icontains=q) | Q(client__ruc__icontains=q))

    priority = _normalize_pending_priority_value(request.GET.get("priority", ""))
    if priority:
        items = items.filter(priority=priority)

    status_filter = request.GET.get("status", "")
    if status_filter:
        items = items.filter(status=status_filter)

    open_items = items.filter(status=PendingItem.Status.OPEN)
    resolved_items = items.filter(status=PendingItem.Status.RESOLVED)
    open_items, resolved_items = _attach_pending_bank_origin_metadata(request, open_items, resolved_items)

    return render(
        request,
        "app/pending_list.html",
        {
            "open_items": open_items,
            "resolved_items": resolved_items,
            "q": q,
            "status_filter": status_filter,
            "priority_filter": priority,
            "status_choices": PendingItem.Status.choices,
            "priority_choices": PendingItem.Priority.choices,
        },
    )


@login_required
def app_notifications_panel(request):
    notifications_qs = UserNotification.objects.select_related("client", "actor").filter(recipient=request.user)
    unread_count = notifications_qs.filter(is_read=False).count()
    notifications = list(notifications_qs.order_by("is_read", "-created_at")[:20])
    html = render_to_string(
        "app/_notifications_panel.html",
        {
            "notifications": notifications,
            "unread_count": unread_count,
        },
        request=request,
    )
    return JsonResponse({"html": html, "unread_count": unread_count})


@login_required
def app_notifications_mark_read(request):
    if request.method != "POST":
        return JsonResponse({"ok": False, "detail": "Method not allowed."}, status=405)
    now = timezone.now()
    updated = UserNotification.objects.filter(recipient=request.user, is_read=False).update(is_read=True, read_at=now)
    return JsonResponse({"ok": True, "marked": updated, "unread_count": 0})


@login_required
def app_pending_create(request):
    initial = {}
    client_id = request.GET.get("client")
    if client_id:
        initial["client"] = client_id

    if request.method == "POST":
        payload = request.POST.copy()
        normalized_priority = _normalize_pending_priority_value(payload.get("priority"))
        if normalized_priority:
            payload["priority"] = normalized_priority
        form = PendingItemForm(payload)
        if not _is_manager(request.user):
            form.fields["client"].queryset = Client.objects.filter(is_deleted=False, responsible_id=request.user.id)
        if form.is_valid():
            forbidden = _client_access_required(request, form.cleaned_data["client"])
            if forbidden:
                return forbidden
            item = form.save(commit=False)
            item.created_by = request.user
            if item.status == PendingItem.Status.RESOLVED and not item.resolved_at:
                item.resolved_at = timezone.now()
            item.save()
            log_model_event(
                actor=request.user,
                action="create_ui",
                instance=item,
                after_data=get_instance_snapshot(item),
            )
            _notify_pending_created(actor=request.user, item=item)
            messages.success(request, "Pendiente creado.")
            return redirect(_next_or_default(request, f"/app/clients/{item.client_id}/"))
    else:
        form = PendingItemForm(initial=initial)
        if not _is_manager(request.user):
            form.fields["client"].queryset = Client.objects.filter(is_deleted=False, responsible_id=request.user.id)

    return render(
        request,
        "app/form_page.html",
        {
            "title": "Nuevo pendiente",
            "form": form,
            "back_url": "/app/pending-items/",
        },
    )


@login_required
def app_pending_edit(request, item_id):
    item = get_object_or_404(PendingItem, id=item_id, is_deleted=False)
    forbidden = _client_access_required(request, item.client)
    if forbidden:
        return forbidden

    if request.method == "POST":
        before = get_instance_snapshot(item)
        payload = request.POST.copy()
        normalized_priority = _normalize_pending_priority_value(payload.get("priority"))
        if normalized_priority:
            payload["priority"] = normalized_priority
        form = PendingItemForm(payload, instance=item)
        if not _is_manager(request.user):
            form.fields["client"].queryset = Client.objects.filter(is_deleted=False, responsible_id=request.user.id)
        if form.is_valid():
            forbidden = _client_access_required(request, form.cleaned_data["client"])
            if forbidden:
                return forbidden
            updated = form.save(commit=False)
            if updated.status == PendingItem.Status.RESOLVED and not updated.resolved_at:
                updated.resolved_at = timezone.now()
            updated.save()
            log_model_event(
                actor=request.user,
                action="update_ui",
                instance=updated,
                before_data=before,
                after_data=get_instance_snapshot(updated),
            )
            messages.success(request, "Pendiente actualizado.")
            return redirect("app-client-detail", client_id=updated.client_id)
    else:
        form = PendingItemForm(instance=item)
        if not _is_manager(request.user):
            form.fields["client"].queryset = Client.objects.filter(is_deleted=False, responsible_id=request.user.id)

    return render(
        request,
        "app/form_page.html",
        {
            "title": "Editar pendiente",
            "form": form,
            "back_url": f"/app/clients/{item.client_id}/",
        },
    )


@login_required
def app_pending_resolve(request, item_id):
    item = get_object_or_404(PendingItem, id=item_id, is_deleted=False)
    forbidden = _client_access_required(request, item.client)
    if forbidden:
        return forbidden
    if request.method != "POST":
        return redirect("app-client-detail", client_id=item.client_id)

    before = get_instance_snapshot(item)
    item.status = PendingItem.Status.RESOLVED
    item.resolved_at = timezone.now()
    item.save(update_fields=["status", "resolved_at", "updated_at"])

    log_model_event(
        actor=request.user,
        action="resolve_ui",
        instance=item,
        before_data=before,
        after_data=get_instance_snapshot(item),
    )
    messages.success(request, "Pendiente marcado como resuelto.")
    return redirect("app-client-detail", client_id=item.client_id)


@login_required
def app_pending_delete(request, item_id):
    item = get_object_or_404(PendingItem, id=item_id, is_deleted=False)
    forbidden = _client_access_required(request, item.client)
    if forbidden:
        return forbidden
    if request.method != "POST":
        return redirect("app-client-detail", client_id=item.client_id)

    before = get_instance_snapshot(item)
    item.is_deleted = True
    item.deleted_at = timezone.now()
    item.deleted_by = request.user
    item.save(update_fields=["is_deleted", "deleted_at", "deleted_by", "updated_at"])

    log_model_event(
        actor=request.user,
        action="soft_delete_ui",
        instance=item,
        before_data=before,
        after_data=get_instance_snapshot(item),
    )
    messages.success(request, "Pendiente eliminado.")
    return redirect(_next_or_default(request, f"/app/clients/{item.client_id}/"))


@login_required
def app_pending_mark_bank_document_loaded(request, item_id, document_kind):
    if document_kind != "receipts":
        return HttpResponseForbidden("Tipo de documento inválido.")

    pending_item = get_object_or_404(PendingItem, id=item_id, is_deleted=False)
    forbidden = _client_access_required(request, pending_item.client)
    if forbidden:
        return forbidden
    if request.method != "POST":
        return redirect("app-pending-list")

    relation_field = "receipts_pending_item_id"
    bank_item = (
        BankRequest.objects.select_related("client", "responsible", "requested_by")
        .filter(**{relation_field: pending_item.id})
        .first()
    )
    if not bank_item:
        messages.error(request, "No se encontró solicitud de Bancos y recibos vinculada.")
        return redirect(_next_or_default(request, "/app/pending-items/"))

    forbidden = _bank_access_required(request, bank_item)
    if forbidden:
        return forbidden

    before_bank = get_instance_snapshot(bank_item)
    before_pending = get_instance_snapshot(pending_item)
    resolved_pending = mark_document_loaded(item=bank_item, actor=request.user, document_kind=document_kind)

    log_model_event(
        actor=request.user,
        action=f"mark_{document_kind}_loaded_from_pending_ui",
        instance=bank_item,
        before_data=before_bank,
        after_data=get_instance_snapshot(bank_item),
        metadata={"pending_item_id": pending_item.id},
    )

    if resolved_pending and resolved_pending.id == pending_item.id:
        log_model_event(
            actor=request.user,
            action="resolve_from_bank_request_ui",
            instance=resolved_pending,
            before_data=before_pending,
            after_data=get_instance_snapshot(resolved_pending),
            metadata={"bank_request_id": bank_item.id, "document_kind": document_kind},
        )
        messages.success(
            request,
            "Documento marcado como cargado. El pendiente se resolvió automáticamente.",
        )
    else:
        messages.success(request, "Documento marcado como cargado.")
    _notify_bank_event(
        actor=request.user,
        item=bank_item,
        message="Recibos marcados como cargados en Bancos y recibos.",
        event_key="bank_receipts_loaded_from_pending",
        severity=UserNotification.Severity.INFO,
    )

    return redirect(_next_or_default(request, "/app/pending-items/"))


@login_required
def app_submission_list(request):
    today = timezone.localdate()
    clients_scope = Client.objects.filter(is_deleted=False, status=Client.Status.ACTIVE).prefetch_related(
        "client_obligations__obligation"
    )
    if not _is_manager(request.user):
        clients_scope = clients_scope.filter(responsible_id=request.user.id)
    ensure_period_submissions_for_clients(clients_scope, year=today.year, month=today.month)

    items = Submission.objects.select_related("client", "obligation").all()
    if not _is_manager(request.user):
        items = items.filter(client__responsible_id=request.user.id)

    q = request.GET.get("q", "").strip()
    if q:
        items = items.filter(
            Q(submission_type__icontains=q)
            | Q(client__name__icontains=q)
            | Q(client__ruc__icontains=q)
            | Q(obligation__name__icontains=q)
            | Q(obligation__form_code__icontains=q)
        )

    status_filter = request.GET.get("status", "")
    if status_filter:
        items = items.filter(status=status_filter)
    items_for_filters = items

    obligation_filter = request.GET.get("obligation", "")
    if obligation_filter:
        items = items.filter(obligation_id=obligation_filter)

    period_year_filter = request.GET.get("period_year", "")
    if period_year_filter:
        items = items.filter(period_year=period_year_filter)

    period_month_filter = request.GET.get("period_month", "")
    if period_month_filter:
        items = items.filter(period_month=period_month_filter)

    scope = (request.GET.get("scope") or "active").strip().lower()
    if scope == "main":
        scope = "active"
    if scope == "finalized":
        scope = "submitted"
    if scope == "all":
        scope = "history"
    if scope not in {"active", "submitted", "archived", "history"}:
        scope = "active"

    week_cutoff = today + timedelta(days=7)

    active_items = list(items.filter(is_archived=False).exclude(status=Submission.Status.SUBMITTED))
    submitted_items = list(
        items.filter(is_archived=False, status=Submission.Status.SUBMITTED).order_by("-submitted_at", "-updated_at")
    )
    archived_items = list(items.filter(is_archived=True).order_by("-archived_at", "-updated_at"))
    history_items = list(items.order_by("-updated_at", "-created_at")[:120])

    normalized_ids = set()
    for item in active_items + submitted_items + archived_items + history_items:
        if item.id in normalized_ids:
            continue
        _normalize_submission_record(item)
        normalized_ids.add(item.id)

    def _is_overdue(submission):
        if submission.status == Submission.Status.LATE:
            return True
        return bool(submission.due_date and submission.due_date < today)

    overdue_items = [item for item in active_items if _is_overdue(item)]
    due_today_items = [
        item
        for item in active_items
        if not _is_overdue(item) and item.due_date and item.due_date == today
    ]
    due_week_items = [
        item
        for item in active_items
        if not _is_overdue(item) and item.due_date and today < item.due_date <= week_cutoff
    ]
    active_regular_items = [
        item
        for item in active_items
        if not _is_overdue(item) and item.due_date and item.due_date > week_cutoff
    ]
    unscheduled_items = [
        item
        for item in active_items
        if not _is_overdue(item) and not item.due_date
    ]

    overdue_items.sort(key=lambda item: (item.due_date or date.min, item.client.name))
    due_today_items.sort(key=lambda item: (item.due_date, item.client.name))
    due_week_items.sort(key=lambda item: (item.due_date, item.client.name))
    active_regular_items.sort(key=lambda item: (item.due_date, item.client.name))
    unscheduled_items.sort(key=lambda item: item.client.name)
    year_choices = (
        items_for_filters.exclude(period_year__isnull=True)
        .values_list("period_year", flat=True)
        .distinct()
        .order_by("-period_year")
    )
    month_choices = [(month, f"{month:02d}") for month in range(1, 13)]
    obligation_choices = Obligation.objects.filter(is_active=True).order_by("name")

    return render(
        request,
        "app/submission_list.html",
        {
            "active_items": active_items,
            "due_today_items": due_today_items,
            "due_week_items": due_week_items,
            "active_regular_items": active_regular_items,
            "overdue_items": overdue_items,
            "unscheduled_items": unscheduled_items,
            "submitted_items": submitted_items,
            "archived_items": archived_items,
            "history_items": history_items,
            "scope": scope,
            "q": q,
            "status_filter": status_filter,
            "obligation_filter": obligation_filter,
            "period_year_filter": period_year_filter,
            "period_month_filter": period_month_filter,
            "status_choices": Submission.Status.choices,
            "obligation_choices": obligation_choices,
            "year_choices": year_choices,
            "month_choices": month_choices,
            "today": today,
            "week_cutoff": week_cutoff,
        },
    )


@login_required
def app_submission_create(request):
    initial = {}
    client_id = request.GET.get("client")
    if client_id:
        initial["client"] = client_id

    if request.method == "POST":
        form = SubmissionForm(request.POST)
        if not _is_manager(request.user):
            form.fields["client"].queryset = Client.objects.filter(is_deleted=False, responsible_id=request.user.id)
        if form.is_valid():
            forbidden = _client_access_required(request, form.cleaned_data["client"])
            if forbidden:
                return forbidden
            item = form.save(commit=False)
            item.created_by = request.user
            _normalize_submission_record(item)
            item.save()
            log_model_event(
                actor=request.user,
                action="create_ui",
                instance=item,
                after_data=get_instance_snapshot(item),
            )
            _notify_submission_event(
                actor=request.user,
                item=item,
                message=f"Nueva obligación fiscal: {item.obligation_name_display}.",
                event_key="submission_created",
            )
            messages.success(request, "Obligación fiscal registrada.")
            return redirect(_next_or_default(request, f"/app/clients/{item.client_id}/"))
    else:
        form = SubmissionForm(initial=initial)
        if not _is_manager(request.user):
            form.fields["client"].queryset = Client.objects.filter(is_deleted=False, responsible_id=request.user.id)

    return render(
        request,
        "app/form_page.html",
        {
            "title": "Registrar obligación fiscal",
            "form": form,
            "back_url": "/app/submissions/",
        },
    )


@login_required
def app_submission_edit(request, submission_id):
    item = get_object_or_404(Submission, id=submission_id)
    forbidden = _client_access_required(request, item.client)
    if forbidden:
        return forbidden

    if request.method == "POST":
        before = get_instance_snapshot(item)
        form = SubmissionForm(request.POST, instance=item)
        if not _is_manager(request.user):
            form.fields["client"].queryset = Client.objects.filter(is_deleted=False, responsible_id=request.user.id)
        if form.is_valid():
            forbidden = _client_access_required(request, form.cleaned_data["client"])
            if forbidden:
                return forbidden
            updated = form.save(commit=False)
            _normalize_submission_record(updated)
            updated.save()
            log_model_event(
                actor=request.user,
                action="update_ui",
                instance=updated,
                before_data=before,
                after_data=get_instance_snapshot(updated),
            )
            _notify_submission_event(
                actor=request.user,
                item=updated,
                message=f"Obligación fiscal actualizada: {updated.obligation_name_display}.",
                event_key="submission_updated",
            )
            messages.success(request, "Obligación fiscal actualizada.")
            return redirect("app-client-detail", client_id=updated.client_id)
    else:
        form = SubmissionForm(instance=item)
        if not _is_manager(request.user):
            form.fields["client"].queryset = Client.objects.filter(is_deleted=False, responsible_id=request.user.id)

    return render(
        request,
        "app/form_page.html",
        {
            "title": "Editar obligación fiscal",
            "form": form,
            "back_url": f"/app/clients/{item.client_id}/",
        },
    )


@login_required
def app_submission_mark_submitted(request, submission_id):
    item = get_object_or_404(Submission, id=submission_id)
    forbidden = _client_access_required(request, item.client)
    if forbidden:
        return forbidden
    if request.method != "POST":
        return redirect("app-submission-list")
    if item.is_archived:
        messages.error(request, "No podés finalizar una obligación archivada. Reabrila primero.")
        return redirect(_next_or_default(request, "/app/submissions/"))

    before = get_instance_snapshot(item)
    item.status = Submission.Status.SUBMITTED
    if not item.submitted_at:
        item.submitted_at = timezone.localdate()
    item.save(update_fields=["status", "submitted_at", "updated_at"])
    log_model_event(
        actor=request.user,
        action="mark_submitted_ui",
        instance=item,
        before_data=before,
        after_data=get_instance_snapshot(item),
    )
    _notify_submission_event(
        actor=request.user,
        item=item,
        message=f"Obligación presentada: {item.obligation_name_display}.",
        event_key="submission_mark_submitted",
    )
    messages.success(request, "Obligación marcada como presentada.")
    return redirect(_next_or_default(request, "/app/submissions/"))


@login_required
def app_submission_reactivate(request, submission_id):
    item = get_object_or_404(Submission, id=submission_id)
    forbidden = _client_access_required(request, item.client)
    if forbidden:
        return forbidden
    if request.method != "POST":
        return redirect("app-submission-list")
    if item.is_archived:
        messages.error(request, "No podés reactivar una obligación archivada. Reabrila primero.")
        return redirect(_next_or_default(request, "/app/submissions/?scope=archived"))
    if item.status != Submission.Status.SUBMITTED:
        messages.error(request, "Solo obligaciones finalizadas pueden reactivarse.")
        return redirect(_next_or_default(request, "/app/submissions/?scope=active"))

    before = get_instance_snapshot(item)
    item.status = Submission.Status.PENDING
    item.submitted_at = None
    item.save(update_fields=["status", "submitted_at", "updated_at"])
    log_model_event(
        actor=request.user,
        action="reactivate_ui",
        instance=item,
        before_data=before,
        after_data=get_instance_snapshot(item),
    )
    messages.success(request, "Obligación reactivada y movida a Activas.")
    return redirect(_next_or_default(request, "/app/submissions/?scope=active"))


@login_required
def app_submission_archive(request, submission_id):
    item = get_object_or_404(Submission, id=submission_id)
    forbidden = _client_access_required(request, item.client)
    if forbidden:
        return forbidden
    if request.method != "POST":
        return redirect("app-submission-list")
    if item.status != Submission.Status.SUBMITTED:
        messages.error(request, "Solo obligaciones finalizadas pueden archivarse.")
        return redirect(_next_or_default(request, "/app/submissions/"))

    before = get_instance_snapshot(item)
    item.is_archived = True
    item.archived_at = timezone.now()
    item.archived_by = request.user
    item.save(update_fields=["is_archived", "archived_at", "archived_by", "updated_at"])
    log_model_event(
        actor=request.user,
        action="archive_ui",
        instance=item,
        before_data=before,
        after_data=get_instance_snapshot(item),
    )
    messages.success(request, "Obligación fiscal archivada.")
    return redirect(_next_or_default(request, "/app/submissions/"))


@login_required
def app_submission_reopen(request, submission_id):
    item = get_object_or_404(Submission, id=submission_id)
    forbidden = _client_access_required(request, item.client)
    if forbidden:
        return forbidden
    if request.method != "POST":
        return redirect("app-submission-list")

    before = get_instance_snapshot(item)
    item.is_archived = False
    item.archived_at = None
    item.archived_by = None
    item.save(update_fields=["is_archived", "archived_at", "archived_by", "updated_at"])
    log_model_event(
        actor=request.user,
        action="reopen_ui",
        instance=item,
        before_data=before,
        after_data=get_instance_snapshot(item),
    )
    messages.success(request, "Obligación fiscal reabierta.")
    return redirect(_next_or_default(request, "/app/submissions/"))


@login_required
def app_tax_commitment_list(request):
    items_qs = TaxCommitment.objects.select_related("client", "created_by", "notified_by", "paid_by").all()
    today = timezone.localdate()
    week_cutoff = today + timedelta(days=7)
    is_funcionario = request.user.role == User.Role.FUNCIONARIO
    mine_filter = request.GET.get("mine", "")
    responsible_filter = request.GET.get("responsible", "")

    if is_funcionario:
        items_qs = items_qs.filter(client__responsible_id=request.user.id)
        mine_filter = "1"
    else:
        if responsible_filter:
            items_qs = items_qs.filter(client__responsible_id=responsible_filter)
        elif mine_filter == "1":
            items_qs = items_qs.filter(client__responsible_id=request.user.id)

    q = request.GET.get("q", "").strip()
    if q:
        items_qs = items_qs.filter(
            Q(client__name__icontains=q)
            | Q(client__ruc__icontains=q)
            | Q(reference_number__icontains=q)
            | Q(period_reference__icontains=q)
            | Q(type_other__icontains=q)
            | Q(notes__icontains=q)
        )

    commitment_type_filter = request.GET.get("commitment_type", "").strip()
    if commitment_type_filter:
        items_qs = items_qs.filter(commitment_type=commitment_type_filter)

    all_items = list(items_qs.order_by("due_date", "client__name", "id"))

    def _group_key(item):
        if item.installment_group_id:
            return str(item.installment_group_id)
        return f"single-{item.id}"

    for item in all_items:
        item.group_key = _group_key(item)
        if item.effective_status == TaxCommitment.Status.ARCHIVED:
            item.effective_badge = "archived"
        elif item.effective_status == TaxCommitment.Status.PAID:
            item.effective_badge = "paid"
        elif item.effective_status == "OVERDUE":
            item.effective_badge = "overdue"
        elif item.effective_status == TaxCommitment.Status.NOTIFIED:
            item.effective_badge = "notified"
        else:
            item.effective_badge = "pending"
        item.can_notify = item.status in {TaxCommitment.Status.PENDING, TaxCommitment.Status.NOTIFIED}
        item.can_mark_paid = item.status in {TaxCommitment.Status.PENDING, TaxCommitment.Status.NOTIFIED}
        item.can_archive = item.status == TaxCommitment.Status.PAID
        item.installment_label = (
            f"{item.installment_number}/{item.installment_total}"
            if item.installment_number and item.installment_total
            else "Unica"
        )

    status_filter_raw = (request.GET.get("status") or "").strip().upper()
    status_filter = status_filter_raw or "ACTIVOS"
    due_scope = (request.GET.get("due_scope") or "").strip().lower()

    def _matches_status(item):
        if status_filter == "ALL":
            return True
        if status_filter == "ACTIVOS":
            return item.effective_status in {
                TaxCommitment.Status.PENDING,
                TaxCommitment.Status.NOTIFIED,
                "OVERDUE",
            }
        if status_filter == "PAID":
            return item.status == TaxCommitment.Status.PAID
        if status_filter == "ARCHIVED":
            return item.status == TaxCommitment.Status.ARCHIVED
        # Compatibilidad con filtros legacy por estado puntual.
        if status_filter == "OVERDUE":
            return item.effective_status == "OVERDUE"
        if status_filter in {
            TaxCommitment.Status.PENDING,
            TaxCommitment.Status.NOTIFIED,
            TaxCommitment.Status.PAID,
            TaxCommitment.Status.ARCHIVED,
        }:
            return item.status == status_filter
        return True

    def _matches_due_scope(item):
        if not due_scope:
            return True
        is_open = item.status not in {TaxCommitment.Status.PAID, TaxCommitment.Status.ARCHIVED}
        if due_scope == "today":
            return is_open and item.due_date == today
        if due_scope == "week":
            return is_open and today < item.due_date <= week_cutoff
        if due_scope == "overdue":
            return is_open and item.due_date < today
        return True

    matching_keys = {
        item.group_key
        for item in all_items
        if _matches_status(item) and _matches_due_scope(item)
    }
    items = [item for item in all_items if item.group_key in matching_keys]

    groups_map = {}
    groups_order = []
    for item in items:
        key = item.group_key
        if key not in groups_map:
            groups_map[key] = []
            groups_order.append(key)
        groups_map[key].append(item)

    groups = []
    for key in groups_order:
        group_items = sorted(
            groups_map[key],
            key=lambda row: (
                row.installment_number is None,
                row.installment_number or 9999,
                row.due_date,
                row.id,
            ),
        )
        representative = next((row for row in group_items if row.installment_number == 1), group_items[0])
        total_installments = len(group_items)
        paid_count = sum(1 for row in group_items if row.status == TaxCommitment.Status.PAID)
        has_pending = any(row.status == TaxCommitment.Status.PENDING for row in group_items)
        has_notified = any(row.status == TaxCommitment.Status.NOTIFIED for row in group_items)
        has_overdue = any(row.effective_status == "OVERDUE" for row in group_items)
        all_paid = all(row.status == TaxCommitment.Status.PAID for row in group_items)
        all_archived = all(row.status == TaxCommitment.Status.ARCHIVED for row in group_items)
        has_open = any(row.status not in {TaxCommitment.Status.PAID, TaxCommitment.Status.ARCHIVED} for row in group_items)

        if has_overdue:
            general_status = "OVERDUE"
            general_label = "Vencido"
            general_badge = "overdue"
        elif has_pending:
            general_status = TaxCommitment.Status.PENDING
            general_label = "Pendiente"
            general_badge = "pending"
        elif has_notified:
            general_status = TaxCommitment.Status.NOTIFIED
            general_label = "Avisado"
            general_badge = "notified"
        elif all_archived:
            general_status = TaxCommitment.Status.ARCHIVED
            general_label = "Archivado"
            general_badge = "archived"
        elif all_paid or not has_open:
            general_status = TaxCommitment.Status.PAID
            general_label = "Pagado"
            general_badge = "paid"
        else:
            general_status = TaxCommitment.Status.PENDING
            general_label = "Pendiente"
            general_badge = "pending"

        open_items = [row for row in group_items if row.status not in {TaxCommitment.Status.PAID, TaxCommitment.Status.ARCHIVED}]
        next_due_date = min([row.due_date for row in open_items], default=None)
        total_amount = sum((row.amount for row in group_items), Decimal("0"))
        has_installments = total_installments > 1
        if total_installments > 1:
            if paid_count > 0:
                cuota_context = f"{paid_count}/{total_installments} pagadas"
            else:
                cuota_context = f"{total_installments} cuotas"
        else:
            cuota_context = "1 cuota"

        toggle_label = f"{total_installments} cuotas" if total_installments > 1 else "1 cuota"
        group_dom_id = f"tax-group-{key}".replace(" ", "-")
        group_actions_item = representative
        notify_target = next((row for row in group_items if row.can_notify), None)
        mark_paid_target = next((row for row in group_items if row.can_mark_paid), None)
        archive_target = next((row for row in group_items if row.can_archive), None)
        can_archive_group = (
            has_installments
            and all(row.status in {TaxCommitment.Status.PAID, TaxCommitment.Status.ARCHIVED} for row in group_items)
            and any(row.status == TaxCommitment.Status.PAID for row in group_items)
            and representative.installment_group_id is not None
        )
        groups.append(
            {
                "key": key,
                "dom_id": group_dom_id,
                "representative": representative,
                "client": representative.client,
                "client_id": representative.client_id,
                "type_display": representative.type_display,
                "reference_number": representative.reference_number,
                "period_reference": representative.period_reference,
                "origin_display": representative.get_source_display(),
                "currency": representative.currency,
                "cuota_context": cuota_context,
                "toggle_label": toggle_label,
                "next_due_date": next_due_date,
                "total_amount": total_amount,
                "general_status": general_status,
                "general_label": general_label,
                "general_badge": general_badge,
                "has_installments": has_installments,
                "group_action_item": group_actions_item,
                "group_can_notify": bool(notify_target),
                "group_can_mark_paid": bool(mark_paid_target),
                "group_can_archive": bool(archive_target),
                "group_can_archive_group": can_archive_group,
                "group_archive_group_id": str(representative.installment_group_id) if representative.installment_group_id else "",
                "group_notify_target": notify_target,
                "group_mark_paid_target": mark_paid_target,
                "group_archive_target": archive_target,
                "items": group_items,
            }
        )

    status_choices = [
        ("ACTIVOS", "Activos"),
        ("PAID", "Pagados"),
        ("ARCHIVED", "Archivados"),
        ("ALL", "Todos"),
    ]
    due_scope_choices = [
        ("", "Todos"),
        ("today", "Vence hoy"),
        ("week", "Esta semana"),
        ("overdue", "Vencidos"),
    ]

    context = {
        "items": items,
        "groups": groups,
        "q": q,
        "commitment_type_filter": commitment_type_filter,
        "status_filter": status_filter,
        "due_scope": due_scope,
        "status_choices": status_choices,
        "due_scope_choices": due_scope_choices,
        "commitment_type_choices": TaxCommitment.CommitmentType.choices,
        "mine_filter": mine_filter,
        "responsible_filter": responsible_filter,
        "responsible_choices": User.objects.filter(role=User.Role.FUNCIONARIO).order_by("username"),
        "is_funcionario": is_funcionario,
    }
    return render(request, "app/tax_commitment_list.html", context)


@login_required
def app_tax_commitment_create(request):
    initial = {}
    client_id = request.GET.get("client")
    if client_id:
        initial["client"] = client_id

    if request.method == "POST":
        if not _is_manager(request.user):
            requested_client_id = request.POST.get("client")
            if requested_client_id:
                requested_client = Client.objects.filter(id=requested_client_id, is_deleted=False).first()
                if requested_client and requested_client.responsible_id != request.user.id:
                    return HttpResponseForbidden("No tenés permisos para este cliente.")
        form = TaxCommitmentForm(request.POST, user=request.user)
        if form.is_valid():
            forbidden = _client_access_required(request, form.cleaned_data["client"])
            if forbidden:
                return forbidden

            if form.should_generate_installments():
                installment_rows = form.build_installment_data()
                group_id = TaxCommitment.new_installment_group_id()
                created_count = 0
                first_item = None
                for row in installment_rows:
                    item = TaxCommitment.objects.create(
                        client=form.cleaned_data["client"],
                        commitment_type=form.cleaned_data["commitment_type"],
                        installment_mode=form.cleaned_data["installment_mode"],
                        type_other=form.cleaned_data.get("type_other", ""),
                        reference_number=form.cleaned_data.get("reference_number", ""),
                        period_reference=form.cleaned_data.get("period_reference", ""),
                        installment_group_id=group_id,
                        installment_number=row["installment_number"],
                        installment_total=row["installment_total"],
                        due_date=row["due_date"],
                        amount=row["amount"],
                        currency=form.cleaned_data["currency"],
                        status=TaxCommitment.Status.PENDING,
                        source=TaxCommitment.Source.MANUAL,
                        notes=form.cleaned_data.get("notes", ""),
                        created_by=request.user,
                    )
                    created_count += 1
                    if first_item is None:
                        first_item = item
                    log_model_event(
                        actor=request.user,
                        action="create_ui_installment",
                        instance=item,
                        after_data=get_instance_snapshot(item),
                        metadata={"installment_group_id": str(group_id)},
                    )
                if first_item:
                    _notify_tax_commitment_event(
                        actor=request.user,
                        item=first_item,
                        message=f"Nuevo compromiso tributario en cuotas ({created_count}).",
                        event_key="tax_commitment_created_group",
                        severity=UserNotification.Severity.NORMAL,
                    )
                messages.success(request, f"Se generaron {created_count} cuotas del compromiso tributario.")
                return redirect(_next_or_default(request, "/app/tax-commitments/"))

            item = form.save(commit=False)
            item.status = TaxCommitment.Status.PENDING
            item.source = TaxCommitment.Source.MANUAL
            item.created_by = request.user
            item.save()
            log_model_event(
                actor=request.user,
                action="create_ui",
                instance=item,
                after_data=get_instance_snapshot(item),
            )
            _notify_tax_commitment_event(
                actor=request.user,
                item=item,
                message=f"Nuevo compromiso tributario: {item.type_display}.",
                event_key="tax_commitment_created",
                severity=UserNotification.Severity.NORMAL,
            )
            messages.success(request, "Compromiso tributario creado.")
            return redirect(_next_or_default(request, "/app/tax-commitments/"))
    else:
        form = TaxCommitmentForm(initial=initial, user=request.user)

    return render(
        request,
        "app/form_page.html",
        {
            "title": "Nuevo compromiso tributario",
            "form": form,
            "back_url": "/app/tax-commitments/",
            "enable_tax_commitment_helper": True,
            "tax_manual_amount_values": form.get_manual_amounts_for_render() if hasattr(form, "get_manual_amounts_for_render") else [],
            "tax_manual_due_values": form.get_manual_due_dates_for_render() if hasattr(form, "get_manual_due_dates_for_render") else [],
        },
    )


@login_required
def app_tax_commitment_edit(request, commitment_id):
    item = get_object_or_404(TaxCommitment.objects.select_related("client"), id=commitment_id)
    forbidden = _client_access_required(request, item.client)
    if forbidden:
        return forbidden

    if request.method == "POST":
        before = get_instance_snapshot(item)
        form = TaxCommitmentForm(request.POST, instance=item, user=request.user)
        if form.is_valid():
            forbidden = _client_access_required(request, form.cleaned_data["client"])
            if forbidden:
                return forbidden

            updated = form.save(commit=False)
            updated.save()
            log_model_event(
                actor=request.user,
                action="update_ui",
                instance=updated,
                before_data=before,
                after_data=get_instance_snapshot(updated),
            )
            _notify_tax_commitment_event(
                actor=request.user,
                item=updated,
                message=f"Compromiso tributario actualizado: {updated.type_display}.",
                event_key="tax_commitment_updated",
                severity=UserNotification.Severity.INFO,
            )
            messages.success(request, "Compromiso tributario actualizado.")
            return redirect("app-tax-commitment-list")
    else:
        form = TaxCommitmentForm(instance=item, user=request.user)

    return render(
        request,
        "app/form_page.html",
        {
            "title": "Editar compromiso tributario",
            "form": form,
            "back_url": "/app/tax-commitments/",
            "enable_tax_commitment_helper": True,
            "tax_manual_amount_values": form.get_manual_amounts_for_render() if hasattr(form, "get_manual_amounts_for_render") else [],
            "tax_manual_due_values": form.get_manual_due_dates_for_render() if hasattr(form, "get_manual_due_dates_for_render") else [],
        },
    )


@login_required
def app_tax_commitment_notify(request, commitment_id):
    item = get_object_or_404(TaxCommitment.objects.select_related("client"), id=commitment_id)
    forbidden = _client_access_required(request, item.client)
    if forbidden:
        return forbidden
    if request.method != "POST":
        return redirect("app-tax-commitment-list")
    if item.status in {TaxCommitment.Status.PAID, TaxCommitment.Status.ARCHIVED}:
        messages.error(request, "No se puede avisar un compromiso pagado o archivado.")
        return redirect(_next_or_default(request, "/app/tax-commitments/"))

    before = get_instance_snapshot(item)
    item.status = TaxCommitment.Status.NOTIFIED
    item.notified_at = timezone.now()
    item.notified_by = request.user
    item.save(update_fields=["status", "notified_at", "notified_by", "updated_at"])
    log_model_event(
        actor=request.user,
        action="notify_ui",
        instance=item,
        before_data=before,
        after_data=get_instance_snapshot(item),
    )
    _notify_tax_commitment_event(
        actor=request.user,
        item=item,
        message=f"Compromiso tributario avisado: {item.type_display}.",
        event_key="tax_commitment_notified",
        severity=UserNotification.Severity.INFO,
    )
    messages.success(request, "Compromiso marcado como avisado.")
    return redirect(_next_or_default(request, "/app/tax-commitments/"))


@login_required
def app_tax_commitment_mark_paid(request, commitment_id):
    item = get_object_or_404(TaxCommitment.objects.select_related("client"), id=commitment_id)
    forbidden = _client_access_required(request, item.client)
    if forbidden:
        return forbidden
    if request.method != "POST":
        return redirect("app-tax-commitment-list")
    if item.status == TaxCommitment.Status.ARCHIVED:
        messages.error(request, "No se puede marcar pagado un compromiso archivado.")
        return redirect(_next_or_default(request, "/app/tax-commitments/"))

    before = get_instance_snapshot(item)
    item.status = TaxCommitment.Status.PAID
    item.paid_at = timezone.now()
    item.paid_by = request.user
    item.save(update_fields=["status", "paid_at", "paid_by", "updated_at"])
    log_model_event(
        actor=request.user,
        action="mark_paid_ui",
        instance=item,
        before_data=before,
        after_data=get_instance_snapshot(item),
    )
    _notify_tax_commitment_event(
        actor=request.user,
        item=item,
        message=f"Compromiso tributario pagado: {item.type_display}.",
        event_key="tax_commitment_paid",
        severity=UserNotification.Severity.INFO,
    )
    messages.success(request, "Compromiso marcado como pagado.")
    return redirect(_next_or_default(request, "/app/tax-commitments/"))


@login_required
def app_tax_commitment_archive(request, commitment_id):
    item = get_object_or_404(TaxCommitment.objects.select_related("client"), id=commitment_id)
    forbidden = _client_access_required(request, item.client)
    if forbidden:
        return forbidden
    if request.method != "POST":
        return redirect("app-tax-commitment-list")
    if item.status != TaxCommitment.Status.PAID:
        messages.error(request, "Solo compromisos pagados pueden archivarse.")
        return redirect(_next_or_default(request, "/app/tax-commitments/"))

    before = get_instance_snapshot(item)
    item.status = TaxCommitment.Status.ARCHIVED
    item.save(update_fields=["status", "updated_at"])
    log_model_event(
        actor=request.user,
        action="archive_ui",
        instance=item,
        before_data=before,
        after_data=get_instance_snapshot(item),
    )
    messages.success(request, "Compromiso archivado.")
    return redirect(_next_or_default(request, "/app/tax-commitments/"))


@login_required
def app_tax_commitment_archive_group(request, group_id):
    if request.method != "POST":
        return redirect("app-tax-commitment-list")

    items = list(
        TaxCommitment.objects.select_related("client")
        .filter(installment_group_id=group_id)
        .order_by("installment_number", "id")
    )
    if not items:
        messages.error(request, "No se encontró el grupo de cuotas indicado.")
        return redirect(_next_or_default(request, "/app/tax-commitments/"))

    forbidden = _client_access_required(request, items[0].client)
    if forbidden:
        return forbidden

    if any(item.status not in {TaxCommitment.Status.PAID, TaxCommitment.Status.ARCHIVED} for item in items):
        messages.error(request, "Solo se puede archivar el grupo cuando todas las cuotas están pagadas.")
        return redirect(_next_or_default(request, "/app/tax-commitments/"))

    archived_count = 0
    for item in items:
        if item.status == TaxCommitment.Status.ARCHIVED:
            continue
        before = get_instance_snapshot(item)
        item.status = TaxCommitment.Status.ARCHIVED
        item.save(update_fields=["status", "updated_at"])
        archived_count += 1
        log_model_event(
            actor=request.user,
            action="archive_group_ui",
            instance=item,
            before_data=before,
            after_data=get_instance_snapshot(item),
            metadata={"installment_group_id": str(group_id)},
        )

    if archived_count:
        messages.success(request, f"Se archivaron {archived_count} cuota(s) del grupo.")
    else:
        messages.info(request, "El grupo ya estaba archivado.")
    return redirect(_next_or_default(request, "/app/tax-commitments/"))


@login_required
def app_charge_list(request):
    forbidden = _manager_required(request)
    if forbidden:
        return forbidden

    items = Charge.objects.select_related("client", "contract").all()

    q = request.GET.get("q", "").strip()
    if q:
        items = items.filter(Q(client__name__icontains=q) | Q(client__ruc__icontains=q))

    status_filter = request.GET.get("status", "")
    if status_filter:
        items = items.filter(status=status_filter)

    payment_type_filter = request.GET.get("payment_type", "")
    if payment_type_filter:
        items = items.filter(payment_type=payment_type_filter)

    debt_total = items.filter(status=Charge.Status.PENDING).aggregate(total=Sum("debt_amount"))["total"] or Decimal("0")
    summary_rows = items.values("payment_type", "status").annotate(
        count=Count("id"),
        amount_total=Sum("amount"),
        debt_total=Sum("debt_amount"),
    )
    summary_by_type = {
        key: {
            "label": label,
            "pending_count": 0,
            "pending_amount": Decimal("0"),
            "paid_count": 0,
            "paid_amount": Decimal("0"),
        }
        for key, label in Charge.PaymentType.choices
    }
    summary_by_status = {
        Charge.Status.PENDING: {"label": "Pendientes", "count": 0, "amount": Decimal("0")},
        Charge.Status.PAID: {"label": "Pagados", "count": 0, "amount": Decimal("0")},
    }
    for row in summary_rows:
        payment_type = row["payment_type"]
        status = row["status"]
        count = row["count"] or 0
        amount_total = row["amount_total"] or Decimal("0")
        debt_amount = row["debt_total"] or Decimal("0")

        if payment_type in summary_by_type:
            if status == Charge.Status.PAID:
                summary_by_type[payment_type]["paid_count"] += count
                summary_by_type[payment_type]["paid_amount"] += amount_total
            else:
                summary_by_type[payment_type]["pending_count"] += count
                summary_by_type[payment_type]["pending_amount"] += debt_amount or amount_total

        if status in summary_by_status:
            summary_by_status[status]["count"] += count
            summary_by_status[status]["amount"] += amount_total

    return render(
        request,
        "app/charge_list.html",
        {
            "items": items,
            "q": q,
            "status_filter": status_filter,
            "payment_type_filter": payment_type_filter,
            "status_choices": Charge.Status.choices,
            "payment_type_choices": Charge.PaymentType.choices,
            "debt_total": debt_total,
            "summary_by_type": summary_by_type,
            "pending_summary": summary_by_status[Charge.Status.PENDING],
            "paid_summary": summary_by_status[Charge.Status.PAID],
        },
    )


@login_required
def app_charge_create(request):
    forbidden = _manager_required(request)
    if forbidden:
        return forbidden

    initial = {}
    client_id = request.GET.get("client")
    if client_id:
        initial["client"] = client_id

    if request.method == "POST":
        form = ChargeForm(request.POST)
        if form.is_valid():
            charge = form.save(commit=False)
            if charge.status == Charge.Status.PAID:
                charge.paid_at = timezone.now()
                charge.debt_amount = Decimal("0")
            elif charge.debt_amount <= 0:
                charge.debt_amount = charge.amount
            charge.save()
            sync_client_billing_snapshot(charge.client)
            log_model_event(
                actor=request.user,
                action="create_ui",
                instance=charge,
                after_data=get_instance_snapshot(charge),
            )
            messages.success(request, "Cobro registrado.")
            return redirect(_next_or_default(request, f"/app/clients/{charge.client_id}/"))
    else:
        form = ChargeForm(initial=initial)

    return render(
        request,
        "app/form_page.html",
        {
            "title": "Registrar cobro",
            "form": form,
            "back_url": "/app/charges/",
        },
    )


@login_required
def app_charge_edit(request, charge_id):
    forbidden = _manager_required(request)
    if forbidden:
        return forbidden

    charge = get_object_or_404(Charge, id=charge_id)

    if request.method == "POST":
        before = get_instance_snapshot(charge)
        form = ChargeForm(request.POST, instance=charge)
        if form.is_valid():
            updated = form.save(commit=False)
            if updated.status == Charge.Status.PAID:
                updated.paid_at = updated.paid_at or timezone.now()
                updated.debt_amount = Decimal("0")
            elif updated.debt_amount <= 0:
                updated.debt_amount = updated.amount
            updated.save()
            sync_client_billing_snapshot(updated.client)
            log_model_event(
                actor=request.user,
                action="update_ui",
                instance=updated,
                before_data=before,
                after_data=get_instance_snapshot(updated),
            )
            messages.success(request, "Cobro actualizado.")
            return redirect("app-client-detail", client_id=updated.client_id)
    else:
        form = ChargeForm(instance=charge)

    return render(
        request,
        "app/form_page.html",
        {
            "title": "Editar cobro",
            "form": form,
            "back_url": f"/app/clients/{charge.client_id}/",
        },
    )


@login_required
def app_charge_mark_paid(request, charge_id):
    forbidden = _manager_required(request)
    if forbidden:
        return forbidden

    charge = get_object_or_404(Charge, id=charge_id)
    if request.method != "POST":
        return redirect("app-client-detail", client_id=charge.client_id)

    before = get_instance_snapshot(charge)
    charge.status = Charge.Status.PAID
    charge.paid_at = timezone.now()
    charge.debt_amount = Decimal("0")
    charge.save(update_fields=["status", "paid_at", "debt_amount", "updated_at"])
    sync_client_billing_snapshot(charge.client)

    log_model_event(
        actor=request.user,
        action="mark_paid_ui",
        instance=charge,
        before_data=before,
        after_data=get_instance_snapshot(charge),
    )
    messages.success(request, "Cobro marcado como pagado.")
    return redirect("app-client-detail", client_id=charge.client_id)


@login_required
def app_contract_list(request):
    forbidden = _manager_required(request)
    if forbidden:
        return forbidden

    items = Contract.objects.select_related("client").all()
    today = timezone.localdate()
    soon_cutoff = today + timedelta(days=15)

    q = request.GET.get("q", "").strip()
    if q:
        items = items.filter(Q(client__name__icontains=q) | Q(client__ruc__icontains=q))

    active = request.GET.get("active", "")
    if active == "1":
        items = items.filter(active=True)
    elif active == "0":
        items = items.filter(active=False)

    return render(
        request,
        "app/contract_list.html",
        {
            "items": items,
            "q": q,
            "active_filter": active,
            "can_manage_contracts": _is_manager(request.user),
            "today": today,
            "soon_cutoff": soon_cutoff,
        },
    )


@login_required
def app_contract_create(request):
    forbidden = _manager_required(request)
    if forbidden:
        return forbidden

    initial = {}
    client_id = request.GET.get("client")
    if client_id:
        initial["client"] = client_id

    if request.method == "POST":
        form = ContractForm(request.POST)
        if form.is_valid():
            contract = form.save()
            if contract.end_date:
                contract.client.contract_until = contract.end_date
                contract.client.save(update_fields=["contract_until", "updated_at"])
            log_model_event(
                actor=request.user,
                action="create_ui",
                instance=contract,
                after_data=get_instance_snapshot(contract),
            )
            messages.success(request, "Contrato registrado.")
            return redirect("app-client-detail", client_id=contract.client_id)
    else:
        form = ContractForm(initial=initial)

    return render(
        request,
        "app/form_page.html",
        {
            "title": "Nuevo contrato",
            "form": form,
            "back_url": "/app/contracts/",
        },
    )


@login_required
def app_contract_edit(request, contract_id):
    forbidden = _manager_required(request)
    if forbidden:
        return forbidden

    contract = get_object_or_404(Contract, id=contract_id)

    if request.method == "POST":
        before = get_instance_snapshot(contract)
        form = ContractForm(request.POST, instance=contract)
        if form.is_valid():
            updated = form.save()
            if updated.end_date:
                updated.client.contract_until = updated.end_date
                updated.client.save(update_fields=["contract_until", "updated_at"])
            log_model_event(
                actor=request.user,
                action="update_ui",
                instance=updated,
                before_data=before,
                after_data=get_instance_snapshot(updated),
            )
            messages.success(request, "Contrato actualizado.")
            return redirect("app-client-detail", client_id=updated.client_id)
    else:
        form = ContractForm(instance=contract)

    return render(
        request,
        "app/form_page.html",
        {
            "title": "Editar contrato",
            "form": form,
            "back_url": f"/app/clients/{contract.client_id}/",
        },
    )


@login_required
def app_bank_list(request):
    items = BankRequest.objects.select_related(
        "client",
        "requested_by",
        "responsible",
        "last_note_by",
        "receipts_notified_by",
        "receipts_pending_item",
    )
    if not _is_manager(request.user):
        items = items.filter(responsible_id=request.user.id)

    q = request.GET.get("q", "").strip()
    if q:
        items = items.filter(
            Q(client__name__icontains=q)
            | Q(client__ruc__icontains=q)
            | Q(request_type_other__icontains=q)
            | Q(last_note__icontains=q)
            | Q(notes__icontains=q)
        )

    request_type_filter = request.GET.get("request_type", "")
    if request_type_filter:
        items = items.filter(request_type=request_type_filter)

    responsible_filter = request.GET.get("responsible", "")
    if responsible_filter and _is_manager(request.user):
        items = items.filter(responsible_id=responsible_filter)

    receipts_status_filter = request.GET.get("receipts_status", "")
    if receipts_status_filter:
        items = items.filter(receipts_status=receipts_status_filter)

    focus_id_raw = request.GET.get("focus", "").strip()
    focus_id = int(focus_id_raw) if focus_id_raw.isdigit() else None

    scope_param = request.GET.get("scope", "").strip()
    if scope_param in {"active", "completed", "archived", "all"}:
        scope = scope_param
    else:
        scope = "active"
        if focus_id:
            focus_target = items.filter(id=focus_id).only("status").first()
            if focus_target:
                scope = _bank_scope_for_status(focus_target.status)

    active_items = items.filter(status__in=[BankRequest.Status.REQUESTED, BankRequest.Status.IN_PROGRESS]).order_by(
        "-updated_at"
    )
    completed_items = items.filter(status=BankRequest.Status.COMPLETED).order_by("-completed_at", "-updated_at")
    archived_items = items.filter(status=BankRequest.Status.ARCHIVED).order_by("-archived_at", "-updated_at")

    return render(
        request,
        "app/bank_list.html",
        {
            "active_items": active_items,
            "completed_items": completed_items,
            "archived_items": archived_items,
            "scope": scope,
            "q": q,
            "request_type_filter": request_type_filter,
            "responsible_filter": responsible_filter,
            "receipts_status_filter": receipts_status_filter,
            "request_type_choices": BankRequest.RequestType.choices,
            "document_status_choices": BankRequest.DocumentStatus.choices,
            "responsible_choices": User.objects.filter(role=User.Role.FUNCIONARIO).order_by("username"),
            "active_count": active_items.count(),
            "completed_count": completed_items.count(),
            "archived_count": archived_items.count(),
            "can_manage_banks": _is_manager(request.user),
            "focus_id": focus_id,
        },
    )


@login_required
def app_bank_create(request):
    forbidden = _manager_required(request)
    if forbidden:
        return forbidden

    initial = {}
    client_id = request.GET.get("client")
    if client_id:
        initial["client"] = client_id
        linked_client = Client.objects.filter(id=client_id, is_deleted=False).first()
        if linked_client and linked_client.responsible_id:
            initial["responsible"] = linked_client.responsible_id

    if request.method == "POST":
        form = BankRequestForm(request.POST)
        if form.is_valid():
            item = form.save(commit=False)
            item.requested_by = request.user
            if not item.responsible_id and item.client and item.client.responsible_id:
                item.responsible_id = item.client.responsible_id
            item.save()
            log_model_event(
                actor=request.user,
                action="create_ui",
                instance=item,
                after_data=get_instance_snapshot(item),
            )
            _notify_bank_event(
                actor=request.user,
                item=item,
                message=f"Nueva solicitud de Bancos y recibos: {item.get_request_type_display()}.",
                event_key="bank_request_created",
                severity=UserNotification.Severity.NORMAL,
            )
            messages.success(request, "Solicitud de bancos y recibos creada.")
            return redirect(_next_or_default(request, "/app/banks/"))
    else:
        form = BankRequestForm(initial=initial)

    return render(
        request,
        "app/form_page.html",
        {
            "title": "Nueva solicitud de bancos y recibos",
            "form": form,
            "back_url": "/app/banks/",
        },
    )


@login_required
def app_bank_edit(request, request_id):
    forbidden = _manager_required(request)
    if forbidden:
        return forbidden

    item = get_object_or_404(BankRequest, id=request_id)
    if request.method == "POST":
        before = get_instance_snapshot(item)
        form = BankRequestForm(request.POST, instance=item)
        if form.is_valid():
            updated = form.save()
            log_model_event(
                actor=request.user,
                action="update_ui",
                instance=updated,
                before_data=before,
                after_data=get_instance_snapshot(updated),
            )
            _notify_bank_event(
                actor=request.user,
                item=updated,
                message="Solicitud de Bancos y recibos actualizada.",
                event_key="bank_request_updated",
                severity=UserNotification.Severity.INFO,
            )
            messages.success(request, "Solicitud de bancos y recibos actualizada.")
            return redirect(_next_or_default(request, "/app/banks/"))
    else:
        form = BankRequestForm(instance=item)

    return render(
        request,
        "app/form_page.html",
        {
            "title": "Editar solicitud de bancos y recibos",
            "form": form,
            "back_url": "/app/banks/",
        },
    )


@login_required
def app_bank_add_note(request, request_id):
    item = get_object_or_404(BankRequest, id=request_id)
    forbidden = _bank_access_required(request, item)
    if forbidden:
        return forbidden

    if request.method == "POST":
        before = get_instance_snapshot(item)
        form = BankRequestNoteForm(request.POST)
        if form.is_valid():
            note = form.cleaned_data["note"].strip()
            if note:
                now = timezone.now()
                item.last_note = note
                item.last_note_by = request.user
                item.last_note_at = now
                # Compatibilidad: mantener notes con el último valor visible.
                item.notes = note
                item.save(update_fields=["last_note", "last_note_by", "last_note_at", "notes", "updated_at"])
                log_model_event(
                    actor=request.user,
                    action="add_note_ui",
                    instance=item,
                    before_data=before,
                    after_data=get_instance_snapshot(item),
                )
                _notify_bank_event(
                    actor=request.user,
                    item=item,
                    message=f"Nueva observación en Bancos y recibos: {item.client.name}.",
                    event_key="bank_request_note",
                    severity=UserNotification.Severity.INFO,
                )
                messages.success(request, "Observación agregada.")
                return redirect(_next_or_default(request, "/app/banks/"))
            messages.error(request, "La observación no puede estar vacía.")
    else:
        form = BankRequestNoteForm()

    return render(
        request,
        "app/form_page.html",
        {
            "title": f"Observación - {item.client.name}",
            "form": form,
            "back_url": "/app/banks/",
        },
    )


@login_required
def app_bank_mark_receipts_loaded(request, request_id):
    item = get_object_or_404(BankRequest, id=request_id)
    forbidden = _bank_access_required(request, item)
    if forbidden:
        return forbidden
    if request.method != "POST":
        return redirect("app-bank-list")

    before = get_instance_snapshot(item)
    resolved_pending = mark_document_loaded(item=item, actor=request.user, document_kind="receipts")
    log_model_event(
        actor=request.user,
        action="mark_receipts_loaded_ui",
        instance=item,
        before_data=before,
        after_data=get_instance_snapshot(item),
        metadata={"resolved_pending_item_id": resolved_pending.id if resolved_pending else None},
    )
    if resolved_pending:
        log_model_event(
            actor=request.user,
            action="resolve_from_bank_request_ui",
            instance=resolved_pending,
            after_data=get_instance_snapshot(resolved_pending),
            metadata={"bank_request_id": item.id, "document_kind": "receipts"},
        )
    _notify_bank_event(
        actor=request.user,
        item=item,
        message="Recibos marcados como cargados en Bancos y recibos.",
        event_key="bank_request_receipts_loaded",
        severity=UserNotification.Severity.INFO,
    )
    messages.success(request, "Recibos marcados como cargados.")
    return redirect(_next_or_default(request, "/app/banks/"))


@login_required
def app_bank_mark_receipts_pending(request, request_id):
    item = get_object_or_404(BankRequest, id=request_id)
    forbidden = _bank_access_required(request, item)
    if forbidden:
        return forbidden
    if request.method != "POST":
        return redirect("app-bank-list")

    before = get_instance_snapshot(item)
    item.receipts_status = BankRequest.DocumentStatus.PENDING
    item.receipts_loaded_by = None
    item.receipts_loaded_at = None
    item.receipts_client_notified = False
    item.receipts_notified_by = None
    item.receipts_notified_at = None
    item.save(
        update_fields=[
            "receipts_status",
            "receipts_loaded_by",
            "receipts_loaded_at",
            "receipts_client_notified",
            "receipts_notified_by",
            "receipts_notified_at",
            "updated_at",
        ]
    )
    log_model_event(
        actor=request.user,
        action="mark_receipts_pending_ui",
        instance=item,
        before_data=before,
        after_data=get_instance_snapshot(item),
    )
    _notify_bank_event(
        actor=request.user,
        item=item,
        message="Recibos volvió a estado pendiente en Bancos y recibos.",
        event_key="bank_request_receipts_pending",
        severity=UserNotification.Severity.NORMAL,
    )
    messages.success(request, "Recibos volvió a estado Pendiente.")
    return redirect(_next_or_default(request, "/app/banks/"))


@login_required
def app_bank_mark_receipts_notified(request, request_id):
    item = get_object_or_404(BankRequest, id=request_id)
    forbidden = _bank_access_required(request, item)
    if forbidden:
        return forbidden
    if request.method != "POST":
        return redirect("app-bank-list")

    if item.receipts_status == BankRequest.DocumentStatus.LOADED:
        messages.error(request, "No corresponde marcar aviso cuando los recibos ya están cargados.")
        return redirect(_next_or_default(request, "/app/banks/"))
    if item.receipts_client_notified:
        messages.info(request, "El cliente ya estaba marcado como avisado.")
        return redirect(_next_or_default(request, "/app/banks/"))

    before = get_instance_snapshot(item)
    item.receipts_client_notified = True
    item.receipts_notified_by = request.user
    item.receipts_notified_at = timezone.now()
    item.save(update_fields=["receipts_client_notified", "receipts_notified_by", "receipts_notified_at", "updated_at"])

    log_model_event(
        actor=request.user,
        action="mark_receipts_notified_ui",
        instance=item,
        before_data=before,
        after_data=get_instance_snapshot(item),
    )
    _notify_bank_event(
        actor=request.user,
        item=item,
        message="Cliente avisado por recibos pendientes en Bancos y recibos.",
        event_key="bank_request_receipts_notified",
        severity=UserNotification.Severity.NORMAL,
    )
    messages.success(request, "Cliente marcado como avisado por recibos pendientes.")
    return redirect(_next_or_default(request, "/app/banks/"))


def _handle_bank_manager_status_action(request, item, *, action_label, callback, log_action):
    forbidden = _manager_required(request)
    if forbidden:
        return forbidden
    if request.method != "POST":
        return redirect("app-bank-list")

    before = get_instance_snapshot(item)
    try:
        callback()
    except DjangoValidationError as exc:
        message = exc.messages[0] if getattr(exc, "messages", None) else str(exc)
        messages.error(request, message)
        return redirect(_next_or_default(request, "/app/banks/"))

    log_model_event(
        actor=request.user,
        action=log_action,
        instance=item,
        before_data=before,
        after_data=get_instance_snapshot(item),
    )
    _notify_bank_event(
        actor=request.user,
        item=item,
        message=f"Solicitud de Bancos y recibos: {action_label}",
        event_key=f"bank_request_{log_action}",
        severity=UserNotification.Severity.NORMAL,
    )
    messages.success(request, action_label)
    return redirect(_next_or_default(request, "/app/banks/"))


@login_required
def app_bank_mark_in_progress(request, request_id):
    item = get_object_or_404(BankRequest, id=request_id)
    forbidden = _bank_access_required(request, item)
    if forbidden:
        return forbidden
    return _handle_bank_manager_status_action(
        request,
        item,
        action_label="Solicitud marcada En proceso.",
        callback=lambda: mark_in_progress(item=item, actor=request.user),
        log_action="mark_in_progress_ui",
    )


@login_required
def app_bank_mark_completed(request, request_id):
    item = get_object_or_404(BankRequest, id=request_id)
    forbidden = _bank_access_required(request, item)
    if forbidden:
        return forbidden
    return _handle_bank_manager_status_action(
        request,
        item,
        action_label="Solicitud marcada como Realizada.",
        callback=lambda: mark_completed(item=item, actor=request.user),
        log_action="mark_completed_ui",
    )


@login_required
def app_bank_archive(request, request_id):
    item = get_object_or_404(BankRequest, id=request_id)
    forbidden = _bank_access_required(request, item)
    if forbidden:
        return forbidden
    return _handle_bank_manager_status_action(
        request,
        item,
        action_label="Solicitud archivada.",
        callback=lambda: mark_archived(item=item, actor=request.user),
        log_action="archive_ui",
    )


@login_required
def app_bank_reopen(request, request_id):
    item = get_object_or_404(BankRequest, id=request_id)
    forbidden = _bank_access_required(request, item)
    if forbidden:
        return forbidden
    return _handle_bank_manager_status_action(
        request,
        item,
        action_label="Solicitud reabierta.",
        callback=lambda: reopen_archived(item=item),
        log_action="reopen_ui",
    )


@login_required
def app_bank_create_receipts_pending(request, request_id):
    item = get_object_or_404(BankRequest, id=request_id)
    forbidden = _bank_access_required(request, item)
    if forbidden:
        return forbidden
    forbidden = _manager_required(request)
    if forbidden:
        return forbidden
    if request.method != "POST":
        return redirect("app-bank-list")

    before_item = get_instance_snapshot(item)
    description = f"Bancos y recibos: {item.get_request_type_display()} - Recibos pendientes"
    missing = "Recibos pendientes de carga para continuar gestión en Bancos y recibos."
    priority_value = _normalize_pending_priority_value(request.POST.get("priority", ""))
    if not priority_value:
        priority_value = _pending_priority_for_bank_request(item)
    pending, created = create_or_link_document_pending(
        item=item,
        actor=request.user,
        document_kind="receipts",
        description=description,
        missing_documents=missing,
        priority=priority_value,
    )
    log_model_event(
        actor=request.user,
        action="create_receipts_pending_ui",
        instance=item,
        before_data=before_item,
        after_data=get_instance_snapshot(item),
        metadata={"pending_item_id": pending.id, "created": created, "priority": priority_value},
    )
    log_model_event(
        actor=request.user,
        action="create_ui" if created else "update_ui",
        instance=pending,
        after_data=get_instance_snapshot(pending),
        metadata={"source": "bank_request_ui", "document_kind": "receipts", "bank_request_id": item.id},
    )
    _notify_pending_created(actor=request.user, item=pending)
    if pending.priority == PendingItem.Priority.URGENT:
        messages.success(request, "Pendiente urgente de recibos creado.")
    else:
        messages.success(request, "Pendiente de recibos creado.")
    return redirect(_next_or_default(request, "/app/banks/"))


@login_required
def app_tax_commitment_installment_edit(request, commitment_id):
    """Edición rápida de una cuota individual: solo fecha, monto, estado y nota.
    No regenera cuotas ni toca la lógica de grupos.
    """
    item = get_object_or_404(TaxCommitment.objects.select_related("client"), id=commitment_id)
    forbidden = _client_access_required(request, item.client)
    if forbidden:
        return forbidden

    if request.method == "POST":
        before = get_instance_snapshot(item)
        form = TaxCommitmentInstallmentForm(request.POST, instance=item)
        if form.is_valid():
            updated = form.save()
            log_model_event(
                actor=request.user,
                action="update_ui",
                instance=updated,
                before_data=before,
                after_data=get_instance_snapshot(updated),
            )
            _notify_tax_commitment_event(
                actor=request.user,
                item=updated,
                message=f"Cuota {updated.installment_number or ''}/{updated.installment_total or ''} actualizada: {updated.type_display}.",
                event_key="tax_commitment_updated",
                severity=UserNotification.Severity.INFO,
            )
            messages.success(request, "Cuota actualizada correctamente.")
            return redirect(_next_or_default(request, "/app/tax-commitments/"))
    else:
        form = TaxCommitmentInstallmentForm(instance=item)

    installment_label = ""
    if item.installment_number and item.installment_total:
        installment_label = f" — Cuota {item.installment_number}/{item.installment_total}"

    return render(
        request,
        "app/form_page.html",
        {
            "title": f"Editar cuota{installment_label}: {item.client.name}",
            "form": form,
            "back_url": "/app/tax-commitments/",
        },
    )
