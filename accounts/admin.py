from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from accounts.models import User


@admin.register(User)
class HBCUserAdmin(UserAdmin):
    list_description = (
        "Usuarios del sistema HBC. Cada usuario tiene un rol que determina sus permisos: "
        "MASTER (acceso total, puede ver y modificar todo), ADMIN (administrador con permisos amplios) "
        "y FUNCIONARIO (operador que gestiona sus clientes asignados y sus tareas diarias). "
        "Los usuarios inactivos no pueden iniciar sesión. El campo 'is_staff' permite acceder a este panel de administración. "
        "Para crear un funcionario nuevo, asegurarse de asignar el rol correcto y una contraseña segura."
    )
    fieldsets = UserAdmin.fieldsets + (("HBC", {"fields": ("role",)}),)
    list_display = ("username", "email", "role", "is_staff", "is_active")
    list_filter = ("role", "is_staff", "is_active")
