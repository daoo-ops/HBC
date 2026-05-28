from django.contrib import admin
from django.urls import include, path

# ── Títulos del panel de administración en español ──
admin.site.site_header = "HBC Consultoría — Administración"
admin.site.site_title = "HBC Sistema"
admin.site.index_title = "Panel de administración"
from rest_framework.routers import DefaultRouter

from hbc_system import app_views
from accounts.views import (
    DashboardView,
    HomeRedirectView,
    LoginAPIView,
    LoginPageView,
    LogoutAPIView,
    LogoutPageView,
    MeAPIView,
    UserViewSet,
)
from auditing.views import AuditLogViewSet
from banks.views import BankRequestViewSet
from billing.views import ChargeViewSet, ContractViewSet
from clients.views import ClientViewSet
from imports_app.views import ClientsImportCommitAPIView, ClientsImportPreviewAPIView
from operations.views import DeadlineViewSet, PendingItemViewSet, SubmissionViewSet
from payment_logs import views as payment_log_views

router = DefaultRouter(trailing_slash=False)
router.register("users", UserViewSet, basename="users")
router.register("clients", ClientViewSet, basename="clients")
router.register("deadlines", DeadlineViewSet, basename="deadlines")
router.register("submissions", SubmissionViewSet, basename="submissions")
router.register("pending-items", PendingItemViewSet, basename="pending-items")
router.register("charges", ChargeViewSet, basename="charges")
router.register("contracts", ContractViewSet, basename="contracts")
router.register("bank-requests", BankRequestViewSet, basename="bank-requests")
router.register("audit-log", AuditLogViewSet, basename="audit-log")

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", HomeRedirectView.as_view(), name="home"),
    path("login/", LoginPageView.as_view(), name="login-page"),
    path("logout/", LogoutPageView.as_view(), name="logout-page"),
    path("dashboard/", DashboardView.as_view(), name="dashboard"),
    path("app/clients/", app_views.app_clients_list, name="app-clients-list"),
    path("app/clients/new/", app_views.app_client_create, name="app-client-create"),
    path("app/clients/<int:client_id>/", app_views.app_client_detail, name="app-client-detail"),
    path("app/clients/<int:client_id>/edit/", app_views.app_client_edit, name="app-client-edit"),
    path(
        "app/clients/<int:client_id>/invoice-period-status/",
        app_views.app_client_invoice_period_status_update,
        name="app-client-invoice-period-status",
    ),
    path("app/clients/<int:client_id>/notes/new/", app_views.app_note_create, name="app-note-create"),
    path("app/notes/<int:note_id>/edit/", app_views.app_note_edit, name="app-note-edit"),
    path("app/pending-items/", app_views.app_pending_list, name="app-pending-list"),
    path("app/pending-items/new/", app_views.app_pending_create, name="app-pending-create"),
    path("app/pending-items/<int:item_id>/edit/", app_views.app_pending_edit, name="app-pending-edit"),
    path("app/pending-items/<int:item_id>/resolve/", app_views.app_pending_resolve, name="app-pending-resolve"),
    path(
        "app/pending-items/<int:item_id>/bank-document-loaded/<str:document_kind>/",
        app_views.app_pending_mark_bank_document_loaded,
        name="app-pending-bank-document-loaded",
    ),
    path("app/pending-items/<int:item_id>/delete/", app_views.app_pending_delete, name="app-pending-delete"),
    path("app/notifications/panel/", app_views.app_notifications_panel, name="app-notifications-panel"),
    path("app/notifications/mark-read/", app_views.app_notifications_mark_read, name="app-notifications-mark-read"),
    path("app/submissions/", app_views.app_submission_list, name="app-submission-list"),
    path("app/submissions/new/", app_views.app_submission_create, name="app-submission-create"),
    path("app/submissions/<int:submission_id>/edit/", app_views.app_submission_edit, name="app-submission-edit"),
    path(
        "app/submissions/<int:submission_id>/mark-submitted/",
        app_views.app_submission_mark_submitted,
        name="app-submission-mark-submitted",
    ),
    path(
        "app/submissions/<int:submission_id>/reactivate/",
        app_views.app_submission_reactivate,
        name="app-submission-reactivate",
    ),
    path("app/submissions/<int:submission_id>/archive/", app_views.app_submission_archive, name="app-submission-archive"),
    path("app/submissions/<int:submission_id>/reopen/", app_views.app_submission_reopen, name="app-submission-reopen"),
    path("app/banks/", app_views.app_bank_list, name="app-bank-list"),
    path("app/banks/new/", app_views.app_bank_create, name="app-bank-create"),
    path("app/banks/<int:request_id>/edit/", app_views.app_bank_edit, name="app-bank-edit"),
    path("app/banks/<int:request_id>/note/", app_views.app_bank_add_note, name="app-bank-note"),
    path(
        "app/banks/<int:request_id>/mark-receipts-loaded/",
        app_views.app_bank_mark_receipts_loaded,
        name="app-bank-mark-receipts-loaded",
    ),
    path(
        "app/banks/<int:request_id>/mark-receipts-pending/",
        app_views.app_bank_mark_receipts_pending,
        name="app-bank-mark-receipts-pending",
    ),
    path(
        "app/banks/<int:request_id>/mark-receipts-notified/",
        app_views.app_bank_mark_receipts_notified,
        name="app-bank-mark-receipts-notified",
    ),
    path(
        "app/banks/<int:request_id>/mark-in-progress/",
        app_views.app_bank_mark_in_progress,
        name="app-bank-mark-in-progress",
    ),
    path(
        "app/banks/<int:request_id>/mark-completed/",
        app_views.app_bank_mark_completed,
        name="app-bank-mark-completed",
    ),
    path("app/banks/<int:request_id>/archive/", app_views.app_bank_archive, name="app-bank-archive"),
    path("app/banks/<int:request_id>/reopen/", app_views.app_bank_reopen, name="app-bank-reopen"),
    path(
        "app/banks/<int:request_id>/create-receipts-pending/",
        app_views.app_bank_create_receipts_pending,
        name="app-bank-create-receipts-pending",
    ),
    path("app/charges/", app_views.app_charge_list, name="app-charge-list"),
    path("app/charges/new/", app_views.app_charge_create, name="app-charge-create"),
    path("app/charges/<int:charge_id>/edit/", app_views.app_charge_edit, name="app-charge-edit"),
    path("app/charges/<int:charge_id>/mark-paid/", app_views.app_charge_mark_paid, name="app-charge-mark-paid"),
    path("app/payment-logs/", payment_log_views.app_payment_log_list, name="app-payment-log-list"),
    path("app/payment-logs/new/", payment_log_views.app_payment_log_create, name="app-payment-log-create"),
    path("app/payment-logs/<int:log_id>/edit/", payment_log_views.app_payment_log_edit, name="app-payment-log-edit"),
    path("app/payment-logs/<int:log_id>/archive/", payment_log_views.app_payment_log_archive, name="app-payment-log-archive"),
    path(
        "app/payment-logs/<int:log_id>/unarchive/",
        payment_log_views.app_payment_log_unarchive,
        name="app-payment-log-unarchive",
    ),
    path(
        "app/payment-logs/<int:log_id>/delete/",
        payment_log_views.app_payment_log_delete,
        name="app-payment-log-delete",
    ),
    path("app/contracts/", app_views.app_contract_list, name="app-contract-list"),
    path("app/contracts/new/", app_views.app_contract_create, name="app-contract-create"),
    path("app/contracts/<int:contract_id>/edit/", app_views.app_contract_edit, name="app-contract-edit"),
    path("app/tax-commitments/", app_views.app_tax_commitment_list, name="app-tax-commitment-list"),
    path("app/tax-commitments/new/", app_views.app_tax_commitment_create, name="app-tax-commitment-create"),
    path(
        "app/tax-commitments/<int:commitment_id>/edit/",
        app_views.app_tax_commitment_edit,
        name="app-tax-commitment-edit",
    ),
    path(
        "app/tax-commitments/<int:commitment_id>/edit-installment/",
        app_views.app_tax_commitment_installment_edit,
        name="app-tax-commitment-installment-edit",
    ),
    path(
        "app/tax-commitments/<int:commitment_id>/notify/",
        app_views.app_tax_commitment_notify,
        name="app-tax-commitment-notify",
    ),
    path(
        "app/tax-commitments/<int:commitment_id>/mark-paid/",
        app_views.app_tax_commitment_mark_paid,
        name="app-tax-commitment-mark-paid",
    ),
    path(
        "app/tax-commitments/<int:commitment_id>/archive/",
        app_views.app_tax_commitment_archive,
        name="app-tax-commitment-archive",
    ),
    path(
        "app/tax-commitments/group/<uuid:group_id>/archive/",
        app_views.app_tax_commitment_archive_group,
        name="app-tax-commitment-archive-group",
    ),
    path("auth/login", LoginAPIView.as_view(), name="api-login"),
    path("auth/logout", LogoutAPIView.as_view(), name="api-logout"),
    path("auth/me", MeAPIView.as_view(), name="api-me"),
    path("imports/clients/preview", ClientsImportPreviewAPIView.as_view(), name="import-clients-preview"),
    path("imports/clients/commit", ClientsImportCommitAPIView.as_view(), name="import-clients-commit"),
    path("", include(router.urls)),
]
