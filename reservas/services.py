"""Operaciones que cambian datos. Cada una es atómica y deja rastro de auditoría.

Las vistas no tocan `estado` directamente: pasan siempre por aquí para que nunca se
acepte algo sin registrar quién y cuándo.
"""
from functools import partial

from django.db import IntegrityError, transaction
from django.utils import timezone
from psycopg import errors as pg_errors

from . import emails
from .models import EventoReserva, Reserva

Estado = Reserva.Estado
Tipo = EventoReserva.Tipo

_TIPO_POR_ESTADO = {
    Estado.ACEPTADA: Tipo.ACEPTADA,
    Estado.RECHAZADA: Tipo.RECHAZADA,
    Estado.PENDIENTE: Tipo.REABIERTA,
}


class ConflictoReserva(Exception):
    """La franja ya está ocupada por una reserva aceptada."""

    def __init__(self, conflictos=()):
        self.conflictos = list(conflictos)
        super().__init__("La franja se solapa con una reserva ya aceptada.")


def registrar_evento(reserva, tipo, usuario, detalle=""):
    return EventoReserva.objects.create(
        reserva=reserva, tipo=tipo, usuario=usuario, detalle=detalle[:300]
    )


def _guardar(reserva, **kwargs):
    """Guarda dentro de un savepoint y traduce la violación de la restricción
    anti-solape de PostgreSQL (la red de seguridad frente a carreras) a ConflictoReserva."""
    try:
        with transaction.atomic():
            reserva.save(**kwargs)
    except IntegrityError as exc:
        if isinstance(exc.__cause__, pg_errors.ExclusionViolation):
            raise ConflictoReserva() from exc
        raise


@transaction.atomic
def crear_peticion(reserva: Reserva, usuario, aceptar: bool = False) -> Reserva:
    """Guarda una petición nueva.

    aceptar=False -> queda PENDIENTE y se avisa por correo a mantenimiento.
    aceptar=True  -> (solo administradores) queda ACEPTADA al momento; es el caso de
                     mantenimiento reservando por teléfono para quien no tiene red.
    """
    reserva.creada_por = usuario
    if aceptar:
        conflictos = Reserva.aceptadas_solapadas(
            reserva.aula, reserva.fecha, reserva.hora_inicio, reserva.hora_fin
        )
        if conflictos:
            raise ConflictoReserva(conflictos)
        reserva.estado = Estado.ACEPTADA
        reserva.gestionada_por = usuario
        reserva.gestionada_en = timezone.now()
    else:
        reserva.estado = Estado.PENDIENTE

    _guardar(reserva)

    registrar_evento(reserva, Tipo.CREADA, usuario)
    if aceptar:
        registrar_evento(reserva, Tipo.ACEPTADA, usuario, "Reserva hecha directamente por un administrador.")
    else:
        # on_commit: el correo solo sale si la petición realmente quedó guardada.
        transaction.on_commit(partial(emails.notificar_peticion_pendiente, reserva.pk))
    return reserva


@transaction.atomic
def cambiar_estado(reserva: Reserva, nuevo_estado: str, usuario, motivo: str = ""):
    """Acepta, rechaza o devuelve a pendiente una reserva.

    Devuelve (reserva, cambiado). `cambiado` es False si ya estaba en ese estado
    (p. ej. doble clic), sin duplicar el rastro de auditoría.
    Lanza ConflictoReserva si se intenta aceptar sobre una franja ocupada.
    """
    # Bloquea la fila para que dos administradores no actúen a la vez sobre la misma.
    reserva = (
        Reserva.objects.select_for_update(of=("self",)).select_related("aula").get(pk=reserva.pk)
    )
    if reserva.estado == nuevo_estado:
        return reserva, False

    if nuevo_estado == Estado.ACEPTADA:
        conflictos = Reserva.aceptadas_solapadas(
            reserva.aula, reserva.fecha, reserva.hora_inicio, reserva.hora_fin, excluir_pk=reserva.pk
        )
        if conflictos:
            raise ConflictoReserva(conflictos)

    motivo = (motivo or "").strip()
    reserva.estado = nuevo_estado
    if nuevo_estado == Estado.PENDIENTE:
        reserva.gestionada_por = None
        reserva.gestionada_en = None
        reserva.motivo_rechazo = ""
    else:
        reserva.gestionada_por = usuario
        reserva.gestionada_en = timezone.now()
        reserva.motivo_rechazo = motivo if nuevo_estado == Estado.RECHAZADA else ""

    _guardar(
        reserva,
        update_fields=["estado", "gestionada_por", "gestionada_en", "motivo_rechazo"],
    )
    registrar_evento(
        reserva,
        _TIPO_POR_ESTADO[nuevo_estado],
        usuario,
        motivo if nuevo_estado == Estado.RECHAZADA else "",
    )
    return reserva, True