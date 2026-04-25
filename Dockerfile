# syntax=docker/dockerfile:1.6
#
# Production image for Family Books.
#
# Multi-stage:
#   builder    install build deps + pip wheels into a venv
#   runner     minimal runtime; copies the venv + app code; runs as
#              an unprivileged user; gunicorn ENTRYPOINT
#
# Mirrors the rowe-contest pattern (multi-stage, non-root user,
# explicit ENTRYPOINT) adapted for Python/Django.

# ---------- builder ----------
FROM python:3.12-slim AS builder
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Build deps for psycopg, cryptography, weasyprint compile path.
# (The runner stage installs runtime libs; this stage only needs
# what's required to compile any wheels that don't ship binaries.)
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
 && rm -rf /var/lib/apt/lists/*

# Create the venv in a known location so the runner stage can copy
# it verbatim. /opt/venv keeps app code separate from interpreter +
# deps for clean layer caching.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install dependencies first (layer caches well — pyproject.toml
# changes less often than app code).
COPY pyproject.toml README.md ./
COPY books ./books
COPY family_books ./family_books
COPY manage.py ./

RUN pip install --upgrade pip \
 && pip install .

# ---------- runner ----------
FROM python:3.12-slim AS runner
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=family_books.settings.prod

WORKDIR /app

# Runtime system deps:
#   libpango-1.0-0 / libpangoft2-1.0-0 / libgdk-pixbuf2.0-0 — WeasyPrint
#       (PDF export, F.5; W001 check fires if these are missing)
#   age — encrypted backup tooling (backup_db / restore_db, H.1/H.2)
#   postgresql-client — pg_dump / pg_restore (backup_db, restore_db)
#   tini — proper PID 1 / signal forwarding for gunicorn workers
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libgdk-pixbuf-2.0-0 \
        age \
        postgresql-client \
        tini \
 && rm -rf /var/lib/apt/lists/* \
 && addgroup --system django \
 && adduser --system --ingroup django --uid 1001 django

# Copy the venv from the builder (interpreter + dep wheels).
COPY --from=builder --chown=django:django /opt/venv /opt/venv

# Copy app code separately so its layer doesn't bust on dep changes
# (or vice versa).
COPY --chown=django:django manage.py pyproject.toml README.md ./
COPY --chown=django:django books ./books
COPY --chown=django:django family_books ./family_books
COPY --chown=django:django templates ./templates
COPY --chown=django:django fixtures ./fixtures

# Static files: collectstatic at build time so the image carries
# them (WhiteNoise serves them at runtime). Build-time SECRET_KEY
# is a placeholder; collectstatic doesn't read secrets.
ENV SECRET_KEY="build-time-placeholder-only-not-for-runtime-not-a-secret"
ENV ALLOWED_HOSTS="build.example.com"
RUN python manage.py collectstatic --noinput --clear

USER django
EXPOSE 8000

ENTRYPOINT ["/usr/bin/tini", "--"]
# 2 workers: single-user low-traffic; conservative.
# 60s timeout: matches nginx proxy_read_timeout in deploy/nginx.conf.
# --access-logfile - / --error-logfile -: logs to stdout/stderr so
# `docker compose logs` captures them.
CMD ["gunicorn", \
     "family_books.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "2", \
     "--timeout", "60", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
