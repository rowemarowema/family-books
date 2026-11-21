#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# cron-backup.sh — nightly encrypted backup wrapper for crontab.
#
# Invokes `python manage.py backup_db --reason scheduled` inside the
# `web` container. The backup_db management command does the real work
# (pg_dump → age encrypt → B2 upload → retention prune → audit row);
# this script is just the cron entry point + logging.
#
# Intended usage (crontab -e on the droplet, 03:00 UTC daily):
#   0 3 * * * /home/app/family-books/scripts/cron-backup.sh \
#       >> /var/log/family-books-backup.log 2>&1
#
# Production prefix: backup_db reads B2_PREFIX from .env.production
# (default: prod/). Drill runs use dev-test/ via drill_rollback's
# explicit --prefix override. NEVER co-mingle.
# -----------------------------------------------------------------------------
set -euo pipefail

APP_DIR="${APP_DIR:-/home/app/family-books}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
ENV_FILE="${ENV_FILE:-.env.production}"

cd "$APP_DIR"

log() {
    printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"
}

log "START: scheduled backup"

docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" \
    exec -T web python manage.py backup_db --reason scheduled

log "DONE: scheduled backup"
