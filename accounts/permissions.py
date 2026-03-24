from rest_framework.permissions import SAFE_METHODS, BasePermission

from accounts.models import User


def _is_authenticated(user) -> bool:
    return bool(user and user.is_authenticated)


def _is_manager(user) -> bool:
    return _is_authenticated(user) and user.role in {User.Role.MASTER, User.Role.ADMIN}


class IsMasterOrAdmin(BasePermission):
    def has_permission(self, request, view):
        return _is_manager(request.user)


class ReadOnlyForFuncionarioWriteForManagers(BasePermission):
    def has_permission(self, request, view):
        if not _is_authenticated(request.user):
            return False
        if request.method in SAFE_METHODS:
            return True
        return _is_manager(request.user)


class ClientAccessPermission(BasePermission):
    """Clientes: funcionario puede editar solo sus asignados; create/delete solo manager."""

    def has_permission(self, request, view):
        if not _is_authenticated(request.user):
            return False
        if request.method in SAFE_METHODS:
            return True
        if request.method in {"POST", "DELETE"}:
            return _is_manager(request.user)
        if request.method in {"PUT", "PATCH"}:
            return request.user.role in {
                User.Role.MASTER,
                User.Role.ADMIN,
                User.Role.FUNCIONARIO,
            }
        return False

    def has_object_permission(self, request, view, obj):
        if _is_manager(request.user):
            return True
        return obj.responsible_id == request.user.id


class OperationalAccessPermission(BasePermission):
    """Funcionario puede operar módulos operativos, pero no eliminar."""

    def has_permission(self, request, view):
        if not _is_authenticated(request.user):
            return False
        if request.method in SAFE_METHODS:
            return True
        if request.method == "DELETE":
            return _is_manager(request.user)
        return request.user.role in {
            User.Role.MASTER,
            User.Role.ADMIN,
            User.Role.FUNCIONARIO,
        }
