from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date

from accounts.models import User
from auditing.services import get_instance_snapshot, log_model_event
from clients.models import Client
from payment_logs.forms import PaymentReceptionLogForm
from payment_logs.models import PaymentReceptionLog


def _is_manager(user):
    return user.role in {User.Role.MASTER, User.Role.ADMIN}


def _client_queryset_for_user(user):
    queryset = Client.objects.filter(is_deleted=False)
    if _is_manager(user):
        return queryset
    return queryset.filter(responsible_id=user.id)


def _can_access_client(user, client):
    if _is_manager(user):
        return True
    return bool(client and client.responsible_id == user.id)


def _client_access_required(request, client):
    if _can_access_client(request.user, client):
        return None
    return HttpResponseForbidden("No tenés permisos para acceder a este cliente.")


def _next_or_default(request, default_url):
    next_url = (request.POST.get("next") or "").strip()
    if next_url.startswith("/"):
        return next_url
    return default_url


@login_required
def app_payment_log_list(request):
    items = PaymentReceptionLog.objects.select_related("client", "recorded_by", "archived_by")
    if not _is_manager(request.user):
        items = items.filter(client__responsible_id=request.user.id)

    q = request.GET.get("q", "").strip()
    if q:
        items = items.filter(
            Q(client__name__icontains=q)
            | Q(client__ruc__icontains=q)
            | Q(paid_by__icontains=q)
            | Q(observation__icontains=q)
        )

    concept_filter = request.GET.get("concept_type", "").strip()
    if concept_filter:
        items = items.filter(concept_type=concept_filter)

    method_filter = request.GET.get("payment_method", "").strip()
    if method_filter:
        items = items.filter(payment_method=method_filter)

    date_from_raw = request.GET.get("date_from", "").strip()
    date_to_raw = request.GET.get("date_to", "").strip()
    date_from = parse_date(date_from_raw) if date_from_raw else None
    date_to = parse_date(date_to_raw) if date_to_raw else None
    if date_from:
        items = items.filter(payment_date__gte=date_from)
    if date_to:
        items = items.filter(payment_date__lte=date_to)

    scope = (request.GET.get("scope") or "active").strip().lower()
    if scope == "archived":
        items = items.filter(is_archived=True)
    elif scope == "all":
        pass
    else:
        scope = "active"
        items = items.filter(is_archived=False)

    return render(
        request,
        "app/payment_log_list.html",
        {
            "items": items,
            "q": q,
            "scope": scope,
            "concept_filter": concept_filter,
            "method_filter": method_filter,
            "date_from": date_from_raw,
            "date_to": date_to_raw,
            "concept_choices": PaymentReceptionLog.ConceptType.choices,
            "method_choices": PaymentReceptionLog.PaymentMethod.choices,
            "can_manage_all": _is_manager(request.user),
        },
    )


@login_required
def app_payment_log_create(request):
    initial = {}
    client_id = request.GET.get("client")
    if client_id:
        initial["client"] = client_id

    if request.method == "POST":
        posted_client_id = request.POST.get("client")
        if posted_client_id:
            posted_client = Client.objects.filter(id=posted_client_id, is_deleted=False).first()
            if posted_client:
                forbidden = _client_access_required(request, posted_client)
                if forbidden:
                    return forbidden
        form = PaymentReceptionLogForm(request.POST)
        form.fields["client"].queryset = _client_queryset_for_user(request.user)
        if form.is_valid():
            forbidden = _client_access_required(request, form.cleaned_data["client"])
            if forbidden:
                return forbidden
            item = form.save(commit=False)
            item.recorded_by = request.user
            item.save()
            log_model_event(
                actor=request.user,
                action="create_ui",
                instance=item,
                after_data=get_instance_snapshot(item),
            )
            messages.success(request, "Recepción de pago registrada.")
            return redirect(_next_or_default(request, "/app/payment-logs/"))
    else:
        form = PaymentReceptionLogForm(initial=initial)
        form.fields["client"].queryset = _client_queryset_for_user(request.user)

    return render(
        request,
        "app/form_page.html",
        {
            "title": "Registrar recepción de pago",
            "form": form,
            "back_url": "/app/payment-logs/",
        },
    )


@login_required
def app_payment_log_edit(request, log_id):
    item = get_object_or_404(PaymentReceptionLog, id=log_id)
    forbidden = _client_access_required(request, item.client)
    if forbidden:
        return forbidden

    if request.method == "POST":
        posted_client_id = request.POST.get("client")
        if posted_client_id:
            posted_client = Client.objects.filter(id=posted_client_id, is_deleted=False).first()
            if posted_client:
                forbidden = _client_access_required(request, posted_client)
                if forbidden:
                    return forbidden
        before = get_instance_snapshot(item)
        form = PaymentReceptionLogForm(request.POST, instance=item)
        form.fields["client"].queryset = _client_queryset_for_user(request.user)
        if form.is_valid():
            forbidden = _client_access_required(request, form.cleaned_data["client"])
            if forbidden:
                return forbidden
            updated = form.save()
            log_model_event(
                actor=request.user,
                action="update_ui",
                instance=updated,
                before_data=before,
                after_data=get_instance_snapshot(updated),
            )
            messages.success(request, "Recepción de pago actualizada.")
            return redirect(_next_or_default(request, "/app/payment-logs/"))
    else:
        form = PaymentReceptionLogForm(instance=item)
        form.fields["client"].queryset = _client_queryset_for_user(request.user)

    return render(
        request,
        "app/form_page.html",
        {
            "title": "Editar recepción de pago",
            "form": form,
            "back_url": "/app/payment-logs/",
        },
    )


@login_required
def app_payment_log_archive(request, log_id):
    item = get_object_or_404(PaymentReceptionLog, id=log_id)
    forbidden = _client_access_required(request, item.client)
    if forbidden:
        return forbidden
    if request.method != "POST":
        return redirect("app-payment-log-list")

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
    messages.success(request, "Registro archivado.")
    return redirect(_next_or_default(request, "/app/payment-logs/"))


@login_required
def app_payment_log_unarchive(request, log_id):
    item = get_object_or_404(PaymentReceptionLog, id=log_id)
    forbidden = _client_access_required(request, item.client)
    if forbidden:
        return forbidden
    if request.method != "POST":
        return redirect("app-payment-log-list")

    before = get_instance_snapshot(item)
    item.is_archived = False
    item.archived_at = None
    item.archived_by = None
    item.save(update_fields=["is_archived", "archived_at", "archived_by", "updated_at"])
    log_model_event(
        actor=request.user,
        action="unarchive_ui",
        instance=item,
        before_data=before,
        after_data=get_instance_snapshot(item),
    )
    messages.success(request, "Registro reabierto.")
    return redirect(_next_or_default(request, "/app/payment-logs/"))


@login_required
def app_payment_log_delete(request, log_id):
    if not _is_manager(request.user):
        return HttpResponseForbidden("No tenés permisos para eliminar registros.")

    item = get_object_or_404(PaymentReceptionLog, id=log_id)
    if request.method != "POST":
        return redirect("app-payment-log-list")

    before = get_instance_snapshot(item)
    item_id = item.id
    item.delete()
    log_model_event(
        actor=request.user,
        action="delete_ui",
        instance=PaymentReceptionLog(id=item_id),
        before_data=before,
        after_data={},
    )
    messages.success(request, "Registro eliminado.")
    return redirect(_next_or_default(request, "/app/payment-logs/"))
