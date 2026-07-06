# Imagen ÚNICA con las cuatro caras del CLI (decisión #5): K8s elige cuál
# ejecutar vía `args:` — ["migrate"], ["ingest-prices"], ["ingest-nav"]...
#
# ¿Por qué NO multi-stage? Todas las dependencias llegan como wheels
# precompilados (psycopg[binary], pandas...): no hay toolchain de C que
# separar en un stage de build. Un stage único es más legible; la puerta de
# escape (multi-stage) queda para cuando haga falta compilar algo.
#
# ¿Por qué slim y no alpine? alpine usa musl y obligaría a compilar pandas
# (minutos de build y sorpresas). slim es Debian recortado: wheels estándar.

FROM python:3.13-slim

# Metadatos OCI: GHCR enlaza la imagen con el repo automáticamente por esta label
LABEL org.opencontainers.image.source="https://github.com/fmr693/k8s-market-sentinel" \
      org.opencontainers.image.description="CEF discount watchtower - single image, multi-command CLI"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Todo el repo (filtrado por .dockerignore: sin .env, sin tests, sin .git)
COPY . .

# Install EDITABLE (-e) a conciencia: el paquete queda en /app/src/sentinel y
# config.py resuelve REPO_ROOT=/app, encontrando config/tickers.yaml y
# db/migrations en las MISMAS rutas relativas que en desarrollo. Con un
# install normal el código iría a site-packages y esas rutas se romperían.
# El cache mount de BuildKit conserva los wheels descargados entre builds:
# cambiar código fuente no vuelve a bajar pandas.
RUN --mount=type=cache,target=/root/.cache/pip pip install -e .

# Nunca root dentro del contenedor: si algo escapa del proceso, que se
# encuentre un usuario sin privilegios (y K8s podrá exigirlo vía
# securityContext.runAsNonRoot).
RUN useradd --create-home --uid 1000 sentinel
USER sentinel

# ENTRYPOINT fijo + CMD variable: `docker run imagen ingest-fx` ejecuta
# `sentinel ingest-fx`; sin argumentos, muestra la ayuda.
ENTRYPOINT ["sentinel"]
CMD ["--help"]
