# Orbix Signal Lab - single-process Python image.
#
# One worker only: DuckDB and SQLite are single-process stores and a second
# worker would fight the same files. Everything persistent lives in /data.
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ORBIX_DATA_DIR=/data \
    PORT=8000

WORKDIR /app

# Dependencies first so application edits do not invalidate the layer.
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY orbix ./orbix
COPY web ./web
COPY pyproject.toml README.md .env.example ./

# Unprivileged runtime user; /data is the only writable path it needs.
RUN useradd --create-home --uid 10001 orbix \
    && mkdir -p /data \
    && chown -R orbix:orbix /data /app
USER orbix

VOLUME ["/data"]
EXPOSE 8000

# /healthz is public on purpose (see PUBLIC_PATHS) so the probe works even when
# dashboard authentication is enabled.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT','8000'), timeout=4)"

# Shell form so a platform-injected $PORT (Render, Railway, Cloud Run) is honoured.
CMD ["sh", "-c", "exec uvicorn orbix.api:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --no-server-header"]
