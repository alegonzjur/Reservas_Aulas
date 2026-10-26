#!/usr/bin/env python
"""Ejecutor del servidor de producción de «Reservas de aulas».

    python run.py              Comprueba la instalación y arranca el servidor (Waitress).
    python run.py --check      Solo comprueba la instalación y sale (código 0 si todo va bien).
    python run.py --port 8080  Cambia el puerto; --host y --threads funcionan igual.

Es el programa que ejecuta el servicio de Windows creado con NSSM (ver DESPLIEGUE.md), pero
también sirve para probar a mano en una terminal.

Por qué existe en lugar de «manage.py runserver»:
  * runserver es solo para desarrollo (un hilo, sin endurecer, recarga de código).
  * Waitress es un servidor WSGI de producción escrito en Python puro: funciona en Windows
    sin compiladores y se apaga limpiamente cuando el servicio recibe la orden de parar.

Antes de atender peticiones se comprueba todo lo que suele fallar tras un despliegue (clave
secreta, PostgreSQL, migraciones, estáticos). Si algo falla, el programa termina con código 1
y un mensaje que dice qué hacer; así el servicio no queda «arrancado» pero roto, y el motivo
queda en el fichero de log de NSSM.

Los valores se leen del fichero .env (ver .env.example) y los parámetros de la línea de
comandos tienen prioridad sobre él.
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
logger = logging.getLogger("reservas.run")

HOST_POR_DEFECTO = "0.0.0.0"
PUERTO_POR_DEFECTO = 8002
HILOS_POR_DEFECTO = 8
_HOSTS_LOCALES = {"localhost", "127.0.0.1", "[::1]", "::1"}
# Un formulario de reserva ocupa pocos KB; más de esto no es una petición legítima.
TAMANO_MAXIMO_PETICION = 5 * 1024 * 1024


class ErrorDeArranque(Exception):
    """Algo impide arrancar. El mensaje está pensado para quien administra el servidor."""


# ----------------------------------------------------------------------------- Entorno
def preparar_consola() -> None:
    """Fuerza UTF-8 en la salida.

    Cuando NSSM redirige la salida a un fichero, Python en Windows usa la página de códigos
    del sistema (cp1252) y un carácter fuera de ella provocaría un error al escribir el log.
    """
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except (AttributeError, ValueError):
            pass


def preparar_dll_de_conda() -> None:
    """En Windows, permite que un entorno Anaconda funcione sin haberlo «activado».

    Un servicio ejecuta python.exe directamente, sin «conda activate». Python de conda guarda
    parte de sus DLL (OpenSSL, SQLite…) en <entorno>\\Library\\bin; sin esa carpeta en el PATH,
    `import ssl` falla y con él el envío de correo con TLS. Aquí la añadimos nosotros.
    No hace nada fuera de Windows ni en entornos que no son de conda.
    """
    if os.name != "nt":
        return
    prefijo = Path(sys.prefix)
    if not (prefijo / "conda-meta").is_dir():
        return
    carpetas = [
        prefijo / r"Library\mingw-w64\bin",
        prefijo / r"Library\usr\bin",
        prefijo / r"Library\bin",
        prefijo / "Scripts",
    ]
    for carpeta in carpetas:
        if carpeta.is_dir():
            os.add_dll_directory(str(carpeta))
            os.environ["PATH"] = f"{carpeta}{os.pathsep}{os.environ.get('PATH', '')}"


def cargar_entorno() -> None:
    """Carga .env (sin pisar las variables que ya estén definidas en el sistema)."""
    try:
        from dotenv import load_dotenv
    except ImportError as exc:
        raise ErrorDeArranque(
            f"Falta instalar las dependencias ({exc}). Ejecuta: pip install -r requirements.txt"
        ) from exc
    load_dotenv(BASE_DIR / ".env")


def preparar_proyecto() -> None:
    """Hace que el programa funcione igual se lance desde donde se lance."""
    os.chdir(BASE_DIR)
    if str(BASE_DIR) not in sys.path:
        sys.path.insert(0, str(BASE_DIR))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")


# ------------------------------------------------------------------------- Argumentos
def _numero_de_entorno(entorno, nombre: str, por_defecto: int) -> int:
    valor = (entorno.get(nombre) or "").strip()
    if not valor:
        return por_defecto
    try:
        return int(valor)
    except ValueError:
        raise ErrorDeArranque(f"{nombre} debe ser un número entero y vale «{valor}».") from None


def leer_argumentos(argv=None, entorno=None) -> argparse.Namespace:
    """Lee la línea de comandos. Los valores por defecto salen de las variables RESERVAS_*."""
    entorno = os.environ if entorno is None else entorno
    parser = argparse.ArgumentParser(
        prog="run.py",
        description="Arranca el servidor de Reservas de aulas (Waitress).",
    )
    parser.add_argument(
        "--host",
        default=(entorno.get("RESERVAS_HOST") or HOST_POR_DEFECTO).strip(),
        help="Interfaz donde escucha (RESERVAS_HOST). 0.0.0.0 = toda la red. "
        f"Por defecto: {HOST_POR_DEFECTO}",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=_numero_de_entorno(entorno, "RESERVAS_PORT", PUERTO_POR_DEFECTO),
        help=f"Puerto (RESERVAS_PORT). Por defecto: {PUERTO_POR_DEFECTO}",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=_numero_de_entorno(entorno, "RESERVAS_THREADS", HILOS_POR_DEFECTO),
        help=f"Peticiones atendidas a la vez (RESERVAS_THREADS). Por defecto: {HILOS_POR_DEFECTO}",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Solo comprueba la instalación (configuración, base de datos, migraciones, "
        "estáticos) y sale, sin arrancar el servidor.",
    )
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("el puerto debe estar entre 1 y 65535")
    if not 1 <= args.threads <= 64:
        parser.error("los hilos deben estar entre 1 y 64")
    return args


# ------------------------------------------------------------------------ Django
def iniciar_django() -> None:
    import django
    from django.core.exceptions import ImproperlyConfigured

    try:
        django.setup()
    except ImproperlyConfigured as exc:
        raise ErrorDeArranque(f"Configuración incorrecta: {exc}") from exc
    except ImportError as exc:
        raise ErrorDeArranque(
            f"Falta instalar una dependencia ({exc}). Ejecuta: pip install -r requirements.txt"
        ) from exc


def comprobar_configuracion() -> None:
    """Ejecuta las comprobaciones internas de Django («manage.py check»)."""
    from io import StringIO

    from django.core.management import call_command
    from django.core.management.base import SystemCheckError

    try:
        # Django escribe «no issues» en stdout incluso con verbosity=0: lo descartamos.
        call_command("check", stdout=StringIO())
    except SystemCheckError as exc:
        raise ErrorDeArranque(f"Django ha detectado errores de configuración:\n{exc}") from exc


def comprobar_base_de_datos() -> str:
    """Comprueba que PostgreSQL responde y que las migraciones están aplicadas."""
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    from django.db.utils import OperationalError

    datos = connection.settings_dict
    destino = f"{datos['HOST']}:{datos['PORT']}, base «{datos['NAME']}», usuario «{datos['USER']}»"
    try:
        connection.ensure_connection()
    except OperationalError as exc:
        raise ErrorDeArranque(
            f"No se puede conectar con PostgreSQL ({destino}).\n"
            f"  Detalle: {str(exc).strip()}\n"
            "  Comprueba que el servicio de PostgreSQL está en marcha y los valores DB_* de .env."
        ) from exc

    ejecutor = MigrationExecutor(connection)
    pendientes = ejecutor.migration_plan(ejecutor.loader.graph.leaf_nodes())
    if pendientes:
        raise ErrorDeArranque(
            f"Faltan {len(pendientes)} migraciones de la base de datos por aplicar.\n"
            "  Ejecuta: python manage.py migrate"
        )
    return destino


def comprobar_estaticos(ajustes) -> None:
    """Sin «collectstatic» la web arranca pero se ve sin estilos ni scripts."""
    if ajustes.DEBUG:
        return  # en desarrollo WhiteNoise sirve directamente desde static/
    css = Path(ajustes.STATIC_ROOT) / "css" / "app.css"
    if not css.is_file():
        raise ErrorDeArranque(
            f"No se han recogido los ficheros estáticos (falta {css}).\n"
            "  Ejecuta: python manage.py collectstatic --noinput"
        )


def _estaticos_desactualizados(ajustes) -> bool:
    """True si algún fichero de static/ es más nuevo que su copia recogida."""
    origen = Path(ajustes.STATICFILES_DIRS[0])
    destino = Path(ajustes.STATIC_ROOT)
    for ruta in origen.rglob("*"):
        if not ruta.is_file():
            continue
        copia = destino / ruta.relative_to(origen)
        if not copia.is_file() or copia.stat().st_mtime + 2 < ruta.stat().st_mtime:
            return True
    return False


def recopilar_avisos(ajustes, host: str) -> list[str]:
    """Cosas que no impiden arrancar pero casi seguro hay que arreglar."""
    avisos = []
    if ajustes.DEBUG:
        avisos.append(
            "DJANGO_DEBUG está activado: ante un error se muestran detalles internos a quien "
            "navegue. Ponlo a False en producción."
        )
    escucha_en_red = host not in _HOSTS_LOCALES
    if escucha_en_red and set(ajustes.ALLOWED_HOSTS) <= _HOSTS_LOCALES:
        avisos.append(
            "DJANGO_ALLOWED_HOSTS solo admite localhost: el resto de equipos recibirá «400 Bad "
            "Request». Añade el nombre o la IP del servidor."
        )
    if not ajustes.DEBUG:
        if "localhost" in ajustes.SITE_URL or "127.0.0.1" in ajustes.SITE_URL:
            avisos.append(
                f"SITE_URL vale «{ajustes.SITE_URL}»: el enlace del correo de aviso no funcionará "
                "desde otros equipos."
            )
        if "console" in ajustes.EMAIL_BACKEND:
            avisos.append(
                "EMAIL_BACKEND es el de consola: los avisos a Mantenimiento se escriben en el log "
                "y NO se envían por correo."
            )
    if not ajustes.RESERVAS_EMAIL_DESTINO:
        avisos.append("RESERVAS_EMAIL_DESTINO está vacío: no se avisará a nadie de las peticiones.")
    try:
        import ssl  # noqa: F401
    except ImportError as exc:
        avisos.append(
            f"No se puede cargar el módulo ssl ({exc}): el correo con TLS fallará. En Anaconda "
            "suele deberse a que faltan las carpetas Library\\bin del entorno en el PATH."
        )
    if not ajustes.DEBUG and Path(ajustes.STATIC_ROOT).is_dir() and _estaticos_desactualizados(ajustes):
        avisos.append(
            "Los ficheros de static/ son más nuevos que los recogidos: ejecuta "
            "«python manage.py collectstatic --noinput» para que se vean los cambios."
        )
    return avisos


def contar_usuarios() -> int:
    from django.contrib.auth import get_user_model

    return get_user_model().objects.filter(is_active=True).count()


def ejecutar_comprobaciones(args) -> tuple[list[str], list[str]]:
    """Devuelve (líneas de resumen, avisos). Lanza ErrorDeArranque si algo impide arrancar."""
    from django.conf import settings

    comprobar_configuracion()
    destino = comprobar_base_de_datos()
    comprobar_estaticos(settings)

    avisos = recopilar_avisos(settings, args.host)
    usuarios = contar_usuarios()
    if usuarios == 0:
        avisos.append("No hay ningún usuario. Ejecuta: python manage.py crear_usuarios")

    resumen = [
        f"Python {sys.version.split()[0]} en {sys.prefix}",
        "Configuración correcta",
        f"PostgreSQL conectado ({destino}), migraciones al día",
        "Ficheros estáticos " + ("(modo desarrollo)" if settings.DEBUG else "recogidos"),
        f"Usuarios activos: {usuarios}",
    ]
    return resumen, avisos


# ------------------------------------------------------------------------ Servidor
def _apagar(numero, marco):
    # Waitress interpreta KeyboardInterrupt como «parar»: termina las peticiones en curso
    # y cierra el puerto. NSSM envía Ctrl+C al detener el servicio.
    raise KeyboardInterrupt


def instalar_senales() -> None:
    # SIGINT se instala a propósito: si quien nos lanza lo había dejado «ignorado» (pasa con
    # los procesos en segundo plano de una shell no interactiva), Python no lo trataría solo.
    for nombre in ("SIGINT", "SIGTERM", "SIGBREAK"):  # SIGBREAK solo existe en Windows
        senal = getattr(signal, nombre, None)
        if senal is not None:
            signal.signal(senal, _apagar)


def servir(host: str, port: int, threads: int) -> None:
    from waitress import serve

    from config.wsgi import application

    instalar_senales()
    # waitress.serve() llama a logging.basicConfig() si el logger raíz no tiene manejadores,
    # y eso duplicaría cada línea de Waitress. Un NullHandler en la raíz lo evita sin tocar
    # nada más: nuestros loggers ya tienen su propio manejador (ver LOGGING en settings.py).
    logging.getLogger().addHandler(logging.NullHandler())
    donde = "todas las interfaces de red" if host == "0.0.0.0" else host
    logger.info("Iniciando Reservas de aulas en %s, puerto %s (hilos: %s).", donde, port, threads)
    logger.info("Para detenerlo: Ctrl+C (o «nssm stop» si es un servicio).")
    serve(
        application,
        host=host,
        port=port,
        threads=threads,
        ident="reservas-aulas",
        max_request_body_size=TAMANO_MAXIMO_PETICION,
        clear_untrusted_proxy_headers=True,  # no se confía en cabeceras X-Forwarded-* de nadie
    )
    logger.info("Servidor detenido.")


def main(argv=None) -> int:
    preparar_consola()
    preparar_dll_de_conda()
    try:
        cargar_entorno()
        args = leer_argumentos(argv)
        preparar_proyecto()
        iniciar_django()
        resumen, avisos = ejecutar_comprobaciones(args)
    except ErrorDeArranque as exc:
        print(f"\nNO SE PUEDE ARRANCAR\n{exc}\n", file=sys.stderr)
        return 1

    if args.check:
        print("Reservas de aulas: comprobación previa")
        for linea in resumen:
            print(f"  OK  {linea}")
        for aviso in avisos:
            print(f"  AVISO  {aviso}")
        print("\nTodo listo." if not avisos else "\nPuede arrancar, pero revisa los avisos.")
        return 0

    for aviso in avisos:
        logger.warning(aviso)
    try:
        servir(args.host, args.port, args.threads)
    except OSError as exc:  # puerto ocupado, sin permisos, dirección inválida…
        print(
            f"\nNO SE PUEDE ARRANCAR\nNo se puede escuchar en {args.host}:{args.port}: {exc}\n"
            "  ¿Hay otro programa (o una copia del servicio) usando ese puerto?\n",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())