from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone

from .models import Aula, Curso, Material, Reserva


def _franja_valida(inicio, fin) -> bool:
    return bool(
        inicio
        and fin
        and fin > inicio
        and inicio >= settings.HORA_APERTURA
        and fin <= settings.HORA_CIERRE
    )


def _texto_conflictos(conflictos) -> str:
    franjas = ", ".join(f"{c.hora_inicio:%H:%M}-{c.hora_fin:%H:%M}" for c in conflictos)
    return f"Esa aula ya tiene una reserva aceptada ({franjas}) que se solapa con la franja pedida."


class PeticionForm(forms.ModelForm):
    """Formulario de petición de reserva (profesorado) o de reserva directa (administradores)."""

    aceptar_directamente = forms.BooleanField(
        required=False,
        initial=True,
        label="Aceptar directamente",
        help_text="Para reservas hechas por teléfono o en persona. Desmárcalo para dejarla pendiente.",
    )

    class Meta:
        model = Reserva
        fields = [
            "aula", "fecha", "hora_inicio", "hora_fin", "titulo", "curso", "profesor",
            "materiales", "notas",
        ]
        labels = {
            "aula": "Aula",
            "fecha": "Día",
            "hora_inicio": "Hora de inicio",
            "hora_fin": "Hora de fin",
            "titulo": "Título",
            "curso": "Curso",
            "profesor": "Profesor/a",
            "materiales": "Material",
            "notas": "Notas",
        }
        help_texts = {
            "titulo": "Asignatura, taller, evento… lo que se hará en el aula.",
            "curso": "Grupo que ocupará el aula. Si no ves el tuyo, avisa a mantenimiento.",
            "profesor": "Nombre de quien dará la clase. Puede ser distinto del tuyo.",
            "materiales": "Marca lo que necesitas tener preparado en el aula.",
            "notas": "Opcional: disposición de mesas, detalles del material, etc.",
        }
        widgets = {
            "fecha": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "hora_inicio": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
            "hora_fin": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
            "titulo": forms.TextInput(attrs={"autocomplete": "off", "placeholder": "Por ejemplo: Anatomía I"}),
            "profesor": forms.TextInput(attrs={"autocomplete": "off", "placeholder": "Nombre y apellidos"}),
            "materiales": forms.CheckboxSelectMultiple,
            "notas": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, es_admin: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        aula = self.fields["aula"]
        aula.queryset = Aula.objects.filter(activa=True)
        aula.empty_label = "Escribe para buscar un aula…"
        aula.widget.attrs["data-buscable"] = "true"
        aula.widget.attrs["data-placeholder"] = "Escribe para buscar un aula…"
        aula.widget.attrs["data-sin-resultados"] = "Ningún aula coincide con lo escrito"

        # En el modelo estos campos admiten vacío (reservas antiguas); en el formulario no.
        self.fields["titulo"].required = True
        curso = self.fields["curso"]
        curso.required = True
        curso.queryset = Curso.objects.filter(activo=True)
        curso.empty_label = "Escribe para buscar un curso…"
        curso.widget.attrs["data-buscable"] = "true"
        curso.widget.attrs["data-placeholder"] = "Escribe para buscar un curso…"
        curso.widget.attrs["data-sin-resultados"] = "Ningún curso coincide con lo escrito"
        self.fields["materiales"].queryset = Material.objects.filter(activo=True)

        paso = settings.PASO_MINUTOS * 60
        for campo in ("hora_inicio", "hora_fin"):
            self.fields[campo].widget.attrs.update(
                {
                    "step": paso,
                    "min": f"{settings.HORA_APERTURA:%H:%M}",
                    "max": f"{settings.HORA_CIERRE:%H:%M}",
                }
            )
        self.fields["fecha"].widget.attrs["min"] = timezone.localdate().isoformat()

        if not es_admin:
            del self.fields["aceptar_directamente"]

    def clean(self):
        datos = super().clean()
        fecha = datos.get("fecha")
        inicio = datos.get("hora_inicio")
        fin = datos.get("hora_fin")
        aula = datos.get("aula")

        if fecha:
            if fecha < timezone.localdate():
                self.add_error("fecha", "No se pueden pedir reservas para días que ya han pasado.")
            elif fecha.weekday() >= 5:
                self.add_error(
                    "fecha",
                    "Solo se puede reservar de lunes a viernes. Para otros días, habla con mantenimiento.",
                )

        for campo, valor in (("hora_inicio", inicio), ("hora_fin", fin)):
            if valor and (valor.minute % settings.PASO_MINUTOS or valor.second):
                self.add_error(campo, f"Usa horas en múltiplos de {settings.PASO_MINUTOS} minutos.")

        # Bloquea la petición si la franja ya está ocupada por una reserva aceptada.
        # (Varias peticiones PENDIENTES para la misma franja sí están permitidas.)
        if aula and fecha and _franja_valida(inicio, fin) and not self.errors:
            conflictos = list(Reserva.aceptadas_solapadas(aula, fecha, inicio, fin))
            if conflictos:
                raise ValidationError(_texto_conflictos(conflictos))
        return datos


class DisponibilidadForm(forms.Form):
    """Valida los parámetros del aviso en vivo de disponibilidad (sin mostrar errores)."""

    aula = forms.ModelChoiceField(Aula.objects.filter(activa=True))
    fecha = forms.DateField()
    hora_inicio = forms.TimeField()
    hora_fin = forms.TimeField()

    def clean(self):
        datos = super().clean()
        if not self.errors and not _franja_valida(datos["hora_inicio"], datos["hora_fin"]):
            raise ValidationError("Franja no válida.")
        return datos


class FiltroReservasForm(forms.Form):
    """Filtros compartidos por el listado de reservas y la exportación."""

    estado = forms.ChoiceField(
        choices=[("", "Todos")] + list(Reserva.Estado.choices), required=False
    )
    aula = forms.ModelChoiceField(Aula.objects.all(), required=False, empty_label="Todas")
    desde = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    hasta = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    q = forms.CharField(required=False, label="Profesor, título o notas")

    def clean(self):
        datos = super().clean()
        if datos.get("desde") and datos.get("hasta") and datos["desde"] > datos["hasta"]:
            raise ValidationError("La fecha «desde» no puede ser posterior a «hasta».")
        return datos

    def filtrar(self, queryset):
        """Aplica los filtros rellenados. Solo debe llamarse con el formulario ya validado."""
        d = self.cleaned_data
        if d.get("estado"):
            queryset = queryset.filter(estado=d["estado"])
        if d.get("aula"):
            queryset = queryset.filter(aula=d["aula"])
        if d.get("desde"):
            queryset = queryset.filter(fecha__gte=d["desde"])
        if d.get("hasta"):
            queryset = queryset.filter(fecha__lte=d["hasta"])
        if d.get("q"):
            queryset = queryset.filter(
                Q(profesor__icontains=d["q"]) | Q(titulo__icontains=d["q"]) | Q(notas__icontains=d["q"])
            )
        return queryset


class FiltroExportacionForm(FiltroReservasForm):
    delimitador = forms.ChoiceField(
        choices=[(",", "Coma (,)"), (";", "Punto y coma (;)")],
        required=False,
        initial=",",
        label="Separador del CSV",
        help_text="La coma es lo estándar para migrar; el punto y coma se abre directo en Excel en español.",
    )

    def clean_delimitador(self):
        return self.cleaned_data.get("delimitador") or ","