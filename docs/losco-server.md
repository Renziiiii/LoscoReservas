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

### Etiquetar chats de Chatwoot por filtro (campañas)

`deploy/chatwoot_etiquetar_chats.rb` etiqueta o des-etiqueta conversaciones de
Chatwoot **por filtro** (canal, inactividad, estados), a nivel aplicación
(`rails runner` dentro del container de Chatwoot). Es la herramienta con la
que se armaron las tandas de la campaña de WhatsApp: la 1ra (2026-11-04)
etiqueto `campania-wsp` a los 576 chats inactivos >3 semanas y despues se
redujo a los 300 mas antiguos; la 2da (2026-11-11) etiqueto `campania-wsp-2`
a los 276 restantes de ese grupo original (ver «Registro de tandas» abajo).

Copia en el server: `/root/chatwoot/scripts/chatwoot_etiquetar_chats.rb` y
`/root/chatwoot/scripts/chatwoot_etiquetar_contactos.rb` (actualizar por scp
desde `deploy/` si cambian).

```bash
# 0) backup de la base de Chatwoot ANTES de escribir (regla de oro):
cd /root/chatwoot && docker exec chatwoot-postgres-1 pg_dump -U postgres -Fc \
  -d chatwoot_production > backups/chatwoot_production_$(date +%Y%m%d_%H%M%S).dump

# 1) subir el script al container:
docker cp /root/chatwoot/scripts/chatwoot_etiquetar_chats.rb chatwoot-rails-1:/tmp/

# 2) SIEMPRE dry-run primero (default: dry_run=true, no escribe nada):
docker exec chatwoot-rails-1 bundle exec rails runner /tmp/chatwoot_etiquetar_chats.rb \
  action=tag label=campania-wsp channel=Channel::Whatsapp inactive_days=21

# 3) aplicar (mismo comando + dry_run=false):
docker exec chatwoot-rails-1 bundle exec rails runner /tmp/chatwoot_etiquetar_chats.rb \
  action=tag label=campania-wsp channel=Channel::Whatsapp inactive_days=21 dry_run=false
```

Parámetros: `label` (obligatorio), `action` = `tag` (agrega, merge) |
`untag` (quita) | `keep_oldest` + `limit=N` (deja la label solo en los N
chats con actividad más antigua), `channel` (default WhatsApp),
`inactive_days`, `statuses=open,resolved,...`, `exclude_label=a,b` (varias
separadas por coma: descarta los chats cuyas labels —o las de su
contacto— incluyan alguna; aborta si una label no existe),
`account_id` (default: la primera cuenta).

Propiedades verificadas: a nivel app (`add_labels`/`update_labels`), taggings
y `cached_label_list` quedan consistentes, no genera mensajes de actividad en
los timelines ni toca `last_activity_at`, no pisa labels existentes, es
idempotente (re-ejecutar `tag` no duplica). Salida greppable: `PARAMS`,
`EXCLUDED`, `MATCHED`, `CHANGED`, `FAILED`, `WOULD_KEEP`/`WOULD_UNTAG` en
dry-run de `keep_oldest`.

**Audiencia de campaña: labels de CONTACTO.** Las campañas one-off de
WhatsApp de Chatwoot resuelven la audiencia sobre labels de *contactos*
(`campaign.account.contacts.tagged_with` en `Whatsapp::OneoffCampaignService`),
no de conversaciones. Después de taggear conversaciones, propagar la label a
los contactos con `deploy/chatwoot_etiquetar_contactos.rb` (mismo protocolo:
backup, dry-run, `dry_run=false`):

```bash
docker cp /root/chatwoot/scripts/chatwoot_etiquetar_contactos.rb chatwoot-rails-1:/tmp/
docker exec chatwoot-rails-1 bundle exec rails runner /tmp/chatwoot_etiquetar_contactos.rb \
  label=campania-wsp-2                # dry-run: imprime WOULD_TAG
docker exec chatwoot-rails-1 bundle exec rails runner /tmp/chatwoot_etiquetar_contactos.rb \
  label=campania-wsp-2 dry_run=false
```

**Registro de tandas:**

- **1ra — 2026-11-04 — `campania-wsp`** (id=3, `#CA7AE3`): 576 chats
  inactivos >3 semanas; `keep_oldest limit=300` dejo la label solo en los 300
  mas antiguos (la tanda blasteada); 300 contactos propagados.
- **2da — 2026-11-11 — `campania-wsp-2`** (id=6, `#F1663E`): los 276 del 2do
  grupo original, reconstruidos con el corte congelado de la 1ra tanda (el
  filtro del 04-11 era «inactivos >21 días», que al 11-11 equivale a
  `inactive_days=28`) con `exclude_label=campania-wsp,no-publicidad` para no
  repetir la 1ra ni tocar opt-outs. Resultado: 276 conversaciones
  (`CHANGED=276 FAILED=0`) y 276 contactos (`TAGGED=276 FAILED=0`, todos con
  teléfono). Ninguno del grupo llevaba `no-publicidad` (los 2 de la cuenta
  estan dentro de la 1ra tanda). Sin `keep_oldest`: los 276 son la tanda
  completa. Backup previo: `chatwoot_production_20261111_171034.dump`
  (hay que tomarlo ANTES de escribir, como siempre).

**Integración futura en el dashboard**: no hace falta el runner — la app ya
habla con la API de Chatwoot (`app/chatwoot.py`, token en `.env`). Faltan dos
piezas sobre `ChatwootClient`: listar conversaciones por inbox con la
inactividad como filtro (`GET /conversations?status=all&inbox_id=&page=` y
filtrar por `last_activity_at` cliente-side) y aplicar el mismo
merge-antes-de-POST de `app/etiquetas.py` (`agregar_labels_conversacion`
**pisa** la lista completa). El runner queda para ops manual sin depender del
token.

**Decisión (2026-11-11): automatizar las tandas de campaña en el dashboard.**
Más adelante este flujo —taggear por filtro con exclusiones, propagado a
contactos y verificación tipo dry-run— se lleva a una interfaz para que Losco
arme sus tandas de campaña sin ops manual. El diseño queda pendiente ("el
cómo"); hoy el runner es la herramienta operativa.

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

## Seguridad SSH (hallazgo 2026-11-07)

**Estado verificado:** `sshd -T` da `permitrootlogin yes` + `passwordauthentication yes` en el
puerto 22022, y la password de root está activa (`passwd -S root` → `P 09/02/2026`, seteada el
día del provisioning). La causa: `/etc/ssh/sshd_config.d/50-cloud-init.conf` (herencia del
provisioning) fuerza `PasswordAuthentication yes`, y `PermitRootLogin yes` está en
`/etc/ssh/sshd_config:33`. **Fix aprobado y pendiente** (decisión 2026-11-07: aplicarlo en otra
ventana):

```bash
cat > /etc/ssh/sshd_config.d/00-losco-hardening.conf <<'EOF'
# 2026-11-07: SSH solo por clave. Password de root queda SOLO para consola VNC del panel.
PermitRootLogin prohibit-password
PasswordAuthentication no
KbdInteractiveAuthentication no
EOF
sshd -t && systemctl reload ssh
sshd -T | grep -Ei '^(permitrootlogin|passwordauthentication|kbdinteractive)'

# verificación (desde la máquina con la clave):
ssh -o BatchMode=yes ValenLoscoBarServer 'echo clave OK'    # debe entrar
ssh -o PubkeyAuthentication=no -o PreferredAuthentications=password \
    -o BatchMode=yes -p 22022 root@143.95.165.168           # debe dar: Permission denied (publickey)
```

Sin riesgo de lockout: el acceso propio es por clave `loscovale` (verificado), el host accede por
sus claves/CA (`sshca_2020_2`, el fix no las toca), y la password de root **no** se bloquea —
queda como rescate por la consola VNC del panel si el server no bootea.

**Forense 2026-11-07: server limpio, no comprometido.** Historial completo (journald persistente
desde 2026-09-02 + wtmp): todos los logins aceptados fueron por clave pública (`root`, desde
nuestras IPs con la clave `loscovale` o desde infra del host); cero logins por contraseña, cero
claves ajenas. Fuerza bruta: ~15.300 intentos fallidos entre 2026-09-02 y 2026-10-19 (pico
11–23 de sep; top `37.111.53.110` con 6.222; `77.239.124.105` probó los usuarios `fake` y
`trompeta` el 24-sep). Ninguno exitoso; sin persistencia (authorized_keys, usuarios, cron,
systemd, containers y `/root` verificados). Puertos públicos: solo 80/443 (nginx) y 22022 (sshd).

**Ojo con journald:** los logs de la fuerza bruta viven en `journalctl -u ssh` (journald es
persistente desde el nacimiento del server); `auth.log` solo cubre ~4 semanas. Sin `--since`,
journald muestra el histórico completo y un ataque de hace meses parece "en curso" — así se
detectó este hallazgo.

**fail2ban:** corre pero su jail `sshd` está ciega: el journalmatch busca la unidad `sshd.service`
y la real es `ssh.service` → 0 baneos en los 15.000 intentos. Decisión 2026-11-07: dejarlo (tras
el fix de password es cosmético). Para arreglarlo si algún día importa: jail.d con
`journalmatch = _SYSTEMD_UNIT=ssh.service + _COMM=sshd` o backend que lea `/var/log/auth.log`.

## Puesta en marcha original (referencia para reproducirla)

1. `git clone https://github.com/Renziiiii/LoscoReservas.git /root/losco-reservas`
2. Crear `.env` desde `env.example` (secrets con `openssl rand`; `chmod 600`).
3. `cd /root/losco-reservas && docker compose up -d --build`
4. vhost nginx en `/etc/nginx/sites-available/reservas` (`listen 80`, proxy a
   `127.0.0.1:8010`), symlink a `sites-enabled`, `nginx -t && systemctl reload nginx`.
5. DNS: registro A `reservas` → IP del server, **proxied** en Cloudflare.
6. `certbot --nginx -d reservas.loscobar.com.ar` (agrega el 443 + redirect).
7. Cron de backup (ver arriba) + `mkdir -p /root/backups/reservas`.
