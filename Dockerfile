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

# uv copiado del contenedor oficial con versión CLAVADA (decisión #36): el
# binario es estático y pinearlo evita que un uv nuevo cambie el build.
COPY --from=ghcr.io/astral-sh/uv:0.9.30 /uv /uvx /bin/

# Metadatos OCI: GHCR enlaza la imagen con el repo automáticamente por esta label
LABEL org.opencontainers.image.source="https://github.com/fmr693/k8s-market-sentinel" \
      org.opencontainers.image.description="CEF discount watchtower - single image, multi-command CLI"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # uv debe usar el Python de la imagen base, jamás descargarse uno propio
    UV_PYTHON_DOWNLOADS=never \
    # el cache mount vive en otro filesystem: copiar en vez de hardlink
    UV_LINK_MODE=copy \
    # el venv del proyecto por delante: `sentinel` resuelve sin activar nada
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# CAPA 1 — solo dependencias (decisión #36): se copian ÚNICAMENTE los dos
# ficheros que las definen y se sincroniza sin instalar el proyecto. La capa
# solo se reconstruye si cambian pyproject.toml o uv.lock: editar código
# fuente NO vuelve a instalar pandas. --locked verifica hashes y exige que el
# lock esté al día con pyproject (build roto > deps silenciosamente distintas).
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project

# CAPA 2 — el código (filtrado por .dockerignore: sin .env, sin tests, sin
# .git). Cambia a diario; por eso va DESPUÉS de las dependencias.
COPY . .

# Install EDITABLE a conciencia (comportamiento por defecto de `uv sync` con
# el propio proyecto): el paquete queda en /app/src/sentinel y config.py
# resuelve REPO_ROOT=/app, encontrando config/tickers.yaml y db/migrations en
# las MISMAS rutas relativas que en desarrollo (decisión #15). Con un install
# normal el código iría a site-packages y esas rutas se romperían.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked

# Nunca root dentro del contenedor: si algo escapa del proceso, que se
# encuentre un usuario sin privilegios (y K8s podrá exigirlo vía
# securityContext.runAsNonRoot).
RUN useradd --create-home --uid 1000 sentinel
# USER NUMÉRICO a propósito: el kubelet no lee /etc/passwd de la imagen, así
# que con `USER sentinel` un pod con runAsNonRoot falla con
# "non-numeric user, cannot verify user is non-root" (aprendido en fase 3).
USER 1000

# ENTRYPOINT fijo + CMD variable: `docker run imagen ingest-fx` ejecuta
# `sentinel ingest-fx`; sin argumentos, muestra la ayuda.
ENTRYPOINT ["sentinel"]
CMD ["--help"]
