# LoscoReservas — single lightweight container (runs beside Chatwoot on LoscoServer).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /srv

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

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
