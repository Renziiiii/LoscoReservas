# LoscoReservas — single lightweight container (runs beside Chatwoot on LoscoServer).
# Multi-stage: stage 1 builds the web/ bundle (Svelte + FullCalendar) with Node,
# stage 2 ships only Python + the prebuilt static assets. The web/ source tree
# and node_modules never reach the runtime image.

FROM node:24-slim AS webbuild
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
# Build straight into the app static dir layout (base=/static/calendar/).
RUN npm run build -- --outDir /web-dist

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /srv

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
# Prebuilt calendar bundle (built in the webbuild stage).
COPY --from=webbuild /web-dist ./app/static/calendar

# Non-root runtime user; /data holds the SQLite volume.
RUN useradd --system --create-home reservas \
    && mkdir -p /data \
    && chown reservas:reservas /data
USER reservas
ENV LOSCO_DATABASE_PATH=/data/reservas.db

EXPOSE 8000
# Single worker on purpose: the deploy server has 1 core, and SQLite writes
# are serialized anyway.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
