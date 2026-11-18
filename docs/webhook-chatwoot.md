# Webhook de Chatwoot (sincronización de tags a chats nuevos)

Un chat nuevo de Chatwoot —creado por la mensajería automatizada de
LoscoReservas, por un agente, o por cualquier integración— tiene que heredar
las etiquetas de su contacto. Este webhook cubre los chats que no pasa por la
app (la mensajería automatizada ya sincroniza por sí sola; ver README,
sección Tags).

## Qué hace

| Evento de Chatwoot | Qué hace LoscoReservas |
| --- | --- |
| `conversation_created` | Copia al chat nuevo TODAS las etiquetas actuales de su contacto. |
| `contact_updated` | Un agente sumó/cambió etiquetas del contacto: las propaga a todos sus chats existentes (solo agrega, nunca quita). |

Cualquier otro evento se ignora sin tocar la API de Chatwoot. El endpoint
nunca responde 5xx con token válido: un fallo interno se loguea y no rompe la
entrega del webhook (fail-soft, L6).

Las etiquetas se sincronizan también cuando una reserva cambia de estado:
la etiqueta del estado anterior se retira del contacto y de todos sus chats,
y se aplica la del estado nuevo (configurable en el panel → Ajustes → Tags).

## 1. Token compartido

Generá un secreto y agregalo al `.env` del server:

```bash
openssl rand -hex 24   # → p. ej. 9f2c…64
```

```
LOSCO_CHATWOOT_WEBHOOK_TOKEN=<el valor generado>
```

y `docker compose up -d --build` para que tome el env.

Sin la variable configurada el endpoint responde 404 a todo (fail closed:
no existe webhook si no hay token).

## 2. Suscribir el webhook en Chatwoot

Chatwoot → **Settings → Integrations → Webhooks → + Add webhook**:

- **URL**:
  `https://reservas.loscobar.com.ar/api/webhooks/chatwoot?token=<TOKEN>`
- **Suscripciones**: marcá **solo** `conversation_created` y
  `contact_updated`. Los demás eventos se ignoran, pero cada llamada
  consume un request sin razón.

Alterna: hay tokens por header con la misma validez — Chatwoot permite
headers custom en el webhook: `X-Chatwoot-Token: <TOKEN>` con la URL
sin query string. Elegí UNA de las dos formas (query es la documentada acá).

## 3. Verificación

```bash
# desde el server (sin token → 404, y el endpoint no confirma su existencia)
curl -s -o /dev/null -w '%{http_code}\n' \
  -X POST https://reservas.loscobar.com.ar/api/webhooks/chatwoot \
  -H 'Content-Type: application/json' -d '{"event":"conversation_created"}'

# con token → 200 {"received": true}
curl -s -X POST \
  'https://reservas.loscobar.com.ar/api/webhooks/chatwoot?token=<TOKEN>' \
  -H 'Content-Type: application/json' \
  -d '{"event":"conversation_created","conversation":{"id":123,"meta":{"sender":{"type":"contact","id":45}}}}'
```

En los logs del contenedor (`docker logs losco-reservas`) debe aparecer el
trace de `losco.webhooks` si algo falló; con éxito no hay ruido.

Nota de producción: la URL pública pasa por Cloudflare + nginx (topología en
docs/losco-server.md); desde la propia máquina también funciona la URL
interna, pero el webhook de Chatwoot conviene apuntarlo al HTTPS público por
uniformidad con el resto de las integraciones.
