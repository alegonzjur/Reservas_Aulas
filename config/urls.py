"""Rutas raíz del proyecto."""
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

admin.site.site_header = "Reservas de aulas · Administración"
admin.site.site_title = "Reservas de aulas"
admin.site.index_title = "Gestión de datos"

urlpatterns = [
    path("admin/", admin.site.urls),
    path(
        "login/",
        auth_views.LoginView.as_view(
            template_name="registration/login.html", redirect_authenticated_user=True
        ),
        name="login",
    ),
    # En Django 5 el cierre de sesión solo acepta POST (el botón del menú es un formulario).
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", include("reservas.urls")),
]