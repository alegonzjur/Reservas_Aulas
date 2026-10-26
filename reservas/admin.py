"""Panel de administración de Django (/admin/).

Se usa para mantener el catálogo de aulas y, de forma excepcional, corregir datos de una
reserva. Cambiar el ESTADO (aceptar/rechazar) solo se puede hacer desde el panel de la
aplicación, para que nunca se salte la auditoría.
"""
from django.contrib import admin

from .models import Aula, Curso, EventoReserva, Material, Reserva
from .services import registrar_evento


@admin.register(Aula)
class AulaAdmin(admin.ModelAdmin):
    list_display = ("nombre", "ubicacion", "capacidad", "activa")
    list_filter = ("activa",)
    search_fields = ("nombre", "ubicacion")


@admin.register(Curso)
class CursoAdmin(admin.ModelAdmin):
    list_display = ("nombre", "activo")
    list_filter = ("activo",)
    search_fields = ("nombre",)


@admin.register(Material)
class MaterialAdmin(admin.ModelAdmin):
    list_display = ("nombre", "orden", "activo")
    list_editable = ("orden",)
    list_filter = ("activo",)
    search_fields = ("nombre",)


@admin.register(Reserva)
class ReservaAdmin(admin.ModelAdmin):
    list_display = ("__str__", "titulo", "curso", "profesor", "estado", "gestionada_por", "gestionada_en")
    list_filter = ("estado", "aula", "curso")
    search_fields = ("profesor", "titulo", "notas")
    filter_horizontal = ("materiales",)
    date_hierarchy = "fecha"
    readonly_fields = (
        "estado", "serie", "creada_por", "creada_en", "gestionada_por", "gestionada_en", "motivo_rechazo",
    )

    def has_add_permission(self, request):
        return False  # las reservas se crean desde el formulario de la aplicación

    def has_delete_permission(self, request, obj=None):
        # Borrar destruiría el rastro de auditoría: solo un superusuario.
        return request.user.is_superuser

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if change:
            campos = ", ".join(form.changed_data)
            registrar_evento(obj, EventoReserva.Tipo.EDITADA, request.user, f"Editada en administración: {campos}")


@admin.register(EventoReserva)
class EventoReservaAdmin(admin.ModelAdmin):
    list_display = ("fecha", "reserva", "tipo", "usuario", "detalle")
    list_filter = ("tipo",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False