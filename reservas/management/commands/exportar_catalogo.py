"""Exporta aulas, cursos y materiales a un fichero JSON para llevarlos a otro servidor.

    python manage.py exportar_catalogo catalogo.json

No exporta reservas ni usuarios. Se importa con «importar_catalogo».
"""
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from reservas.models import Aula, Curso, Material

VERSION = 1


def volcar_catalogo() -> dict:
    return {
        "version": VERSION,
        "aulas": [
            {"nombre": a.nombre, "ubicacion": a.ubicacion, "capacidad": a.capacidad, "activa": a.activa}
            for a in Aula.objects.order_by("nombre")
        ],
        "cursos": [{"nombre": c.nombre, "activo": c.activo} for c in Curso.objects.order_by("nombre")],
        "materiales": [
            {"nombre": m.nombre, "activo": m.activo, "orden": m.orden}
            for m in Material.objects.order_by("orden", "nombre")
        ],
    }


class Command(BaseCommand):
    help = "Exporta aulas, cursos y materiales a un fichero JSON."

    def add_arguments(self, parser):
        parser.add_argument("fichero", help="Fichero de salida, p. ej. catalogo.json")

    def handle(self, *args, fichero, **opciones):
        datos = volcar_catalogo()
        ruta = Path(fichero)
        try:
            ruta.write_text(json.dumps(datos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            raise CommandError(f"No se puede escribir {ruta}: {exc}") from exc
        self.stdout.write(
            self.style.SUCCESS(
                f"Exportado a {ruta}: {len(datos['aulas'])} aulas, {len(datos['cursos'])} cursos, "
                f"{len(datos['materiales'])} materiales."
            )
        )