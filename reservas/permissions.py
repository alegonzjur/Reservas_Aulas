"""Roles de la aplicación.

Hay dos grupos de Django:
  * Administrador  -> Mantenimiento e Informática: gestionan (aceptan/rechazan) y exportan.
  * Profesorado    -> usuario genérico "Profesor": solo envía peticiones.
"""
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

GRUPO_ADMIN = "Administrador"
GRUPO_PROFESORADO = "Profesorado"


def es_admin(user) -> bool:
    if not user.is_authenticated:
        return False
    return user.is_superuser or user.groups.filter(name=GRUPO_ADMIN).exists()


def puede_pedir_reserva(user) -> bool:
    if not user.is_authenticated:
        return False
    return es_admin(user) or user.groups.filter(name=GRUPO_PROFESORADO).exists()


def _requiere(comprobacion):
    """Fabrica un decorador: exige sesión iniciada y, además, que pase `comprobacion`.

    Sin sesión -> redirige al login. Con sesión pero sin permiso -> 403.
    """

    def decorador(vista):
        @wraps(vista)
        @login_required
        def envoltura(request, *args, **kwargs):
            if not comprobacion(request.user):
                raise PermissionDenied
            return vista(request, *args, **kwargs)

        return envoltura

    return decorador


solo_admin = _requiere(es_admin)
solo_con_rol = _requiere(puede_pedir_reserva)