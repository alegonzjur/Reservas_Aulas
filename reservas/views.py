import logging
from datetime import date, timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.formats import date_format
from django.views.decorators.http import require_GET, require_POST

from . import exports
from .calendario import construir_semana, lunes_de, lunes_por_defecto, pendientes_para_revisar
from .forms import DisponibilidadForm, FiltroExportacionForm, FiltroReservasForm, PeticionForm
from .models import Aula, Reserva
from .permissions import es_admin, solo_admin, solo_con_rol
from .services import (
    ConflictoReserva,
    cambiar_estado,
    cambiar_estado_serie,
    crear_peticion,
    crear_serie,
    fechas_de_serie,
)

logger = logging.getLogger(__name__)
Estado = Reserva.Estado


def _es_htmx(request) -> bool:
    return request.headers.get("HX-Request") == "true"


# --------------------------------------------------------------------------- Entrada
@login_required
def inicio(request):
    """Cada rol aterriza donde le corresponde."""
    if es_admin(request.user):
        return redirect("panel")
    return redirect("reservar")


# --------------------------------------------------------------------------- Petición
@solo_con_rol
def reservar(request):
    admin = es_admin(request.user)
    if request.method == "POST":
        form = PeticionForm(request.POST, es_admin=admin)
        if form.is_valid():
            aceptar = admin and form.cleaned_data.get("aceptar_directamente", False)
            try:
                if form.fechas_serie:
                    resultado = crear_serie(
                        form.instance,
                        request.user,
                        hasta=form.cleaned_data["repetir_hasta"],
                        aceptar=aceptar,
                        materiales=form.cleaned_data["materiales"],
                    )
                else:
                    resultado = None
                    reserva = crear_peticion(
                        form.instance,
                        request.user,
                        aceptar=aceptar,
                        materiales=form.cleaned_data["materiales"],
                    )
            except ConflictoReserva:
                form.add_error(
                    None,
                    "Otra reserva acaba de ocupar esa franja. Elige otra hora u otra aula.",
                )
            else:
                if resultado is not None:
                    _avisar_serie_creada(request, resultado, aceptar)
                elif aceptar:
                    messages.success(request, f"Reserva creada y aceptada: {reserva}.")
                else:
                    messages.success(
                        request,
                        f"Petición enviada: {reserva}. Mantenimiento la revisará y, si hay "
                        "algún problema, se pondrá en contacto contigo.",
                    )
                return redirect("reservar")
    else:
        form = PeticionForm(es_admin=admin)
    return render(request, "reservas/reservar.html", {"form": form})


def _avisar_serie_creada(request, resultado, aceptada: bool):
    creadas = resultado.creadas
    primera, ultima = creadas[0], creadas[-1]
    dia = date_format(primera.fecha, "l")
    resumen = (
        f"{len(creadas)} fechas ({dia}, {primera.hora_inicio:%H:%M} a {primera.hora_fin:%H:%M}), "
        f"del {primera.fecha:%d/%m/%Y} al {ultima.fecha:%d/%m/%Y}, {primera.aula}"
    )
    if aceptada:
        messages.success(request, f"Serie creada y aceptada: {resumen}.")
    else:
        messages.success(
            request,
            f"Petición enviada: {resumen}. Mantenimiento la revisará y, si hay algún "
            "problema, se pondrá en contacto contigo.",
        )
    if resultado.omitidas:
        fechas = ", ".join(date_format(f, "j/n") for f in resultado.omitidas)
        messages.warning(
            request,
            f"No se han incluido {len(resultado.omitidas)} fechas porque el aula ya tiene una "
            f"reserva aceptada en esa franja: {fechas}.",
        )


@solo_con_rol
@require_GET
def disponibilidad(request):
    """Fragmento HTMX: avisa en vivo si la franja elegida ya está ocupada."""
    form = DisponibilidadForm(request.GET)
    contexto = {"estado": None}
    if form.is_valid():
        d = form.cleaned_data
        conflictos = list(
            Reserva.aceptadas_solapadas(d["aula"], d["fecha"], d["hora_inicio"], d["hora_fin"])
        )
        contexto = {"estado": "ocupada" if conflictos else "libre", "conflictos": conflictos}
        hasta = _fecha_o_none(request.GET.get("repetir_hasta"))
        if hasta and hasta >= d["fecha"] + timedelta(days=7):
            fechas = fechas_de_serie(d["fecha"], hasta)[: settings.MAX_REPETICIONES_SERIE + 1]
            if len(fechas) <= settings.MAX_REPETICIONES_SERIE:
                ocupadas = [
                    f for f in fechas
                    if Reserva.aceptadas_solapadas(d["aula"], f, d["hora_inicio"], d["hora_fin"]).exists()
                ]
                contexto = {
                    "estado": "serie",
                    "total": len(fechas),
                    "ocupadas": ocupadas,
                    "libres": len(fechas) - len(ocupadas),
                }
    return render(request, "reservas/_disponibilidad.html", contexto)


def _fecha_o_none(texto):
    try:
        return date.fromisoformat(texto or "")
    except ValueError:
        return None


# --------------------------------------------------------------------------- Panel
def _parametros_panel(request):
    try:
        semana = date.fromisoformat(request.GET.get("semana", ""))
    except ValueError:
        semana = None
    lunes = lunes_de(semana) if semana else lunes_por_defecto()
    aula_id = request.GET.get("aula", "")
    aula_id = int(aula_id) if aula_id.isdigit() else None
    return lunes, aula_id


def _contexto_panel(request):
    lunes, aula_id = _parametros_panel(request)
    # La cola de pendientes es trabajo de los administradores: el profesorado ve el
    # calendario (para saber si hay reservas) pero no esa lista.
    if es_admin(request.user):
        pendientes, total_pendientes = pendientes_para_revisar()
    else:
        pendientes, total_pendientes = [], 0
    params = {"semana": lunes.isoformat()}
    if aula_id:
        params["aula"] = aula_id
    contexto = construir_semana(lunes, aula_id, solo_aceptadas=not es_admin(request.user))
    contexto.update(
        aulas=Aula.objects.all(),
        aula_id=aula_id,
        pendientes=pendientes,
        total_pendientes=total_pendientes,
        querystring=urlencode(params),
    )
    return contexto


# El calendario lo ve cualquier usuario con rol; solo los administradores ven además la
# lista de pendientes y pueden actuar (las acciones siguen siendo solo_admin).
@solo_con_rol
@require_GET
def panel(request):
    return render(request, "reservas/panel.html", _contexto_panel(request))


@solo_con_rol
@require_GET
def panel_contenido(request):
    """Fragmento HTMX con calendario + pendientes (navegación entre semanas y refresco)."""
    contexto = _contexto_panel(request)
    # oob: además del calendario, devuelve la lista de pendientes para que HTMX la
    # sustituya "fuera de banda" en su propia columna (ver _panel_contenido.html).
    # El profesorado no tiene esa columna.
    contexto["oob"] = es_admin(request.user)
    respuesta = render(request, "reservas/_panel_contenido.html", contexto)
    # Para que "atrás" y recargar funcionen, la URL visible apunta a la página completa.
    # Los refrescos automáticos (tras aceptar/rechazar) no añaden entradas al historial.
    if "refresco" not in request.GET:
        respuesta["HX-Push-Url"] = f"{reverse('panel')}?{contexto['querystring']}"
    return respuesta


# --------------------------------------------------------------------------- Detalle y acciones
def _contexto_detalle(reserva, aviso=None):
    conflictos = []
    if reserva.estado != Estado.ACEPTADA:
        conflictos = list(
            Reserva.aceptadas_solapadas(
                reserva.aula, reserva.fecha, reserva.hora_inicio, reserva.hora_fin, excluir_pk=reserva.pk
            )
        )
    return {
        "reserva": reserva,
        "conflictos": conflictos,
        "eventos": reserva.eventos.select_related("usuario"),
        "aviso": aviso,
        "serie": _resumen_serie(reserva),
    }


def _resumen_serie(reserva):
    """Fechas hermanas y recuentos por estado, o None si no es parte de una serie."""
    if reserva.serie_id is None:
        return None
    fechas = list(reserva.serie.reservas.order_by("fecha", "hora_inicio", "id"))
    por_estado = {e: sum(1 for r in fechas if r.estado == e) for e in Estado.values}
    return {"fechas": fechas, "total": len(fechas), **por_estado}


def _reserva_completa(pk):
    return get_object_or_404(
        Reserva.objects.select_related("aula", "curso", "creada_por", "gestionada_por", "serie")
        .prefetch_related("materiales"),
        pk=pk,
    )


def _detalle_lectura(request, pk):
    """Ficha de solo lectura para el profesorado: sin notas, material, auditoría ni acciones.

    Solo las reservas aceptadas, que son las que ve en el calendario: las pendientes y las
    rechazadas no existen para quien no administra (tampoco por su URL: 404).
    """
    if not _es_htmx(request):
        return redirect("panel")  # la ficha completa (con historial) es solo para administradores
    reserva = get_object_or_404(
        Reserva.objects.select_related("aula", "curso").filter(estado=Estado.ACEPTADA),
        pk=pk,
    )
    return render(request, "reservas/_detalle_lectura.html", {"reserva": reserva})


@solo_con_rol
@require_GET
def reserva_detalle(request, pk):
    if not es_admin(request.user):
        return _detalle_lectura(request, pk)
    contexto = _contexto_detalle(_reserva_completa(pk))
    plantilla = "reservas/_detalle.html" if _es_htmx(request) else "reservas/detalle_pagina.html"
    return render(request, plantilla, contexto)


_ESTADO_POR_ACCION = {
    "aceptar": Estado.ACEPTADA,
    "rechazar": Estado.RECHAZADA,
    "reabrir": Estado.PENDIENTE,
}
_MENSAJE_OK = {
    Estado.ACEPTADA: "Reserva aceptada.",
    Estado.RECHAZADA: "Reserva rechazada. Recuerda avisar al profesor.",
    Estado.PENDIENTE: "Reserva devuelta a pendiente.",
}


@solo_admin
@require_POST
def reserva_accion(request, pk):
    destino = _ESTADO_POR_ACCION.get(request.POST.get("accion"))
    if destino is None:
        return HttpResponseBadRequest("Acción no válida.")
    reserva = get_object_or_404(Reserva, pk=pk)

    try:
        _, cambiado = cambiar_estado(reserva, destino, request.user, request.POST.get("motivo", ""))
        if cambiado:
            aviso = ("ok", _MENSAJE_OK[destino])
        else:
            aviso = ("info", f"La reserva ya estaba {Estado(destino).label.lower()}.")
    except ConflictoReserva:
        aviso = (
            "error",
            "No se puede aceptar: ese aula ya tiene una reserva aceptada en la misma franja.",
        )

    if _es_htmx(request):
        respuesta = render(
            request, "reservas/_detalle.html", _contexto_detalle(_reserva_completa(pk), aviso)
        )
        # Avisa al panel para que se recargue (calendario y lista de pendientes).
        respuesta["HX-Trigger"] = "reservasActualizadas"
        return respuesta

    nivel = {"ok": messages.SUCCESS, "info": messages.INFO, "error": messages.ERROR}[aviso[0]]
    messages.add_message(request, nivel, aviso[1])
    return redirect("reserva_detalle", pk=pk)


@solo_admin
@require_POST
def reserva_serie_accion(request, pk):
    """Acepta o rechaza las fechas pendientes de la serie a la que pertenece la reserva `pk`."""
    destino = {"aceptar": Estado.ACEPTADA, "rechazar": Estado.RECHAZADA}.get(request.POST.get("accion"))
    if destino is None:
        return HttpResponseBadRequest("Acción no válida.")
    reserva = get_object_or_404(Reserva.objects.select_related("serie"), pk=pk)
    if reserva.serie is None:
        return HttpResponseBadRequest("Esta reserva no pertenece a ninguna serie.")

    resultado = cambiar_estado_serie(reserva.serie, destino, request.user, request.POST.get("motivo", ""))
    hechas = len(resultado.cambiadas)
    verbo = "aceptadas" if destino == Estado.ACEPTADA else "rechazadas"
    if not hechas and not resultado.en_conflicto:
        aviso = ("info", "La serie no tenía fechas pendientes.")
    else:
        texto = f"{hechas} fechas {verbo}."
        nivel = "ok"
        if destino == Estado.RECHAZADA and hechas:
            texto += " Recuerda avisar al profesor."
        if resultado.en_conflicto:
            fechas = ", ".join(date_format(r.fecha, "j/n") for r in resultado.en_conflicto)
            texto += (
                f" {len(resultado.en_conflicto)} fechas siguen pendientes porque el aula ya tiene una "
                f"reserva aceptada en esa franja: {fechas}. Decídelas una a una."
            )
            nivel = "error" if not hechas else "info"
        aviso = (nivel, texto)

    if _es_htmx(request):
        respuesta = render(
            request, "reservas/_detalle.html", _contexto_detalle(_reserva_completa(pk), aviso)
        )
        respuesta["HX-Trigger"] = "reservasActualizadas"
        return respuesta

    nivel = {"ok": messages.SUCCESS, "info": messages.INFO, "error": messages.ERROR}[aviso[0]]
    messages.add_message(request, nivel, aviso[1])
    return redirect("reserva_detalle", pk=pk)


# --------------------------------------------------------------------------- Listado
@solo_admin
@require_GET
def listado(request):
    form = FiltroReservasForm(request.GET or None)
    reservas = Reserva.objects.select_related("aula", "curso", "gestionada_por")
    if form.is_bound and form.is_valid():
        reservas = form.filtrar(reservas)
    reservas = reservas.order_by("-fecha", "-hora_inicio", "-id")

    pagina = Paginator(reservas, 30).get_page(request.GET.get("pagina"))
    parametros = request.GET.copy()
    parametros.pop("pagina", None)
    return render(
        request,
        "reservas/listado.html",
        {"form": form, "pagina": pagina, "querystring": parametros.urlencode()},
    )


# --------------------------------------------------------------------------- Exportación
@solo_admin
@require_GET
def exportar(request):
    formato = request.GET.get("formato", "")
    form = FiltroExportacionForm(request.GET or None)

    if formato and form.is_valid():
        reservas = form.filtrar(Reserva.objects.all())
        delimitador = form.cleaned_data["delimitador"]
        if formato == "xlsx":
            return exports.exportar_excel_completo(reservas)
        if formato == "reservas_csv":
            return exports.respuesta_csv(
                "reservas", exports.CABECERA_RESERVAS, exports.filas_reservas(reservas), delimitador
            )
        if formato == "aulas_csv":
            return exports.respuesta_csv(
                "aulas", exports.CABECERA_AULAS, exports.filas_aulas(), delimitador
            )
        if formato == "auditoria_csv":
            return exports.respuesta_csv(
                "auditoria", exports.CABECERA_AUDITORIA, exports.filas_auditoria(reservas), delimitador
            )
        return HttpResponseBadRequest("Formato de exportación no válido.")

    return render(request, "reservas/exportar.html", {"form": form})