"""Cálculo de la vista semanal (lunes a viernes) y de la lista de pendientes.

Toda la geometría se calcula aquí, en Python, para poder probarla; la plantilla solo
pinta los números ya hechos.
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from django.conf import settings
from django.utils import timezone
from django.utils.formats import date_format

from .models import Reserva

DIAS_VISIBLES = 5  # lunes a viernes

# Anchos de la cuadrícula (px). Un día con N reservas simultáneas necesita N carriles
# legibles, así que su columna crece con N (y el calendario admite scroll horizontal).
ANCHO_EJE_PX = 54
ANCHO_MIN_DIA_PX = 150
ANCHO_CARRIL_PX = 100


def lunes_de(dia: date) -> date:
    return dia - timedelta(days=dia.weekday())


def lunes_por_defecto(hoy: date | None = None) -> date:
    """Lunes de la semana a mostrar al abrir el panel.

    En fin de semana se salta a la semana siguiente, que es la que interesa preparar.
    """
    hoy = hoy or timezone.localdate()
    if hoy.weekday() >= 5:
        return hoy + timedelta(days=7 - hoy.weekday())
    return lunes_de(hoy)


def _minutos(hora) -> int:
    return hora.hour * 60 + hora.minute


def _se_solapan(a, b) -> bool:
    return a.hora_inicio < b.hora_fin and b.hora_inicio < a.hora_fin


@dataclass
class Bloque:
    """Una reserva colocada en la cuadrícula de un día."""

    reserva: Reserva
    top: float  # % desde la apertura
    alto: float  # % de la altura total
    carril: int = 0
    carriles: int = 1

    @property
    def estilo(self) -> str:
        # Formato con punto decimal fijo: en español, un float renderizado por la
        # plantilla saldría con coma y rompería el CSS.
        izquierda = self.carril / self.carriles * 100
        ancho = 100 / self.carriles
        return (
            f"--top:{self.top:.3f}%;--alto:{self.alto:.3f}%;"
            f"--izq:{izquierda:.3f}%;--ancho:{ancho:.3f}%"
        )


def repartir_en_carriles(reservas: list[Reserva]) -> list[tuple[Reserva, int, int]]:
    """Reparte reservas de un mismo día en carriles verticales para que las que se
    superponen en el tiempo se pinten una al lado de otra (como en un calendario).

    Devuelve tuplas (reserva, carril, nº de carriles del grupo). Un "grupo" es un
    conjunto de reservas encadenadas por solapes: todas comparten el mismo número de
    carriles para que el ancho sea homogéneo.
    """
    ordenadas = sorted(reservas, key=lambda r: (r.hora_inicio, r.hora_fin, r.pk))
    grupos: list[list[Reserva]] = []
    fin_grupo = None
    for r in ordenadas:
        if grupos and fin_grupo is not None and r.hora_inicio < fin_grupo:
            grupos[-1].append(r)
            fin_grupo = max(fin_grupo, r.hora_fin)
        else:
            grupos.append([r])
            fin_grupo = r.hora_fin

    resultado = []
    for grupo in grupos:
        fines_de_carril: list = []
        asignaciones = []
        for r in grupo:
            for i, fin in enumerate(fines_de_carril):
                if fin <= r.hora_inicio:  # el carril i ya está libre
                    fines_de_carril[i] = r.hora_fin
                    asignaciones.append((r, i))
                    break
            else:
                fines_de_carril.append(r.hora_fin)
                asignaciones.append((r, len(fines_de_carril) - 1))
        total = len(fines_de_carril)
        resultado.extend((r, carril, total) for r, carril in asignaciones)
    return resultado


def texto_rango(lunes: date, viernes: date) -> str:
    """«Del 19 al 23 de octubre de 2026», o con ambos meses (y años) si cruza."""
    if lunes.month == viernes.month:
        return f"Del {lunes.day} al {viernes.day} de {date_format(viernes, 'F')} de {viernes.year}"
    if lunes.year == viernes.year:
        return (
            f"Del {lunes.day} de {date_format(lunes, 'F')} al {viernes.day} "
            f"de {date_format(viernes, 'F')} de {viernes.year}"
        )
    return (
        f"Del {lunes.day} de {date_format(lunes, 'F')} de {lunes.year} al {viernes.day} "
        f"de {date_format(viernes, 'F')} de {viernes.year}"
    )


def construir_semana(lunes: date, aula_id: int | None = None) -> dict:
    hoy = timezone.localdate()
    apertura = _minutos(settings.HORA_APERTURA)
    total = _minutos(settings.HORA_CIERRE) - apertura

    dias_fecha = [lunes + timedelta(days=i) for i in range(DIAS_VISIBLES)]
    # Solo ocupan hueco las pendientes y las aceptadas; las rechazadas no se pintan.
    reservas = Reserva.objects.filter(
        fecha__range=(dias_fecha[0], dias_fecha[-1]),
        estado__in=[Reserva.Estado.PENDIENTE, Reserva.Estado.ACEPTADA],
    ).select_related("aula")
    if aula_id:
        reservas = reservas.filter(aula_id=aula_id)

    por_dia: dict[date, list[Reserva]] = defaultdict(list)
    for r in reservas:
        por_dia[r.fecha].append(r)

    dias = []
    for d in dias_fecha:
        bloques = [
            Bloque(
                reserva=r,
                top=(_minutos(r.hora_inicio) - apertura) / total * 100,
                alto=(_minutos(r.hora_fin) - _minutos(r.hora_inicio)) / total * 100,
                carril=carril,
                carriles=carriles,
            )
            for r, carril, carriles in repartir_en_carriles(por_dia[d])
        ]
        dias.append(
            {
                "fecha": d,
                "nombre": date_format(d, "l").capitalize(),
                "es_hoy": d == hoy,
                "bloques": bloques,
            }
        )

    primera_hora = settings.HORA_APERTURA.hour
    horas = [
        {"i": i, "texto": f"{primera_hora + i:02d}:00"} for i in range(total // 60 + 1)
    ]

    anchos_dia = [
        max(ANCHO_MIN_DIA_PX, max((b.carriles for b in d["bloques"]), default=1) * ANCHO_CARRIL_PX)
        for d in dias
    ]
    columnas_css = f"{ANCHO_EJE_PX}px " + " ".join(f"minmax({a}px, 1fr)" for a in anchos_dia)

    return {
        "lunes": lunes,
        "viernes": dias_fecha[-1],
        "rango_texto": texto_rango(lunes, dias_fecha[-1]),
        "columnas_css": columnas_css,
        "ancho_minimo": ANCHO_EJE_PX + sum(anchos_dia),
        "anterior": lunes - timedelta(days=7),
        "siguiente": lunes + timedelta(days=7),
        "es_semana_actual": lunes == lunes_por_defecto(hoy),
        "hay_reservas": any(d["bloques"] for d in dias),
        "dias": dias,
        "horas": horas,
        "n_horas": total // 60,
    }


def pendientes_para_revisar(limite: int = 50):
    """Peticiones pendientes (de todas las fechas) con avisos de conflicto.

    Cada elemento recibe tres atributos:
      conflicto    -> solapa con una reserva ya ACEPTADA (no se puede aceptar tal cual)
      competidoras -> nº de otras peticiones PENDIENTES que piden la misma franja
      caducada     -> su fecha ya pasó sin que nadie la gestionara
    Devuelve (lista, total_pendientes).
    """
    base = Reserva.objects.filter(estado=Reserva.Estado.PENDIENTE)
    total = base.count()
    pendientes = list(base.select_related("aula").order_by("fecha", "hora_inicio", "id")[:limite])

    fechas = {p.fecha for p in pendientes}
    aceptadas: dict[tuple, list[Reserva]] = defaultdict(list)
    for a in Reserva.objects.filter(estado=Reserva.Estado.ACEPTADA, fecha__in=fechas):
        aceptadas[(a.aula_id, a.fecha)].append(a)
    pendientes_por_hueco: dict[tuple, list[Reserva]] = defaultdict(list)
    for p in pendientes:
        pendientes_por_hueco[(p.aula_id, p.fecha)].append(p)

    hoy = timezone.localdate()
    for p in pendientes:
        clave = (p.aula_id, p.fecha)
        p.conflicto = any(_se_solapan(p, a) for a in aceptadas.get(clave, []))
        p.competidoras = sum(
            1 for q in pendientes_por_hueco[clave] if q.pk != p.pk and _se_solapan(p, q)
        )
        p.caducada = p.fecha < hoy
    return pendientes, total