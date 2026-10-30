# Deploy en Dokploy (servidor de pruebas)

Guía para montar LoscoReservas en un servidor con [Dokploy](https://dokploy.com).
Dokploy usa Traefik como reverse proxy con Let's Encrypt automático, así que
el HTTPS (necesario para el iframe en Chatwoot) sale sin configurar nada a mano.

## Por qué tipo "Application" (y no Docker Compose)

El repo ya trae `Dockerfile` + `docker-compose.yml`, pero en Dokploy conviene
el tipo **Application**: él solo clona el repo de GitHub, buildea el Dockerfile,
inyecta las variables de entorno y gestiona el dominio. El compose queda para
un server sin Dokploy.

## Pasos

### 1. Conectar GitHub (repo privado)

En Dokploy: **Settings → Git Providers → GitHub**. Dos opciones:

- **GitHub App** (recomendado): instalás la app de Dokploy en tu cuenta de
  GitHub y le das acceso al repo `Renziiiii/LoscoReservas`.
- **Personal Access Token**: token clásico con scope `repo`.

### 2. Crear la aplicación

**Projects → tu proyecto → Create → Application**

- **Name**: `losco-reservas`
- **Source**: GitHub → repo `Renziiiii/LoscoReservas`, branch `master`
- **Build type**: `Dockerfile` (lo detecta solo; el archivo está en la raíz)

### 3. Variables de entorno (pestaña Environment)

Para Applications las variables se inyectan directo al contenedor. Mínimo:

```
LOSCO_USERS=valen:tucontraseña
LOSCO_JWT_SECRET=<openssl rand -hex 32>
LOSCO_TZ=America/Argentina/Buenos_Aires
```

Opcional para probar el iframe en Chatwoot (ver `docs/iframe-chatwoot.md`):

```
LOSCO_IFRAME_KEY=<openssl rand -hex 24>
LOSCO_IFRAME_ORIGINS=https://chatwoot.loscobar.com.ar
```

> La mensajería (`LOSCO_SCHEDULER_ENABLED`, token de Chatwoot, plantillas
> Meta) va **apagada** hasta tener las credenciales de Valen.

### 4. Persistencia del SQLite (Advanced → Mounts)

El Dockerfile ya apunta la base a `/data/reservas.db`. Creá un mount para
que los datos sobrevivan a cada redeploy:

- **Advanced → Mounts → Volume Mount**
- **Volume name**: `reservas-data`
- **Mount path**: `/data`

(Volumen nombrado = podés activar **Volume Backups** a S3 después si querés.)

### 5. Dominio (pestaña Domains)

Dos caminos:

- **Dominio propio** (necesario para el iframe en Chatwoot, que requiere
  HTTPS): creá un registro `A` en el DNS del dominio apuntando a la IP del
  server de pruebas, y en Dokploy: **Host** `reservas.tudominio.com`,
  **Container port** `8000`, **HTTPS** on, **Certificate** letsencrypt.
- **Dominio gratis** `*.traefik.me` (solo HTTP): sirve para probar dashboard
  y API, no alcanza para el iframe (contenido mixto bloqueado por el
  navegador).

El **Container port** es `8000` (uvicorn adentro del contenedor); no hace
falta exponer ningún puerto público.

### 6. Deploy

Botón **Deploy**. Seguí el build en la pestaña **Deployments** y los logs en
**Logs**. Verificación:

```
curl https://reservas.tudominio.com/api/health   # {"status":"ok"}
```

Dashboard: `https://reservas.tudominio.com/login` — API docs: `/docs`.

### 7. Si querés redeploy automático

En **General** activá **AutoDeploy**: cada push a `master` del repo de GitHub
dispara un deploy.

## Chequeo rápido post-deploy

| Qué | Cómo |
| --- | --- |
| App viva | `GET /api/health` → `{"status":"ok"}` |
| Login | `POST /api/auth/login` con usuario de `LOSCO_USERS` |
| CRUD | `GET /api/reservas` con el token Bearer |
| Persistencia | Crear una reserva, redeployar, verificar que sigue |
