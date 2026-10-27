# LoscoReservas

API de gestión de reservas para el bar Losco. Reemplaza el Google Sheets manual
por una API autohospedada con dashboard y mensajería WhatsApp automatizada vía
Chatwoot.

## Stack

- **Python 3.12+ / FastAPI** — API REST con documentación OpenAPI automática.
- **SQLite (WAL)** — un bar tiene decenas de reservas por día; no hace falta
  un servidor de base de datos (y el server de deploy tiene 1 núcleo / 1.9 GB
  con Chatwoot ya corriendo).
- **Docker** — un solo contenedor liviano, desplegado al lado de Chatwoot en
  LoscoServer.

## Desarrollo local

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
uvicorn app.main:app --reload   # http://localhost:8000/docs
```

Configuración por variables de entorno (prefijo `LOSCO_`):

| Variable | Default | Descripción |
| --- | --- | --- |
| `LOSCO_DATABASE_PATH` | `data/reservas.db` | Ruta del archivo SQLite |
| `LOSCO_TZ` | `America/Argentina/Buenos_Aires` | Zona horaria del negocio (horarios de recordatorios) |

## Deploy (LoscoServer)

```bash
docker compose up -d --build
```

Documentación completa del proyecto y decisiones: `odd/tasks/losco-reservas.md`.
