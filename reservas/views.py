import logging
from datetime import date
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from . import exports
from .calendario import construir_semana, lunes_de, lunes_por_defecto, pendientes_para_revisar
from .forms import DisponibilidadForm, FiltroExportacionForm, FiltroReservasForm, PeticionForm
from .models import Aula, Reserva
from .permissions import es_admin, solo_admin, solo_con_rol
from .services import ConflictoReserva, cambiar_estado, crear_peticion

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
                reserva = crear_peticion(form.instance, request.user, aceptar=aceptar)
            except ConflictoReserva:
                form.add_error(
                    None,
                    "Otra reserva acaba de ocupar esa franja. Elige otra hora u otra aula.",
                )
            else:
                if aceptar:
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
    return render(request, "reservas/_disponibilidad.html", contexto)


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
    pendientes, total_pendientes = pendientes_para_revisar()
    params = {"semana": lunes.isoformat()}
    if aula_id:
        params["aula"] = aula_id
    contexto = construir_semana(lunes, aula_id)
    contexto.update(
        aulas=Aula.objects.all(),
        aula_id=aula_id,
        pendientes=pendientes,
        total_pendientes=total_pendientes,
        querystring=urlencode(params),
    )
    return contexto


@solo_admin
@require_GET
def panel(request):
    return render(request, "reservas/panel.html", _contexto_panel(request))


@solo_admin
@require_GET
def panel_contenido(request):
    """Fragmento HTMX con calendario + pendientes (navegación entre semanas y refresco)."""
    contexto = _contexto_panel(request)
    # oob: además del calendario, devuelve la lista de pendientes para que HTMX la
    # sustituya "fuera de banda" en su propia columna (ver _panel_contenido.html).
    contexto["oob"] = True
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
    }


def _reserva_completa(pk):
    return get_object_or_404(
        Reserva.objects.select_related("aula", "creada_por", "gestionada_por"), pk=pk
    )


@solo_admin
@require_GET
def reserva_detalle(request, pk):
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


# --------------------------------------------------------------------------- Listado
@solo_admin
@require_GET
def listado(request):
    form = FiltroReservasForm(request.GET or None)
    reservas = Reserva.objects.select_related("aula", "gestionada_por")
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