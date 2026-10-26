"""Exportación de datos a CSV y Excel (pensada para migrar a otro sistema).

Decisiones:
  * Fechas en ISO (AAAA-MM-DD) y horas en HH:MM: no ambiguas entre programas e idiomas.
  * El estado se exporta con su código estable (pendiente/aceptada/rechazada).
  * CSV en UTF-8 con BOM para que Excel respete tildes y eñes.
  * Defensa contra inyección de fórmulas: el texto libre lo escribe el profesorado y lo
    abre un administrador; un valor como «=HYPERLINK(...)» no debe ejecutarse.
"""
import csv
from datetime import date, datetime, time
from io import BytesIO

from django.http import HttpResponse
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .models import Aula, Curso, EventoReserva, Material, Reserva

CABECERA_RESERVAS = [
    "id", "aula", "fecha", "hora_inicio", "hora_fin", "titulo", "curso", "profesor",
    "materiales", "notas", "estado",
    "creada_en", "creada_por", "gestionada_en", "gestionada_por", "motivo_rechazo",
]
CABECERA_AULAS = ["id", "nombre", "ubicacion", "capacidad", "activa"]
CABECERA_CURSOS = ["id", "nombre", "activo"]
CABECERA_MATERIALES = ["id", "nombre", "activo", "orden"]

# Separador de la columna "materiales" (una reserva puede pedir varios). No es ni la coma
# ni el punto y coma, para no confundirse con el delimitador del CSV.
SEPARADOR_MATERIALES = " | "
CABECERA_AUDITORIA = ["id_evento", "id_reserva", "fecha", "tipo", "usuario", "detalle"]

_INICIOS_PELIGROSOS = ("=", "+", "-", "@", "\t", "\r")


def _local_naive(dt: datetime | None) -> datetime | None:
    """Fecha/hora local sin zona (Excel no admite datetimes con zona horaria)."""
    if dt is None:
        return None
    return timezone.localtime(dt).replace(tzinfo=None, microsecond=0)


def filas_reservas(queryset):
    qs = (
        queryset.select_related("aula", "curso", "creada_por", "gestionada_por")
        .prefetch_related("materiales")
        .order_by("fecha", "hora_inicio", "id")
    )
    for r in qs:
        yield [
            r.pk, r.aula.nombre, r.fecha, r.hora_inicio, r.hora_fin, r.titulo,
            r.curso.nombre if r.curso else None, r.profesor,
            SEPARADOR_MATERIALES.join(m.nombre for m in r.materiales.all()),
            r.notas, r.estado,
            _local_naive(r.creada_en), r.creada_por.get_username(),
            _local_naive(r.gestionada_en),
            r.gestionada_por.get_username() if r.gestionada_por else None,
            r.motivo_rechazo,
        ]


def filas_aulas():
    for a in Aula.objects.order_by("nombre"):
        yield [a.pk, a.nombre, a.ubicacion, a.capacidad, a.activa]


def filas_cursos():
    for c in Curso.objects.order_by("nombre"):
        yield [c.pk, c.nombre, c.activo]


def filas_materiales():
    for m in Material.objects.order_by("orden", "nombre"):
        yield [m.pk, m.nombre, m.activo, m.orden]


def filas_auditoria(queryset_reservas):
    eventos = (
        EventoReserva.objects.filter(reserva__in=queryset_reservas)
        .select_related("usuario")
        .order_by("fecha", "id")
    )
    for e in eventos:
        yield [e.pk, e.reserva_id, _local_naive(e.fecha), e.tipo, e.usuario.get_username(), e.detalle]


# ------------------------------------------------------------------------------- CSV
def _celda_csv(valor) -> str:
    if valor is None:
        return ""
    if isinstance(valor, bool):
        return "Sí" if valor else "No"
    if isinstance(valor, datetime):  # antes que date: datetime hereda de date
        return valor.isoformat(sep=" ", timespec="seconds")
    if isinstance(valor, date):
        return valor.isoformat()
    if isinstance(valor, time):
        return valor.strftime("%H:%M")
    texto = str(valor)
    if texto.startswith(_INICIOS_PELIGROSOS):
        texto = "'" + texto  # Excel lo muestra como texto literal
    return texto


def respuesta_csv(nombre_base: str, cabecera, filas, delimitador: str = ",") -> HttpResponse:
    ahora = timezone.localtime()
    respuesta = HttpResponse(content_type="text/csv; charset=utf-8")
    respuesta["Content-Disposition"] = f'attachment; filename="{nombre_base}_{ahora:%Y%m%d_%H%M}.csv"'
    respuesta.write("﻿")  # BOM para Excel
    escritor = csv.writer(respuesta, delimiter=delimitador, lineterminator="\r\n")
    escritor.writerow(cabecera)
    for fila in filas:
        escritor.writerow([_celda_csv(v) for v in fila])
    return respuesta


# ----------------------------------------------------------------------------- Excel
_FORMATOS = {datetime: "DD/MM/YYYY HH:MM", date: "DD/MM/YYYY", time: "HH:MM"}


def _largo_visible(valor) -> int:
    """Caracteres que ocupa el valor ya formateado (para dimensionar la columna)."""
    if isinstance(valor, datetime):
        return 16
    if isinstance(valor, date):
        return 10
    if isinstance(valor, time):
        return 5
    return len(str(valor)) if valor is not None else 0


def _escribir_hoja(libro: Workbook, titulo: str, cabecera, filas):
    hoja = libro.create_sheet(titulo)
    hoja.append(cabecera)
    for celda in hoja[1]:
        celda.font = Font(bold=True, color="FFFFFF")
        celda.fill = PatternFill("solid", fgColor="2F4858")
        celda.alignment = Alignment(vertical="center")

    anchos = [len(str(c)) for c in cabecera]
    for fila in filas:
        hoja.append([None] * len(fila))
        numero = hoja.max_row
        for col, valor in enumerate(fila, start=1):
            celda = hoja.cell(row=numero, column=col)
            if isinstance(valor, bool):
                valor = "Sí" if valor else "No"
            celda.value = valor
            if isinstance(valor, str):
                celda.data_type = "s"  # texto literal: nunca se interpreta como fórmula
                celda.alignment = Alignment(wrap_text=False, vertical="top")
            elif valor is not None:
                formato = next((f for t, f in _FORMATOS.items() if isinstance(valor, t)), None)
                if formato:
                    celda.number_format = formato
            anchos[col - 1] = max(anchos[col - 1], min(_largo_visible(valor), 60))

    for col, ancho in enumerate(anchos, start=1):
        hoja.column_dimensions[get_column_letter(col)].width = ancho + 2
    hoja.freeze_panes = "A2"
    hoja.auto_filter.ref = hoja.dimensions


def respuesta_excel(nombre_base: str, hojas) -> HttpResponse:
    """hojas: iterable de (titulo, cabecera, filas)."""
    libro = Workbook()
    libro.remove(libro.active)
    for titulo, cabecera, filas in hojas:
        _escribir_hoja(libro, titulo, cabecera, filas)
    buffer = BytesIO()
    libro.save(buffer)
    ahora = timezone.localtime()
    respuesta = HttpResponse(
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    respuesta["Content-Disposition"] = f'attachment; filename="{nombre_base}_{ahora:%Y%m%d_%H%M}.xlsx"'
    return respuesta


def exportar_excel_completo(queryset_reservas) -> HttpResponse:
    return respuesta_excel(
        "reservas",
        [
            ("Reservas", CABECERA_RESERVAS, filas_reservas(queryset_reservas)),
            ("Aulas", CABECERA_AULAS, filas_aulas()),
            ("Cursos", CABECERA_CURSOS, filas_cursos()),
            ("Materiales", CABECERA_MATERIALES, filas_materiales()),
            ("Auditoría", CABECERA_AUDITORIA, filas_auditoria(queryset_reservas)),
        ],
    )