# syntax=docker/dockerfile:1
# =============================================================================
# TPV Moderno — imagen de producción (fase Despliegue).
#
# Imagen ÚNICA y autocontenida (§1.4): la API sirve también los tres
# frontends, de modo que «apuntar el navegador a la IP del servidor» es toda
# la configuración del terminal. Contexto de build: raíz del repositorio.
#
#   docker build -f deploy/docker/Dockerfile .
# =============================================================================

# ---- Frontend mostrador (/app/tpv) ------------------------------------------
FROM node:20-alpine AS web-mostrador
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build -- --base=/app/tpv/

# ---- Frontend móvil (/app/movil) --------------------------------------------
FROM node:20-alpine AS web-movil
WORKDIR /build
COPY frontend/mobile/package.json frontend/mobile/package-lock.json ./
RUN npm ci
COPY frontend/mobile/ ./
RUN npm run build -- --base=/app/movil/

# ---- Frontend administración (/app/admin) -----------------------------------
FROM node:20-alpine AS web-admin
WORKDIR /build
COPY frontend/admin/package.json frontend/admin/package-lock.json ./
RUN npm ci
COPY frontend/admin/ ./
RUN npm run build -- --base=/app/admin

# ---- Servidor (API + frontends + utilidades de BD) --------------------------
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# pg_dump/pg_restore para que el endpoint de backups de la API funcione dentro
# del contenedor. El cliente de Debian es de un major IGUAL O SUPERIOR al del
# servidor (fijado a 16 por la imagen postgres:16-alpine), y pg_dump oficialmente
# soporta volcar servidores de major igual o anterior al suyo. Sin repo PGDG:
# menos piezas externas que rompan el build.
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/app

# Dependencias y código de la API.
COPY backend/pyproject.toml ./
COPY backend/app ./app
COPY backend/alembic.ini ./
COPY backend/alembic ./alembic

# Utilidades de puesta en marcha (sin credenciales: ADR-008).
COPY deploy/create_admin.py ./
COPY deploy/docker/seed_once.py ./
COPY docs/database/seed.sql ./

RUN pip install --no-cache-dir . \
 && useradd --create-home --uid 1000 tpv \
 && mkdir -p /data /backups /srv/frontends \
 && chown -R tpv:tpv /srv/app /data /backups /srv/frontends

COPY --from=web-mostrador /build/dist /srv/frontends/tpv
COPY --from=web-movil /build/dist /srv/frontends/movil
COPY --from=web-admin /build/dist /srv/frontends/admin

USER tpv
EXPOSE 8000

# En cada arranque: migraciones (alembic es incremental) + seed idempotente
# y condicional (solo si la BD está vacía). Después exec sustituye la shell
# por uvicorn para que reciba las señales de parada como PID 1.
# UN SOLO worker: el hub de WebSocket asume un proceso único (§8).
CMD ["sh", "-c", "alembic upgrade head && python seed_once.py && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
