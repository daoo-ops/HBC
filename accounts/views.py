from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from datetime import date
from datetime import timedelta
from django.db.models import Q
from django.shortcuts import redirect, render
from django.utils.decorators import method_decorator
from django.utils import timezone
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from rest_framework import permissions, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework import viewsets
from rest_framework.views import APIView

from accounts.models import User
from accounts.permissions import IsMasterOrAdmin
from accounts.serializers import UserManagementSerializer, UserSummarySerializer
from auditing.services import get_instance_snapshot, log_model_event
from clients.models import Client
from operations.models import PendingItem, Submission
from operations.services import ensure_period_submissions_for_clients
from tax_commitments.models import TaxCommitment


class HomeRedirectView(View):
    def get(self, request):
        if request.user.is_authenticated:
            return redirect("dashboard")
        return redirect("login-page")


class LoginPageView(View):
    def get(self, request):
        if request.user.is_authenticated:
            return redirect("dashboard")
        return render(request, "login.html")

    def post(self, request):
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        user = authenticate(request, username=username, password=password)
        if user is None:
            messages.error(request, "Usuario o contraseña inválidos.")
            return render(request, "login.html", status=401)
        login(request, user)
        return redirect("dashboard")


class LogoutPageView(View):
    def get(self, request):
        logout(request)
        return redirect("login-page")


@method_decorator(login_required, name="dispatch")
class DashboardView(View):
    def get(self, request):
        can_view_financial = request.user.role in {User.Role.MASTER, User.Role.ADMIN}
        is_funcionario = request.user.role == User.Role.FUNCIONARIO
        today = timezone.localdate()
        week_cutoff = today + timedelta(days=7)
        active_clients = Client.objects.filter(is_deleted=False, status=Client.Status.ACTIVE).prefetch_related(
            "client_obligations__obligation"
        )
        if is_funcionario:
            active_clients = active_clients.filter(responsible_id=request.user.id)
        ensure_period_submissions_for_clients(active_clients, year=today.year, month=today.month)
        pending_items_qs = PendingItem.objects.filter(
            status=PendingItem.Status.OPEN,
            is_deleted=False,
        ).select_related("client")
        if is_funcionario:
            pending_items_qs = pending_items_qs.filter(client__responsible_id=request.user.id)
        pending_items = pending_items_qs.count()

        tax_commitments_qs = TaxCommitment.objects.select_related("client").exclude(status=TaxCommitment.Status.ARCHIVED)
        if is_funcionario:
            tax_commitments_qs = tax_commitments_qs.filter(client__responsible_id=request.user.id)
        tax_open_qs = tax_commitments_qs.exclude(status=TaxCommitment.Status.PAID)
        tax_due_today_qs = tax_open_qs.filter(due_date=today)
        tax_due_week_qs = tax_open_qs.filter(due_date__gt=today, due_date__lte=week_cutoff)
        tax_overdue_qs = tax_open_qs.filter(due_date__lt=today)
        tax_to_notify_qs = tax_open_qs.filter(status=TaxCommitment.Status.PENDING, due_date__lte=week_cutoff)
        tax_due_today_items = list(tax_due_today_qs.order_by("due_date", "client__name")[:3])
        tax_due_week_items = list(tax_due_week_qs.order_by("due_date", "client__name")[:3])
        tax_overdue_items = list(tax_overdue_qs.order_by("due_date", "client__name")[:3])
        tax_to_notify_items = list(tax_to_notify_qs.order_by("due_date", "client__name")[:3])

        active_submissions_qs = Submission.objects.select_related("client", "obligation").filter(is_archived=False).exclude(
            status=Submission.Status.SUBMITTED
        )
        if is_funcionario:
            active_submissions_qs = active_submissions_qs.filter(client__responsible_id=request.user.id)
        active_submissions = list(active_submissions_qs)

        due_today_items = []
        due_week_items = []
        overdue_items = []
        unscheduled_items = []
        for item in active_submissions:
            if item.status == Submission.Status.LATE or (item.due_date and item.due_date < today):
                item.days_late = abs((today - item.due_date).days) if item.due_date else None
                overdue_items.append(item)
            elif item.due_date and item.due_date == today:
                due_today_items.append(item)
            elif item.due_date and today < item.due_date <= week_cutoff:
                due_week_items.append(item)
            else:
                unscheduled_items.append(item)

        due_today_items.sort(key=lambda row: row.client.name)
        due_week_items.sort(key=lambda row: (row.due_date, row.client.name))
        overdue_items.sort(key=lambda row: (row.due_date or date.min, row.client.name))
        unscheduled_items.sort(key=lambda row: row.client.name)

        submissions_open_count = len(active_submissions)

        finalized_items_qs = Submission.objects.select_related("client", "obligation").filter(
            is_archived=False,
            status=Submission.Status.SUBMITTED,
        )
        if is_funcionario:
            finalized_items_qs = finalized_items_qs.filter(client__responsible_id=request.user.id)
        finalized_items = list(finalized_items_qs.order_by("-submitted_at", "-updated_at")[:8])

        urgent_pending_items = pending_items_qs.filter(priority=PendingItem.Priority.URGENT).order_by("expected_date")[:8]
        urgent_pending_count = pending_items_qs.filter(priority=PendingItem.Priority.URGENT).count()
        non_urgent_pending_items = pending_items_qs.exclude(priority=PendingItem.Priority.URGENT).order_by(
            "expected_date",
            "priority",
            "client__name",
        )[:4]

        context = {
            "can_view_financial": can_view_financial,
            "is_funcionario": is_funcionario,
            "active_clients_count": active_clients.count(),
            "pending_items_count": pending_items,
            "submissions_open_count": submissions_open_count,
            "tax_open_count": tax_open_qs.count(),
            "today": today,
            "week_cutoff": week_cutoff,
            "due_today_items": due_today_items[:10],
            "due_week_items": due_week_items[:12],
            "overdue_items": overdue_items[:12],
            "unscheduled_items": unscheduled_items[:8],
            "finalized_items": finalized_items,
            "due_today_count": len(due_today_items),
            "due_week_count": len(due_week_items),
            "overdue_count": len(overdue_items),
            "urgent_count": urgent_pending_count,
            "urgent_pending_items": urgent_pending_items,
            "non_urgent_pending_items": non_urgent_pending_items,
            "tax_due_today_count": tax_due_today_qs.count(),
            "tax_due_week_count": tax_due_week_qs.count(),
            "tax_overdue_count": tax_overdue_qs.count(),
            "tax_to_notify_count": tax_to_notify_qs.count(),
            "tax_due_today_items": tax_due_today_items,
            "tax_due_week_items": tax_due_week_items,
            "tax_overdue_items": tax_overdue_items,
            "tax_to_notify_items": tax_to_notify_items,
        }
        return render(request, "dashboard.html", context)


@method_decorator(csrf_exempt, name="dispatch")
class LoginAPIView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        username = request.data.get("username", "").strip()
        password = request.data.get("password", "")
        if not username or not password:
            return Response({"detail": "username y password son requeridos"}, status=400)

        user = authenticate(request, username=username, password=password)
        if user is None:
            return Response({"detail": "Credenciales inválidas"}, status=status.HTTP_401_UNAUTHORIZED)

        login(request, user)
        return Response({
            "message": "Login exitoso",
            "user": UserSummarySerializer(user).data,
        })


@method_decorator(csrf_exempt, name="dispatch")
class LogoutAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        logout(request)
        return Response({"message": "Logout exitoso"})


class MeAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        return Response(UserSummarySerializer(request.user).data)


class UserViewSet(viewsets.ModelViewSet):
    serializer_class = UserManagementSerializer
    permission_classes = [permissions.IsAuthenticated, IsMasterOrAdmin]

    def get_queryset(self):
        if self.request.user.role == User.Role.MASTER:
            return User.objects.all().order_by("username")
        return User.objects.filter(Q(role=User.Role.FUNCIONARIO) | Q(id=self.request.user.id)).order_by(
            "username"
        )

    def _validate_admin_role_scope(self, role):
        if self.request.user.role == User.Role.ADMIN and role != User.Role.FUNCIONARIO:
            raise PermissionDenied("Administrador solo puede gestionar funcionarios.")

    def perform_create(self, serializer):
        requested_role = serializer.validated_data.get("role", User.Role.FUNCIONARIO)
        self._validate_admin_role_scope(requested_role)
        user = serializer.save()
        log_model_event(
            actor=self.request.user,
            action="create",
            instance=user,
            after_data=get_instance_snapshot(user),
        )

    def perform_update(self, serializer):
        instance = serializer.instance
        if self.request.user.role == User.Role.ADMIN and instance.role != User.Role.FUNCIONARIO:
            raise PermissionDenied("Administrador no puede modificar usuarios de nivel superior.")

        requested_role = serializer.validated_data.get("role", instance.role)
        self._validate_admin_role_scope(requested_role)

        before = get_instance_snapshot(instance)
        user = serializer.save()
        log_model_event(
            actor=self.request.user,
            action="update",
            instance=user,
            before_data=before,
            after_data=get_instance_snapshot(user),
        )

    def perform_destroy(self, instance):
        if self.request.user.role == User.Role.ADMIN and instance.role != User.Role.FUNCIONARIO:
            raise PermissionDenied("Administrador no puede eliminar usuarios de nivel superior.")
        before = get_instance_snapshot(instance)
        super().perform_destroy(instance)
        log_model_event(
            actor=self.request.user,
            action="delete",
            instance=instance,
            before_data=before,
            after_data={},
        )
