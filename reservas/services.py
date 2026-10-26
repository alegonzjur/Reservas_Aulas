"""Operaciones que cambian datos. Cada una es atómica y deja rastro de auditoría.

Las vistas no tocan `estado` directamente: pasan siempre por aquí para que nunca se
acepte algo sin registrar quién y cuándo.
"""
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import partial

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from psycopg import errors as pg_errors

from . import emails
from .models import EventoReserva, Reserva, SerieReserva

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
def crear_peticion(
    reserva: Reserva, usuario, aceptar: bool = False, materiales=(), notificar: bool = True,
    detalle: str = "",
) -> Reserva:
    """Guarda una petición nueva.

    aceptar=False -> queda PENDIENTE y se avisa por correo a mantenimiento.
    aceptar=True  -> (solo administradores) queda ACEPTADA al momento; es el caso de
                     mantenimiento reservando por teléfono para quien no tiene red.
    materiales    -> los Material pedidos. Se guardan aquí, dentro de la misma transacción
                     y antes del correo, para que el aviso ya los incluya.
    notificar     -> False cuando la llama `crear_serie`, que envía un único correo por serie.
    detalle       -> texto para el primer evento del historial (p. ej. «Serie 3, fecha 2 de 4»).
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
    reserva.materiales.set(materiales)

    registrar_evento(reserva, Tipo.CREADA, usuario, detalle)
    if aceptar:
        registrar_evento(reserva, Tipo.ACEPTADA, usuario, "Reserva hecha directamente por un administrador.")
    elif notificar:
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


# --------------------------------------------------------------------------- Series semanales
def fechas_de_serie(inicio: date, hasta: date) -> list[date]:
    """Fechas desde `inicio` cada 7 días (el mismo día de la semana) hasta `hasta` incluida."""
    fechas = []
    actual = inicio
    while actual <= hasta:
        fechas.append(actual)
        actual += timedelta(days=7)
    return fechas


@dataclass
class ResultadoSerie:
    serie: SerieReserva
    creadas: list = field(default_factory=list)
    omitidas: list = field(default_factory=list)  # fechas que chocaban con una reserva aceptada


@transaction.atomic
def crear_serie(
    plantilla: Reserva, usuario, hasta: date, aceptar: bool = False, materiales=()
) -> ResultadoSerie:
    """Crea una reserva por semana desde `plantilla.fecha` hasta `hasta` (inclusive).

    Cada fecha es una reserva normal. Las fechas cuya franja ya está ocupada por una reserva
    ACEPTADA no se crean (se devuelven en `omitidas`): no impiden crear las demás. Si no se
    puede crear ninguna, lanza ConflictoReserva y no se guarda nada.
    Se envía un único correo a mantenimiento por serie (salvo con aceptar=True).
    """
    fechas = fechas_de_serie(plantilla.fecha, hasta)
    if len(fechas) > settings.MAX_REPETICIONES_SERIE:
        raise ValueError(f"Una serie no puede tener más de {settings.MAX_REPETICIONES_SERIE} fechas.")

    serie = SerieReserva.objects.create(creada_por=usuario)
    resultado = ResultadoSerie(serie=serie)
    for numero, fecha in enumerate(fechas, start=1):
        # Una petición pendiente no choca por sí misma (las pendientes pueden coincidir), pero
        # igual que en el formulario individual no se pide una franja ya ocupada.
        if Reserva.aceptadas_solapadas(
            plantilla.aula, fecha, plantilla.hora_inicio, plantilla.hora_fin
        ).exists():
            resultado.omitidas.append(fecha)
            continue
        reserva = Reserva(
            aula=plantilla.aula, fecha=fecha, hora_inicio=plantilla.hora_inicio,
            hora_fin=plantilla.hora_fin, profesor=plantilla.profesor, titulo=plantilla.titulo,
            curso=plantilla.curso, notas=plantilla.notas, serie=serie,
        )
        try:
            crear_peticion(
                reserva, usuario, aceptar=aceptar, materiales=materiales, notificar=False,
                detalle=f"Serie {serie.pk}: fecha {numero} de {len(fechas)}.",
            )
        except ConflictoReserva:
            resultado.omitidas.append(fecha)
        else:
            resultado.creadas.append(reserva)

    if not resultado.creadas:
        raise ConflictoReserva()  # revierte también la creación de la serie
    if not aceptar:
        transaction.on_commit(partial(emails.notificar_serie_pendiente, serie.pk))
    return resultado


@dataclass
class ResultadoDecisionSerie:
    cambiadas: list = field(default_factory=list)
    en_conflicto: list = field(default_factory=list)  # no se pudieron aceptar: aula ocupada


@transaction.atomic
def cambiar_estado_serie(
    serie: SerieReserva, nuevo_estado: str, usuario, motivo: str = ""
) -> ResultadoDecisionSerie:
    """Acepta o rechaza las fechas PENDIENTES de la serie (las ya decididas no se tocan).

    Al aceptar, las fechas que chocan con una reserva aceptada se quedan pendientes y se
    devuelven en `en_conflicto`; las demás se aceptan. Cada fecha deja su propio rastro de
    auditoría, igual que si se hubiera decidido una a una.
    """
    if nuevo_estado not in (Estado.ACEPTADA, Estado.RECHAZADA):
        raise ValueError("Una serie solo se acepta o se rechaza en bloque.")
    resultado = ResultadoDecisionSerie()
    pendientes = serie.reservas.filter(estado=Estado.PENDIENTE).order_by("fecha", "hora_inicio", "id")
    for reserva in pendientes:
        try:
            reserva, cambiado = cambiar_estado(reserva, nuevo_estado, usuario, motivo)
        except ConflictoReserva:
            resultado.en_conflicto.append(reserva)
        else:
            if cambiado:
                resultado.cambiadas.append(reserva)
    return resultado