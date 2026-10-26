"""Crea los grupos y los tres usuarios de la aplicación.

    python manage.py crear_usuarios

Pide la contraseña de cada usuario por teclado (no se muestra ni se guarda en ningún
fichero). Para automatizarlo, define las variables de entorno
RESERVAS_PASSWORD_MANTENIMIENTO, RESERVAS_PASSWORD_INFORMATICA y
RESERVAS_PASSWORD_PROFESOR y usa --no-input.

Es seguro ejecutarlo varias veces: los usuarios que ya existen no se tocan, salvo que
se indique --restablecer (que les cambia la contraseña).
"""
import os
from getpass import getpass

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from reservas.permissions import GRUPO_ADMIN, GRUPO_PROFESORADO

# (username, nombre visible, grupo, is_staff, is_superuser)
USUARIOS = [
    ("mantenimiento", "Mantenimiento", GRUPO_ADMIN, True, False),
    ("informatica", "Informática", GRUPO_ADMIN, True, True),
    ("profesor", "Profesor", GRUPO_PROFESORADO, False, False),
]

# Permisos del grupo Administrador sobre el panel /admin/ (catálogos y correcciones).
# Se reaplican en cada ejecución del comando, también si los usuarios ya existen.
PERMISOS_ADMIN = [
    "add_aula", "change_aula", "view_aula",
    "add_curso", "change_curso", "view_curso",
    "add_material", "change_material", "view_material",
    "view_reserva", "change_reserva",
    "view_eventoreserva",
]


class Command(BaseCommand):
    help = "Crea los grupos y los usuarios Mantenimiento, Informática y Profesor."

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-input", action="store_true",
            help="Lee las contraseñas de las variables RESERVAS_PASSWORD_<USUARIO>.",
        )
        parser.add_argument(
            "--restablecer", action="store_true",
            help="Cambia la contraseña de los usuarios que ya existen.",
        )

    def handle(self, *args, **opciones):
        grupo_admin, _ = Group.objects.get_or_create(name=GRUPO_ADMIN)
        Group.objects.get_or_create(name=GRUPO_PROFESORADO)
        permisos = Permission.objects.filter(
            content_type__app_label="reservas", codename__in=PERMISOS_ADMIN
        )
        if permisos.count() != len(PERMISOS_ADMIN):
            raise CommandError("Faltan permisos: ejecuta antes «python manage.py migrate».")
        grupo_admin.permissions.set(permisos)

        User = get_user_model()
        for username, nombre, nombre_grupo, es_staff, es_super in USUARIOS:
            existente = User.objects.filter(username=username).first()
            if existente and not opciones["restablecer"]:
                self.stdout.write(f"· {username}: ya existe, no se modifica.")
                continue

            password = self._obtener_password(username, opciones["no_input"], validar=nombre_grupo == GRUPO_ADMIN)
            usuario = existente or User(username=username)
            usuario.first_name = nombre
            usuario.is_staff = es_staff
            usuario.is_superuser = es_super
            usuario.is_active = True
            usuario.set_password(password)
            usuario.save()
            usuario.groups.set([Group.objects.get(name=nombre_grupo)])
            accion = "contraseña restablecida" if existente else "creado"
            self.stdout.write(self.style.SUCCESS(f"✓ {username}: {accion} ({nombre_grupo})."))

    def _obtener_password(self, username, sin_preguntar, validar):
        variable = f"RESERVAS_PASSWORD_{username.upper()}"
        if sin_preguntar:
            password = os.environ.get(variable, "")
            if not password:
                raise CommandError(f"Falta la variable de entorno {variable}.")
            self._comprobar(username, password, validar)
            return password

        while True:
            password = getpass(f"Contraseña para «{username}»: ")
            if password != getpass("Repítela: "):
                self.stderr.write("No coinciden. Inténtalo de nuevo.")
                continue
            try:
                self._comprobar(username, password, validar)
            except CommandError as exc:
                self.stderr.write(str(exc))
                continue
            return password

    @staticmethod
    def _comprobar(username, password, validar):
        """Las cuentas de administración pasan los validadores de Django. La cuenta
        genérica del profesorado solo exige una longitud mínima: la comparte todo el
        claustro y se espera que sea fácil de recordar."""
        if validar:
            try:
                validate_password(password)
            except ValidationError as exc:
                raise CommandError(" ".join(exc.messages)) from exc
        elif len(password) < 6:
            raise CommandError("La contraseña del profesorado debe tener al menos 6 caracteres.")