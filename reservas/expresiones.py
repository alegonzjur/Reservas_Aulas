"""Expresiones SQL propias para la restricción anti-solape.

Guardamos la reserva como (fecha, hora_inicio, hora_fin) porque es lo natural para
consultar y exportar. Para que PostgreSQL pueda comparar solapes necesita un *rango*:

    TSRANGE(fecha + hora_inicio, fecha + hora_fin, '[)')

`date + time` devuelve un `timestamp` sin zona y es una operación inmutable, lo que
PostgreSQL exige para usarla dentro de una restricción. '[)' significa "incluye el
inicio, excluye el fin", así 10:00-11:00 y 11:00-12:00 NO se solapan.
"""
from django.contrib.postgres.fields import DateTimeRangeField
from django.db.models import DateTimeField, Func


class FechaMasHora(Func):
    arity = 2
    arg_joiner = " + "
    template = "(%(expressions)s)"
    output_field = DateTimeField()


class RangoFechaHora(Func):
    function = "TSRANGE"
    output_field = DateTimeRangeField()