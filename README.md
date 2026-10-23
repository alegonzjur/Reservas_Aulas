# Reservas de aulas

Aplicación web para gestionar las peticiones de aulas del centro. El profesorado **pide**
un aula con un formulario; Mantenimiento (o Informática) **acepta o rechaza** desde un
calendario semanal. Pensada para uso en red local.

Stack: Django 5.2 · PostgreSQL 16 · HTMX 2 · Tom Select (selector con búsqueda).
Sin React por ahora: las vistas devuelven HTML y HTMX sustituye fragmentos, así que
añadir React más adelante sería posible sin tirar el modelo ni la lógica de negocio.

## Roles

| Usuario         | Rol            | Qué puede hacer                                                    |
|-----------------|----------------|--------------------------------------------------------------------|
| `mantenimiento` | Administrador  | Calendario, aceptar/rechazar, reservar directamente, exportar      |
| `informatica`   | Administrador  | Lo mismo, y además es superusuario de Django (`/admin/`)           |
| `profesor`      | Profesorado    | Solo el formulario de petición (compartido por todo el claustro)   |

Si no hay red, el profesor llama a Mantenimiento, que crea la reserva con la casilla
«Aceptar directamente» (solo visible para administradores).

## Puesta en marcha

Requisitos: Python 3.11+, PostgreSQL 13+.

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env        # edita: clave secreta, base de datos, correo
```

Crea la base de datos (una sola vez):

```sql
CREATE USER reservas WITH PASSWORD 'cambia-esto';
CREATE DATABASE reservas_aulas OWNER reservas;
```

Después:

```bash
python manage.py migrate
python manage.py crear_usuarios        # pide las tres contraseñas por teclado
python manage.py runserver 0.0.0.0:8000
```

Entra en `http://<ip-del-servidor>:8000/`. Cada usuario va a su pantalla según su rol.

Las **aulas** se dan de alta en `/admin/` (entra como `informatica`). Para quitar un aula
del formulario sin perder su historial, desmarca «activa».

`crear_usuarios --restablecer` cambia contraseñas; con `--no-input` las lee de las variables
`RESERVAS_PASSWORD_MANTENIMIENTO`, `RESERVAS_PASSWORD_INFORMATICA` y `RESERVAS_PASSWORD_PROFESOR`.

### Nota sobre PostgreSQL

La migración `0001` activa la extensión `btree_gist` (necesaria para la restricción
anti-solapes). En PostgreSQL 13+ puede crearla un usuario normal; en versiones anteriores
hay que ejecutar antes, como superusuario: `CREATE EXTENSION btree_gist;` en esa base.

## Correo a Mantenimiento

Cada petición pendiente envía un correo a `RESERVAS_EMAIL_DESTINO` desde la cuenta genérica
configurada en `.env` (`EMAIL_HOST`, `EMAIL_HOST_USER`, …). El envío ocurre **después** de
guardar la petición y nunca la rompe: si el SMTP falla, la petición queda registrada y el
error va al log. Por defecto (`EMAIL_BACKEND` de consola) los correos se imprimen en la
terminal, útil para probar. Los profesores no reciben ninguna notificación.

## Producción en red local

```bash
# .env: DJANGO_DEBUG=False, DJANGO_SECRET_KEY=<larga y aleatoria>, DJANGO_ALLOWED_HOSTS=<ip o nombre>
pip install waitress
python manage.py collectstatic --noinput
waitress-serve --listen=0.0.0.0:8000 config.wsgi:application
```

WhiteNoise sirve los estáticos; no hace falta nginx para el uso previsto. Con
`DJANGO_DEBUG=False` la aplicación se niega a arrancar sin `DJANGO_SECRET_KEY`.
Copias de seguridad: `pg_dump reservas_aulas > copia.sql` (programa una tarea periódica).

## Decisiones de diseño

- **Los solapes los impide la base de datos, no solo el código.** Una `ExclusionConstraint`
  de PostgreSQL rechaza dos reservas *aceptadas* del mismo aula con franjas que se pisan
  (`[inicio, fin)`: 09:00–10:00 y 10:00–11:00 son compatibles). Dos peticiones concurrentes
  no pueden colarse aunque pasen la validación a la vez. Las pendientes sí pueden coincidir.
- **Bloquear y avisar.** El formulario avisa en vivo (HTMX) de que la franja está ocupada y
  el servidor la rechaza igualmente. Al administrador, el detalle le explica qué reserva
  ocupa la franja y deshabilita «Aceptar» hasta resolverlo.
- **Auditoría.** `EventoReserva` es un registro solo de añadir (creada, aceptada, rechazada,
  reabierta, editada) con usuario y fecha/hora. La reserva guarda además la última decisión
  (`gestionada_por`, `gestionada_en`) para listarla rápido. Ambos salen en la exportación.
- **Capa de servicios.** `crear_peticion` y `cambiar_estado` (en `services.py`) concentran
  las reglas dentro de una transacción con bloqueo de fila; vistas y admin las reutilizan.
- **Horario.** `HORA_APERTURA` (08:00), `HORA_CIERRE` (21:00) y `PASO_MINUTOS` (5) están en
  `config/settings.py`. Solo lunes a viernes.
- **Exportación.** Excel con tres hojas (reservas, aulas, auditoría) y CSV por separado
  (UTF-8 con BOM, fechas ISO, coma o punto y coma). Las celdas que empiezan por `=`, `+`,
  `-` o `@` se neutralizan para evitar inyección de fórmulas al abrirlos en Excel.
- **Mejora progresiva.** Los formularios funcionan sin JavaScript (HTMX añade la comodidad).

## Estructura

```
config/            ajustes, urls, wsgi
reservas/
  models.py        Aula, Reserva (+ restricciones), EventoReserva
  services.py      crear_peticion, cambiar_estado, auditoría
  calendario.py    geometría de la semana (carriles de solapes) y lista de pendientes
  forms.py         formularios y filtros
  exports.py       CSV y Excel
  emails.py        aviso a Mantenimiento
  permissions.py   roles y decoradores
  views.py, urls.py, admin.py, tests.py
  management/commands/crear_usuarios.py
templates/         plantillas (fragmentos HTMX con prefijo _)
static/            css, js y librerías incluidas (sin CDN: funciona sin internet)
```

## Pruebas

```bash
python manage.py test
```

112 pruebas: restricción en base de datos, servicios, formularios, calendario, permisos,
vistas y fragmentos HTMX, exportaciones, comando de usuarios y admin. Necesitan un
PostgreSQL accesible (el usuario debe poder crear bases de datos de prueba).

## Limitaciones conocidas

- **El envío real por SMTP no se ha probado** (no había servidor de correo disponible);
  se probó con el backend de consola y con fallos simulados.
- Los campos de fecha y hora los dibuja el navegador, así que su formato (mm/dd/aaaa,
  AM/PM…) depende del idioma del navegador, no de la aplicación. Lo enviado y guardado es
  siempre correcto.
- El calendario ensancha el día cuando hay muchas reservas simultáneas (100 px por carril);
  con muchas a la vez aparece desplazamiento horizontal.
- Las reservas son puntuales; las franjas fijas semanales quedan para una fase posterior.
- Las pendientes cuya fecha ya pasó siguen listadas (marcadas «Fecha pasada») hasta que
  alguien las gestione.
- Una sola cuenta compartida de profesorado: la auditoría de peticiones registra
  «Profesor», y el nombre real es el que se escribe en el campo «Profesor/a».

## Licencias de lo incluido en `static/vendor/`

HTMX 2.0.11 (0BSD) · Tom Select 2.6.2 (Apache-2.0).