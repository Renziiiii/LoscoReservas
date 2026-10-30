# LoscoReservas

API de gestión de reservas para el bar Losco. Reemplaza el Google Sheets
manual por una API autohospedada con dashboard admin, mensajería
automatizada multi-canal vía Chatwoot (WhatsApp, Instagram, Messenger) e
iframe de reservas por contacto dentro de Chatwoot.

## Qué incluye

- **API REST de reservas** (`/api/reservas`) con validación y filtros —
  pensada para que una IA o integración futura registre reservas por chat
  (documentación OpenAPI interactiva en `/docs`).
- **Dashboard admin** server-rendered (Jinja2 + HTMX): login, lista con
  filtros, alta/edición/baja.
- **Mensajería automatizada** (regla de Valen): T-48h confirmación de la
  reserva y el mismo día entre 17:00 y 18:00 confirmación de invitados
  (horas y plantillas se configuran en el panel, `/ajustes`, sin reiniciar
  el contenedor). Consciente del canal: fuera de la ventana de 24hs en
  WhatsApp usa plantillas UTILITY aprobadas (se eligen desde el panel,
  listadas directo de la API de Chatwoot); dentro de la ventana usa texto
  libre; Instagram/Messenger fuera de ventana no permite mensajes iniciados
  por el negocio (se registra la falla). Cada reserva puede excluirse de
  los recordatorios (checkbox "Enviar recordatorios", default sí).
- **Etiqueta "Reserva confirmada" en Chatwoot**: al confirmar una reserva
  (dashboard, iframe o API) el contacto recibe la etiqueta configurada en
  Ajustes (selector con las etiquetas de la cuenta de Chatwoot) con
  migración opcional al cambiarla (fail-soft: sin token configurado el guardado
  no falla).
- **Iframe en Chatwoot** con las reservas del contacto de la conversación
  (mecanismo Dashboard Apps) — ver `docs/iframe-chatwoot.md`.

## Stack

- **Python 3.12+ / FastAPI** — API REST con OpenAPI automático.
- **SQLite (WAL)** — decenas de reservas por día no justifican un server
  de base de datos.
- **Docker** — un solo contenedor liviano, un worker (SQLite serializa
  escrituras igual).
- **APScheduler** — scheduler in-process, sin servicio extra.

## Desarrollo local

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest        # suite completa
uvicorn app.main:app --reload     # http://localhost:8000/docs
```

## Probar en un servidor (Docker)

> ¿Usás **Dokploy**? Tenés la guía específica paso a paso en
> `docs/dokploy.md` (GitHub privado, variables, volumen del SQLite y
> dominio con HTTPS automático).

1. Copiá el repo al server (git clone, rsync o scp).
2. Creá tu `.env` a partir de `env.example` y completá al menos:

   ```bash
   LOSCO_USERS=valen:tucontraseña
   LOSCO_JWT_SECRET=$(openssl rand -hex 32)
   LOSCO_IFRAME_KEY=$(openssl rand -hex 24)   # solo si probás el iframe
   ```

3. Levantá el contenedor:

   ```bash
   docker compose up -d --build
   curl http://localhost:8010/api/health        # {"status":"ok"}
   ```

4. Entrá al dashboard: `http://<server>:8010/login` (usuario de
   `LOSCO_USERS`).

> Nota: el iframe embebido en Chatwoot requiere que la app sea alcanzable
> por HTTPS desde el navegador del agente (Chatwoot Dashboard Apps carga
> la URL tal cual la configuraste). Para probarlo completo necesitás un
> reverse proxy con TLS (nginx + certbot) apuntando al puerto 8010.

## Autenticación

- **Dashboard**: login con usuario/contraseña (cookie).
- **API**: OAuth2 password flow → `POST /api/auth/login` devuelve un JWT;
  usalo como `Authorization: Bearer <token>`. Probalo en `/docs` con el
  botón "Authorize".

```bash
TOKEN=$(curl -s -X POST http://localhost:8010/api/auth/login \
  -d "username=valen&password=tucontraseña" | python3 -c \
  "import sys,json;print(json.load(sys.stdin)['access_token'])")
curl -H "Authorization: Bearer $TOKEN" http://localhost:8010/api/reservas
```

## Configuración (prefijo `LOSCO_`)

Ver `env.example` con todos los valores y comentarios. Resumen:

| Variable | Default | Descripción |
| --- | --- | --- |
| `LOSCO_DATABASE_PATH` | `data/reservas.db` | Ruta del SQLite |
| `LOSCO_TZ` | `America/Argentina/Buenos_Aires` | Zona horaria (jobs de mensajería) |
| `LOSCO_USERS` | `valen:losco` | Pares `usuario:contraseña` separados por coma |
| `LOSCO_JWT_SECRET` | dev | Secreto JWT (`openssl rand -hex 32`) |
| `LOSCO_JWT_EXPIRE_MINUTES` | `480` | Expiración del token |
| `LOSCO_CHATWOOT_BASE_URL` | vacío | URL de la API de Chatwoot |
| `LOSCO_CHATWOOT_API_TOKEN` | vacío | Token de agente/admin de Chatwoot |
| `LOSCO_CHATWOOT_ACCOUNT_ID` | `0` | Account id de Chatwoot |
| `LOSCO_TEMPLATE_CONFIRMACION` | `reserva_confirmacion` | Valor inicial de la plantilla T-48h — después se edita en el panel (`/ajustes`) |
| `LOSCO_TEMPLATE_INVITADOS` | `invitados_confirmacion` | Valor inicial de la plantilla día D — después se edita en el panel (`/ajustes`) |
| `LOSCO_HORA_CONFIRMACION` | `09:00` | Valor inicial de la hora T-48h — después se edita en el panel (`/ajustes`) |
| `LOSCO_HORA_INVITADOS` | `17:15` | Valor inicial de la hora día D — después se edita en el panel (`/ajustes`) |
| `LOSCO_SCHEDULER_ENABLED` | `false` | Prende el scheduler (mensajería) |
| `LOSCO_IFRAME_KEY` | vacío | Clave de embed del iframe (vacía = iframe deshabilitado) |
| `LOSCO_IFRAME_ORIGINS` | `*` | Orígenes con permiso de embeber (CSP frame-ancestors) |

## API para consumo futuro (IA / integraciones)

La API está diseñada API-first: `GET /docs` sirve la OpenAPI completa y
`GET /openapi.json` el schema máquina, listo para que un agente de chat
registre reservas sin tocar el dashboard. Endpoints principales:

- `POST /api/reservas` — crear (valida nombre, teléfono ≥8 dígitos, fecha,
  hora, 1-200 personas).
- `GET /api/reservas?estado=&fecha=&telefono=` — listar con filtros.
- `GET/PATCH/DELETE /api/reservas/{id}` — leer, actualizar parcial,
  borrar.
- `GET /api/health` — liveness (público).

## Estructura

```
app/            config, db, modelos, schemas, API, dashboard, iframe,
                cliente Chatwoot, mensajería, scheduler
docs/           guía del iframe en Chatwoot
odd/tasks/      documento del feature: specs, tareas y log de decisiones
```

Documentación completa del proyecto y decisiones: `odd/tasks/losco-reservas.md`.
