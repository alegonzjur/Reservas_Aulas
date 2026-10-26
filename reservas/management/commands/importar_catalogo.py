"""Importa aulas, cursos y materiales desde el JSON de «exportar_catalogo».

    python manage.py importar_catalogo catalogo.json --simulacro   (solo muestra qué haría)
    python manage.py importar_catalogo catalogo.json

Se identifica cada elemento por su nombre: si existe se actualiza, si no se crea. Nunca borra
nada, así que es seguro repetirlo. Todo ocurre en una sola transacción: si algo falla, no se
guarda nada.
"""
import json
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from reservas.models import Aula, Curso, Material

from .exportar_catalogo import VERSION

SECCIONES = (("aulas", Aula, "aulas"), ("cursos", Curso, "cursos"), ("materiales", Material, "materiales"))


class _Simulacro(Exception):
    """Sirve para deshacer la transacción en el modo --simulacro."""


class Command(BaseCommand):
    help = "Importa aulas, cursos y materiales desde un JSON (crea o actualiza por nombre)."

    def add_arguments(self, parser):
        parser.add_argument("fichero", help="Fichero generado por exportar_catalogo")
        parser.add_argument("--simulacro", action="store_true", help="No guarda nada; solo informa.")

    def handle(self, *args, fichero, simulacro, **opciones):
        ruta = Path(fichero)
        try:
            datos = json.loads(ruta.read_text(encoding="utf-8-sig"))
        except OSError as exc:
            raise CommandError(f"No se puede leer {ruta}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise CommandError(f"{ruta} no es un JSON válido: {exc}") from exc
        if not isinstance(datos, dict) or datos.get("version") != VERSION:
            raise CommandError(f"{ruta} no es un catálogo exportado por esta aplicación (versión {VERSION}).")

        resumen = {}
        try:
            with transaction.atomic():
                for clave, modelo, _ in SECCIONES:
                    resumen[clave] = self._importar(modelo, datos.get(clave, []), clave)
                if simulacro:
                    raise _Simulacro
        except _Simulacro:
            pass

        for clave, (creados, cambiados, iguales) in resumen.items():
            self.stdout.write(f"{clave:11} {creados} nuevos, {cambiados} actualizados, {iguales} sin cambios")
        if simulacro:
            self.stdout.write(self.style.WARNING("Simulacro: no se ha guardado nada."))
        else:
            self.stdout.write(self.style.SUCCESS("Catálogo importado."))

    def _importar(self, modelo, filas, clave):
        if not isinstance(filas, list):
            raise CommandError(f"«{clave}» debe ser una lista.")
        campos = {f.name for f in modelo._meta.get_fields() if f.concrete and not f.primary_key}
        creados = cambiados = iguales = 0
        for i, fila in enumerate(filas, 1):
            if not isinstance(fila, dict) or not str(fila.get("nombre", "")).strip():
                raise CommandError(f"{clave} #{i}: falta el nombre.")
            valores = {k: v for k, v in fila.items() if k in campos and k != "nombre"}
            nombre = fila["nombre"].strip()
            objeto = modelo.objects.filter(nombre=nombre).first()
            nuevo = objeto is None
            if nuevo:
                objeto = modelo(nombre=nombre)
            antes = {k: getattr(objeto, k) for k in valores}
            for k, v in valores.items():
                setattr(objeto, k, v)
            try:
                objeto.full_clean()
            except ValidationError as exc:
                raise CommandError(f"{clave} «{nombre}»: {'; '.join(exc.messages)}") from exc
            if nuevo:
                objeto.save()
                creados += 1
            elif antes != {k: getattr(objeto, k) for k in valores}:
                objeto.save()
                cambiados += 1
            else:
                iguales += 1
        return creados, cambiados, iguales