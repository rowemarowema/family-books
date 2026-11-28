#!/usr/bin/env bash
# Cert-expiry canary — family-books-prod copy (adapted 2026-11-21 from the
# rowe-contest canary; books.markrowecontest.com's cert expired Jul 24 2026 for
# the same standalone-vs-:80 reason). Original header follows.
#
# Cert-expiry canary. The Let's Encrypt renewal for markrowecontest.com failed
# SILENTLY for ~30 days (standalone authenticator couldn't bind :80 while the
# nginx container held it), and the cert expired 2026-07-21 taking the site
# down. Root cause fixed (switched to webroot). This canary is the safety net:
# if renewal ever silently breaks again, it emails admins while there is still
# runway, instead of the next signal being a dead site.
#
# Installed at /usr/local/bin/cert-expiry-canary.sh; runs daily via
# /etc/cron.d/cert-expiry-canary (08:17 UTC). Source of truth:
# rowemarowema/family-books scripts/ops/cert-expiry-canary.sh.
#
# Two checks (#1052):
#   1. DISK   — the cert file certbot writes. Alerts within THRESHOLD_DAYS of
#               expiry; since renewal happens at 30 days out, that only occurs if
#               renewal has been failing for 16+ days.
#   2. SERVED — the cert nginx is actually serving (openssl s_client with SNI).
#               Alerts when the served cert is within THRESHOLD_DAYS of expiry, or
#               when its SHA-256 fingerprint differs from the disk cert's (renewed
#               on disk, nginx never reloaded — the 2026-07-21 shape, which the
#               disk check alone reports as healthy). A mismatch inside
#               MISMATCH_GRACE_HOURS of the disk cert's notBefore is not alerted:
#               the certbot deploy hook reloads nginx in the same run.
# Normal operation: never fires.
#
# DRY_RUN=1 prints what it would send and exits 3 instead of mailing (exit 0 when
# healthy either way). SERVED_PEM=<file> reads the "served" cert from a file
# instead of the network — for tests only.
#
# Alert address: CANARY_ALERT_TO in $ENV_FILE (the same file the Postmark token
# is read from). No address is written in this script. If CANARY_ALERT_TO is
# missing or empty, EVERY run (dry runs included) logs a CONFIG ERROR line and
# exits 5 before checking anything, so the gap shows in the log the same day
# rather than on the day an alert is needed.
#
# Exit codes: 0 healthy; 1 cert file missing or alert send failed; 3 dry run,
# alert needed; 5 CANARY_ALERT_TO missing (nothing checked, nothing sent).
set -euo pipefail

CERT=${CERT:-/etc/letsencrypt/live/books.markrowecontest.com/fullchain.pem}
THRESHOLD_DAYS=${THRESHOLD_DAYS:-14}
MISMATCH_GRACE_HOURS=${MISMATCH_GRACE_HOURS:-2}
ENV_FILE=${ENV_FILE:-/opt/family-books/.env.production}
DOMAIN=books.markrowecontest.com
DRY_RUN=${DRY_RUN:-0}

# Alert address from the env file. `|| TO=` keeps set -e from exiting silently
# when the file or the line is missing; the check below reports it instead.
TO=$(sed -n 's/^CANARY_ALERT_TO=//p' "$ENV_FILE" 2>/dev/null | head -1 | tr -d '"\r') || TO=
case "$TO" in
  ?*@?*) ;;
  *)
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] canary: CONFIG ERROR: CANARY_ALERT_TO is missing, empty or not an address in $ENV_FILE - this canary cannot alert anyone. Add CANARY_ALERT_TO=<address> to $ENV_FILE (see scripts/ops/README.md). Exiting 5; nothing was checked."
    exit 5
    ;;
esac

[ -f "$CERT" ] || { echo "canary: cert missing at $CERT" >&2; exit 1; }

now_epoch=$(date +%s)
epoch_of() { date -d "$1" +%s; }

# ── 1. disk ──
disk_not_after=$(openssl x509 -enddate -noout -in "$CERT" | cut -d= -f2)
disk_not_before=$(openssl x509 -startdate -noout -in "$CERT" | cut -d= -f2)
disk_fp=$(openssl x509 -fingerprint -sha256 -noout -in "$CERT" | cut -d= -f2)
disk_days=$(( ($(epoch_of "$disk_not_after") - now_epoch) / 86400 ))
echo "canary: $DOMAIN disk cert has $disk_days day(s) left (threshold $THRESHOLD_DAYS)"

reasons=()
[ "$disk_days" -ge "$THRESHOLD_DAYS" ] || reasons+=("the certificate on disk expires in $disk_days day(s) - renewal may be broken")

# ── 2. served ──
served_pem=""
if [ -n "${SERVED_PEM:-}" ]; then
  served_pem=$(openssl x509 -in "$SERVED_PEM" 2>/dev/null || true)
else
  for attempt in 1 2 3; do
    served_pem=$(timeout 20 openssl s_client -connect "$DOMAIN:443" -servername "$DOMAIN" </dev/null 2>/dev/null \
      | openssl x509 2>/dev/null || true)
    [ -n "$served_pem" ] && break
    sleep 5
  done
fi

if [ -z "$served_pem" ]; then
  echo "canary: $DOMAIN served cert UNREADABLE (s_client, 3 attempts)"
  reasons+=("the served certificate could not be read from $DOMAIN:443 after 3 attempts - the site may be down or TLS broken")
else
  served_not_after=$(printf '%s\n' "$served_pem" | openssl x509 -enddate -noout | cut -d= -f2)
  served_fp=$(printf '%s\n' "$served_pem" | openssl x509 -fingerprint -sha256 -noout | cut -d= -f2)
  served_days=$(( ($(epoch_of "$served_not_after") - now_epoch) / 86400 ))
  if [ "$served_fp" = "$disk_fp" ]; then match=same; else match=DIFFERENT; fi
  echo "canary: $DOMAIN served cert has $served_days day(s) left; notAfter served=$served_not_after disk=$disk_not_after; fingerprint $match"
  [ "$served_days" -ge "$THRESHOLD_DAYS" ] || reasons+=("the certificate nginx is SERVING expires in $served_days day(s)")
  if [ "$match" = DIFFERENT ]; then
    since_renewal_h=$(( (now_epoch - $(epoch_of "$disk_not_before")) / 3600 ))
    if [ "$since_renewal_h" -ge "$MISMATCH_GRACE_HOURS" ]; then
      reasons+=("renewed on disk but nginx is serving a different (old) certificate: served notAfter $served_not_after, disk notAfter $disk_not_after - reload nginx (docker exec family-books-nginx nginx -s reload)")
    else
      echo "canary: fingerprint mismatch inside the ${MISMATCH_GRACE_HOURS}h post-renewal grace (disk cert ${since_renewal_h}h old) - not alerting"
    fi
  fi
fi

[ "${#reasons[@]}" -eq 0 ] && { echo "canary: healthy"; exit 0; }

joined=$(printf '%s; ' "${reasons[@]}"); joined=${joined%; }
SUBJECT="[cert-canary] $DOMAIN TLS certificate problem - ${reasons[0]}"
BODY="$DOMAIN: $joined. Checks: certbot renew --dry-run ; systemctl status certbot.timer ; tail /var/log/letsencrypt/letsencrypt.log ; openssl s_client -connect $DOMAIN:443 -servername $DOMAIN | openssl x509 -noout -enddate. If the served cert expires the site goes down (SEC_ERROR_EXPIRED_CERTIFICATE), as happened 2026-07-21."

if [ "$DRY_RUN" = 1 ]; then
  echo "canary: ALERT (dry run, not sent): $SUBJECT"
  echo "canary: body: $BODY"
  exit 3
fi

# Alert via Postmark (same token the app sends with).
# Postmark SMTP uses the server token as the SMTP password (EMAIL_HOST=smtp.postmarkapp.com).
TOKEN=$(grep -E '^EMAIL_HOST_PASSWORD=' "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '"')
FROM=$(grep -E '^DEFAULT_FROM_EMAIL=' "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '"')
# TO was read from CANARY_ALERT_TO at the top of the script.
[ -n "$TOKEN" ] && [ -n "$FROM" ] && [ -n "$TO" ] || { echo 'canary: missing EMAIL_HOST_PASSWORD/DEFAULT_FROM_EMAIL/recipient' >&2; exit 1; }

http_code=$(curl -s -o /tmp/canary-pm.out -w '%{http_code}' -X POST 'https://api.postmarkapp.com/email'   -H "X-Postmark-Server-Token: $TOKEN" -H 'Content-Type: application/json' -H 'Accept: application/json'   -d "{\"From\":\"$FROM\",\"To\":\"$TO\",\"Subject\":\"$SUBJECT\",\"TextBody\":\"$BODY\",\"MessageStream\":\"outbound\"}")
echo "canary: alert POST http $http_code ($(cat /tmp/canary-pm.out))"
[ "$http_code" = '200' ]
