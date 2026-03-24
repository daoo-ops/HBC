from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    class Role(models.TextChoices):
        MASTER = "MASTER", "Master"
        ADMIN = "ADMIN", "Administrador"
        FUNCIONARIO = "FUNCIONARIO", "Funcionario"

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.FUNCIONARIO,
        db_index=True,
    )

    @property
    def is_master(self) -> bool:
        return self.role == self.Role.MASTER

    @property
    def is_admin(self) -> bool:
        return self.role == self.Role.ADMIN

    @property
    def is_manager(self) -> bool:
        return self.role in {self.Role.MASTER, self.Role.ADMIN}

    def __str__(self) -> str:
        return f"{self.username} ({self.role})"
