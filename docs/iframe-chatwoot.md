# Iframe de reservas en Chatwoot (S4)

Muestra las reservas del contacto directamente en la vista de conversación
de Chatwoot — funciona igual para WhatsApp, Instagram y Messenger, porque
Chatwoot resuelve el canal y el contacto.

## Cómo funciona

Chatwoot **no** permite poner el teléfono en la URL del iframe. El
mecanismo oficial es **Dashboard Apps** (issue chatwoot#1587, PR #4761):

1. La URL que se configura en Chatwoot es **estática**.
2. Cuando el agente abre una conversación, Chatwoot le envía a esa página
   el contexto (contacto + conversación) como un **window event**
   (`appContext`).
3. Nuestra página escucha ese evento, saca `contact.phone_number` y pide
   los datos a la API.

Endpoints (todos autorizados por la **clave de embed**):

| Endpoint | Uso |
| --- | --- |
| `/iframe/app?key=K` | La página que se configura como Dashboard App (incluye el formulario de alta, T9) |
| `GET /iframe/reservas.json?tel=...&key=K` | Datos JSON que consume esa página |
| `POST /iframe/reservas.json?key=K` | Alta de reserva desde la pestaña (misma validación que la API) |
| `/iframe/reservas?tel=...&key=K` | Página HTML directa (debug / enlace manual) |

La pestaña permite **registrar reservas sin salir de Chatwoot**: el
formulario nace pre-cargado con el nombre y teléfono del contacto de la
conversación (vienen en el `appContext`) y el agente completa fecha, hora,
personas y detalles. La edición y la baja siguen haciéndose desde el
dashboard.

La clave viaja en la URL de la página configurada y se reutiliza para el
fetch de datos; no se hardcodea en el fuente. Sin clave configurada, todo
responde 403 (fail-closed).

## Configuración

Variables de entorno (prefijo `LOSCO_`):

```
LOSCO_IFRAME_KEY=<secreto generado>          # ej.: openssl rand -hex 24
LOSCO_IFRAME_ORIGINS=https://chat.tudominio  # origen de tu Chatwoot
```

`LOSCO_IFRAME_ORIGINS` controla el header `Content-Security-Policy:
frame-ancestors` (default `*`; restringilo al dominio de Chatwoot en
producción).

## Pasos en Chatwoot

1. Andá a **Settings → Integrations → Dashboard apps → Configure**.
2. Nombre: `Reservas Losco`. URL:
   `https://reservas.tudominio/iframe/app?key=<tu clave>`.
3. Guardá. Al abrir una conversación aparece una pestaña "Reservas Losco"
   con las reservas del contacto (ordenadas por fecha y hora).

## Notas

- **El payload llega como string JSON** (verificado en vivo, 2026-10): Chatwoot
  hace `postMessage` del appContext serializado; nuestra página lo parsea con
  `JSON.parse` y acepta también objeto. Además envía
  `chatwoot-dashboard-app:fetch-info` al cargar para pedir el contexto bajo
  demanda (mecanismo documentado) — cubre el caso en que Chatwoot manda el
  contexto antes de que el listener esté attachado.

- El match del teléfono es **por dígitos**: Chatwoot manda E.164
  (`+54911…`) y nuestras reservas guardan el formato original — ambos
  coinciden.
- El shape exacto del evento `appContext` (campos de `contact`) se
  verifica en vivo contra el Chatwoot real de LoscoServer en la tarea de
  deploy (T7); los docs oficiales muestran `contact` con
  `phone_number` entre sus atributos.
- Contacto sin teléfono en Chatwoot → mensaje "El contacto no tiene
  teléfono cargado". Teléfono sin reservas → "Sin reservas para este
  contacto."
