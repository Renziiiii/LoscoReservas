# Deploy en LoscoServer (producción)

LoscoReservas corre como **un contenedor propio al lado del stack de Chatwoot**,
sin tocarlo: chatwoot-rails/sidekiq/redis/postgres viven en su compose
(`/root/chatwoot`) y esta app en el suyo (`/root/losco-reservas`). Comparten
la máquina, el nginx y nada más.

```
Chatwoot (Dashboard App iframe)          agentes en el browser
        │
Cloudflare (DNS proxied) → nginx :443 (Let's Encrypt)
        │                        └─ chatwoot.loscobar.com.ar → 127.0.0.1:3000
        └─ reservas.loscobar.com.ar → 127.0.0.1:8010 → losco-reservas (uvicorn :8000)
                                                                  │ SQLite: /data/reservas.db
La app llama a la API de Chatwoot por su URL pública: https://chatwoot.loscobar.com.ar
```

## Qué hay en el server

| Qué | Dónde |
| --- | --- |
| Código + compose | `/root/losco-reservas` (clone de GitHub) |
| Env de producción | `/root/losco-reservas/.env` (chmod 600, **no** está en el repo) |
| SQLite | volume `losco-reservas_reservas-data` → `/data/reservas.db` en el contenedor |
| vhost nginx | `/etc/nginx/sites-available/reservas` (SSL gestionado por certbot) |
| Backups | `/root/backups/reservas/` (cron diario 04:30, guarda 30) |
| URL pública | `https://reservas.loscobar.com.ar` (Cloudflare proxied) |

## Operaciones

### Actualizar la app

```bash
cd /root/losco-reservas
git pull
docker compose up -d --build   # rebuild + recreate, la base sobrevive (volume)
```

### Backups (automáticos) y restore

El cron de root corre `deploy/backup.py` (backup *online* vía la API `backup()`
de sqlite3, seguro con la app escribiendo; verifica integridad y poda):

```bash
# manual, mismo comando que el cron:
python3 /root/losco-reservas/deploy/backup.py \
  --db /var/lib/docker/volumes/losco-reservas_reservas-data/_data/reservas.db \
  --out /root/backups/reservas --keep 30

# restore de un backup:
cd /root/losco-reservas
docker compose stop reservas
cp /root/backups/reservas/reservas-YYYYMMDD-HHMMSS.db \
   /var/lib/docker/volumes/losco-reservas_reservas-data/_data/reservas.db
docker compose start reservas
```

### Agregar el token de Chatwoot (cuando llegue)

Sin token, etiquetas y mensajería degradan con fail-soft (todo lo demás anda).
Con token + account id del Chatwoot de producción:

```bash
nano /root/losco-reservas/.env
#   LOSCO_CHATWOOT_BASE_URL=https://chatwoot.loscobar.com.ar   (ya está)
#   LOSCO_CHATWOOT_API_TOKEN=<token de agente/admin>
#   LOSCO_CHATWOOT_ACCOUNT_ID=<id>
docker compose up -d   # recrea el contenedor con el env nuevo
```

Después: elegir la etiqueta de "reserva confirmada" y las plantillas desde
**Ajustes** del dashboard (listan las de la cuenta vía API).

### Prender el scheduler (mensajería automática)

Arrancó **apagado** a propósito: prenderlo solo después de validar un envío
real de plantilla (verificar el shape de `template_params` contra el Chatwoot
de producción). Para prenderlo:

```bash
# en .env: LOSCO_SCHEDULER_ENABLED=true
docker compose up -d
```

Horarios y plantillas se eligen en **Dashboard → Ajustes** (re-programa los
jobs en vivo, sin reiniciar).

### Iframe en el Chatwoot de producción

Chatwoot → Settings → Integrations → **Dashboard apps** → nueva app:

- URL: `https://reservas.loscobar.com.ar/iframe/app?key=<LOSCO_IFRAME_KEY>` (está en el `.env` del server)

Detalle del mecanismo (evento `appContext`, clave de embed, CSP):
`docs/iframe-chatwoot.md`.

## Puesta en marcha original (referencia para reproducirla)

1. `git clone https://github.com/Renziiiii/LoscoReservas.git /root/losco-reservas`
2. Crear `.env` desde `env.example` (secrets con `openssl rand`; `chmod 600`).
3. `cd /root/losco-reservas && docker compose up -d --build`
4. vhost nginx en `/etc/nginx/sites-available/reservas` (`listen 80`, proxy a
   `127.0.0.1:8010`), symlink a `sites-enabled`, `nginx -t && systemctl reload nginx`.
5. DNS: registro A `reservas` → IP del server, **proxied** en Cloudflare.
6. `certbot --nginx -d reservas.loscobar.com.ar` (agrega el 443 + redirect).
7. Cron de backup (ver arriba) + `mkdir -p /root/backups/reservas`.
