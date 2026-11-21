#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# deploy.sh — update Family Books to the latest code on this droplet.
#
# Run this from /home/app/family-books after pushing new code to your repo.
# It will:
#   1. Pull the latest source from git (if this is a git checkout)
#   2. Rebuild the production Docker image
#   3. Apply any pending Django migrations against the live database
#   4. Restart the `web` service with zero downtime (postgres + nginx stay up)
#   5. Reload nginx
#
# Usage:
#   ./scripts/deploy.sh
#
# Env overrides:
#   SKIP_PULL=1      skip `git pull` (useful if you rsync'd files manually)
#   SKIP_MIGRATE=1   skip `manage.py migrate`
#
# Mirrors the rowe-contest deploy.sh shape; Django substitutions for
# Prisma/npm.
# -----------------------------------------------------------------------------
set -euo pipefail

APP_DIR="${APP_DIR:-/home/app/family-books}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
ENV_FILE="${ENV_FILE:-.env.production}"
COMPOSE=(docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE")

cd "$APP_DIR"

step() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

# --- 1. Pull latest ---
if [ -z "${SKIP_PULL:-}" ] && [ -d .git ]; then
    step "Pulling latest code"
    git pull --ff-only
else
    step "Skipping git pull"
fi

# --- 2. Build ---
step "Building production image (Dockerfile)"
"${COMPOSE[@]}" build web

# --- 3. Make sure postgres is up before running migrations ---
step "Ensuring postgres is running"
"${COMPOSE[@]}" up -d postgres

# Wait for postgres healthcheck.
step "Waiting for postgres to report healthy"
for _ in $(seq 1 30); do
    status="$(docker inspect -f '{{.State.Health.Status}}' family-books-postgres 2>/dev/null || echo starting)"
    if [ "$status" = "healthy" ]; then
        break
    fi
    sleep 2
done
if [ "$status" != "healthy" ]; then
    echo "postgres did not become healthy in 60s — aborting" >&2
    exit 1
fi

# --- 4. Migrate ---
if [ -z "${SKIP_MIGRATE:-}" ]; then
    step "Applying Django migrations"
    "${COMPOSE[@]}" run --rm --no-deps web python manage.py migrate --noinput
else
    step "Skipping migrate"
fi

# --- 5. Zero-downtime restart of the web container ---
# `--no-deps` leaves postgres/nginx untouched; only web is recreated.
step "Restarting web container"
"${COMPOSE[@]}" up -d --no-deps --build web

# --- 6. Reload nginx ---
# Cheap; picks up any upstream changes (e.g., if web's IP shifted on
# the bridge network). Fall back to `up -d nginx` if exec fails
# (e.g., nginx wasn't running for some reason).
step "Reloading nginx"
"${COMPOSE[@]}" exec -T nginx nginx -s reload || "${COMPOSE[@]}" up -d nginx

step "Deploy complete"
"${COMPOSE[@]}" ps
