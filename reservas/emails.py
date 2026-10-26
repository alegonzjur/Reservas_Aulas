"""Aviso por correo a mantenimiento cuando entra una petición pendiente."""
import logging

from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse
from django.utils.formats import date_format

from .models import Reserva

logger = logging.getLogger(__name__)


def notificar_peticion_pendiente(reserva_id: int) -> bool:
    """Envía el aviso. Devuelve True si se envió.

    Nunca lanza excepciones: si el SMTP falla, la petición ya está guardada y se verá
    igualmente en el panel; solo se deja constancia en el log.
    """
    destinatarios = settings.RESERVAS_EMAIL_DESTINO
    if not destinatarios:
        logger.warning("RESERVAS_EMAIL_DESTINO está vacío: no se envía el aviso de la petición %s.", reserva_id)
        return False

    try:
        reserva = Reserva.objects.select_related("aula", "curso").get(pk=reserva_id)
        materiales = ", ".join(m.nombre for m in reserva.materiales.all()) or "—"
        fecha = date_format(reserva.fecha, "l d/m/Y")
        horario = f"{reserva.hora_inicio:%H:%M} - {reserva.hora_fin:%H:%M}"
        # El asunto solo lleva datos controlados por administradores (aula) y fecha/hora.
        # El texto libre del profesorado va únicamente en el cuerpo.
        asunto = f"[Reservas de aulas] Nueva petición: {reserva.aula} · {reserva.fecha:%d/%m/%Y} {horario}"
        cuerpo = (
            "Hay una nueva petición de reserva pendiente de revisar.\n\n"
            f"Aula:      {reserva.aula}\n"
            f"Día:       {fecha}\n"
            f"Horario:   {horario}\n"
            f"Título:    {reserva.titulo or '—'}\n"
            f"Curso:     {reserva.curso or '—'}\n"
            f"Profesor:  {reserva.profesor}\n"
            f"Material:  {materiales}\n"
            f"Notas:     {reserva.notas or '—'}\n\n"
            f"Revisar y responder: {settings.SITE_URL}{reverse('panel')}\n"
        )
        send_mail(asunto, cuerpo, settings.DEFAULT_FROM_EMAIL, destinatarios, fail_silently=False)
    except Exception:  # noqa: BLE001 - un fallo de correo no debe romper nada más
        logger.exception("No se pudo enviar el aviso de la petición %s.", reserva_id)
        return False
    return True