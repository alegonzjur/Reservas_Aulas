"""Configuración del proyecto de reservas de aulas.

Todo lo que cambia entre equipos (claves, base de datos, correo) se lee de variables
de entorno o del fichero ".env" (ver ".env.example"). Nada sensible va en el código.
"""
import os
from datetime import time
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(nombre: str, por_defecto: bool = False) -> bool:
    valor = os.environ.get(nombre)
    if valor is None:
        return por_defecto
    return valor.strip().lower() in {"1", "true", "t", "si", "sí", "yes", "y"}


def env_lista(nombre: str, por_defecto: str = "") -> list[str]:
    return [x.strip() for x in os.environ.get(nombre, por_defecto).split(",") if x.strip()]


# --------------------------------------------------------------------------- Seguridad
DEBUG = env_bool("DJANGO_DEBUG", False)

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "solo-para-desarrollo-no-usar-en-produccion"
    else:
        raise ImproperlyConfigured(
            "Falta DJANGO_SECRET_KEY. Defínela en el fichero .env (ver .env.example)."
        )

ALLOWED_HOSTS = env_lista("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_lista("DJANGO_CSRF_TRUSTED_ORIGINS")

_COOKIES_SEGURAS = env_bool("DJANGO_COOKIES_SEGURAS", False)
SESSION_COOKIE_SECURE = _COOKIES_SEGURAS
CSRF_COOKIE_SECURE = _COOKIES_SEGURAS
SESSION_COOKIE_AGE = 60 * 60 * 12  # la sesión caduca a las 12 horas

# --------------------------------------------------------------------------- Aplicaciones
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "reservas",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",  # sirve /static/ sin depender de nginx/IIS
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "reservas.context_processors.roles",
            ],
        },
    },
]

# --------------------------------------------------------------------------- Base de datos
# PostgreSQL es obligatorio: la restricción anti-solape usa una "exclusion constraint"
# con btree_gist que SQLite/MySQL no tienen.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("DB_NAME", "reservas_aulas"),
        "USER": os.environ.get("DB_USER", "reservas"),
        "PASSWORD": os.environ.get("DB_PASSWORD", ""),
        "HOST": os.environ.get("DB_HOST", "localhost"),
        "PORT": os.environ.get("DB_PORT", "5432"),
    }
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --------------------------------------------------------------------------- Autenticación
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "inicio"
LOGOUT_REDIRECT_URL = "login"

# --------------------------------------------------------------------------- Idioma y hora
LANGUAGE_CODE = "es"
TIME_ZONE = "Europe/Madrid"
USE_I18N = True
USE_TZ = True

# --------------------------------------------------------------------------- Estáticos
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    # Comprime pero NO renombra con hash: así no hace falta un manifiesto y funciona
    # igual en desarrollo, tests y producción.
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

# --------------------------------------------------------------------------- Correo
EMAIL_BACKEND = os.environ.get(
    "EMAIL_BACKEND",
    "django.core.mail.backends.console.EmailBackend"
    if DEBUG
    else "django.core.mail.backends.smtp.EmailBackend",
)
EMAIL_HOST = os.environ.get("EMAIL_HOST", "localhost")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL", False)
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_TIMEOUT = 10  # un SMTP caído no debe bloquear el formulario más de 10 s
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", EMAIL_HOST_USER or "reservas@localhost")

# --------------------------------------------------------------------------- Reglas de negocio
# Franja en la que se pueden pedir reservas y granularidad de las horas.
# Si el centro cambia de horario, se cambia aquí (la base de datos no lo fija a propósito).
HORA_APERTURA = time(8, 0)
HORA_CIERRE = time(21, 0)
PASO_MINUTOS = 5

# Aviso por correo cuando entra una petición pendiente.
RESERVAS_EMAIL_DESTINO = env_lista("RESERVAS_EMAIL_DESTINO")
SITE_URL = os.environ.get("SITE_URL", "http://localhost:8000").rstrip("/")

# --------------------------------------------------------------------------- Logs
# Todo va a la salida de error estándar. Como servicio de Windows, NSSM la redirige a un
# fichero con rotación (ver DESPLIEGUE.md); en desarrollo se ve en la terminal.
#
# El logger "django" es imprescindible en producción: con DEBUG=False, Django solo envía
# los errores 500 por correo a ADMINS (que aquí está vacío), así que sin esto el traceback
# de un fallo no se vería en ninguna parte. Al definirlo aquí sustituye al de Django.
_NIVEL_LOG = os.environ.get("DJANGO_LOG_LEVEL", "INFO").strip().upper() or "INFO"
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"simple": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"consola": {"class": "logging.StreamHandler", "formatter": "simple"}},
    "loggers": {
        "reservas": {"handlers": ["consola"], "level": _NIVEL_LOG},
        "django": {"handlers": ["consola"], "level": "WARNING"},  # incluye django.request (500)
        "waitress": {"handlers": ["consola"], "level": "INFO"},
    },
}