from django.urls import path

from . import views

urlpatterns = [
    path("", views.inicio, name="inicio"),
    # Profesorado (y administradores)
    path("reservar/", views.reservar, name="reservar"),
    path("reservar/disponibilidad/", views.disponibilidad, name="disponibilidad"),
    # Administradores
    path("panel/", views.panel, name="panel"),
    path("panel/contenido/", views.panel_contenido, name="panel_contenido"),
    path("panel/reservas/", views.listado, name="listado"),
    path("panel/reservas/<int:pk>/", views.reserva_detalle, name="reserva_detalle"),
    path("panel/reservas/<int:pk>/accion/", views.reserva_accion, name="reserva_accion"),
    path("panel/reservas/<int:pk>/serie/", views.reserva_serie_accion, name="reserva_serie_accion"),
    path("panel/exportar/", views.exportar, name="exportar"),
]