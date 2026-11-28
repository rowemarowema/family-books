#!/usr/bin/env bash
# Backup-staleness canary for family-books-prod (added 2026-11-21).
#
# The nightly backup (/etc/cron.d/family-books-backup, 09:37 UTC) uploads an
# age-encrypted pg_dump to Backblaze B2. Until 2026-11-21 nothing was scheduled
# and nothing noticed: the last backup was 2026-04-26. This canary is the
# safety net: it emails CANARY_ALERT_TO when the newest backup is older than
# THRESHOLD_HOURS, or when it cannot tell.
#
# Source of truth: the newest *.sql.age object under the app's configured B2
# prefix (bucket + prefix read from the web container's settings via
# books.core.backup.storage.B2Config.from_env(), never hardcoded). Cross-check:
# the newest backup_created row in the app's audit_log; a disagreement is named
# in the alert.
#
# Exit codes (a check that cannot run never looks healthy):
#   0  healthy (one log line, no email)
#   1  stale backup - alert sent
#   2  check could not run (docker / B2 / audit / parse failure) - alert sent
#   3  alert needed but the Postmark send failed, or mail config missing
#   4  DRY_RUN=1 - alert needed, printed instead of sent
#   5  CANARY_ALERT_TO missing, empty or not an address in $ENV_FILE - nothing
#      checked, nothing sent (logged as CONFIG ERROR on every run, dry runs too)
#
# One-run overrides (for tests): THRESHOLD_HOURS=<h>, PREFIX_OVERRIDE=<prefix>,
# DRY_RUN=1.
#
# Alert address: CANARY_ALERT_TO in $ENV_FILE, the same file the Postmark token
# is read from. No address is written in this script.
#
# Installed at /usr/local/bin/backup-staleness-canary.sh; runs daily via
# /etc/cron.d/backup-staleness-canary (15:07 UTC). Source of truth:
# rowemarowema/family-books scripts/ops/backup-staleness-canary.sh.
set -uo pipefail

THRESHOLD_HOURS=${THRESHOLD_HOURS:-48}
ENV_FILE=${ENV_FILE:-/opt/family-books/.env.production}
BACKUP_LOG=${BACKUP_LOG:-/var/log/family-books-backup.log}
WEB=${WEB:-family-books-web}
DRY_RUN=${DRY_RUN:-0}

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { echo "[$(ts)] backup-canary: $*"; }

# Alert address from the env file. Checked before anything else so a missing
# address shows in the log on the first run, not on the day an alert is needed.
TO=$(sed -n 's/^CANARY_ALERT_TO=//p' "$ENV_FILE" 2>/dev/null | head -1 | tr -d '"\r') || TO=
case "$TO" in
    ?*@?*) ;;
    *)
        log "CONFIG ERROR: CANARY_ALERT_TO is missing, empty or not an address in $ENV_FILE - this canary cannot alert anyone. Add CANARY_ALERT_TO=<address> to $ENV_FILE (see scripts/ops/README.md). Exiting 5; nothing was checked."
        exit 5
        ;;
esac

tmp=$(mktemp -d /tmp/backup-canary.XXXXXX) || { log "FAIL: cannot create temp dir"; exit 2; }
chmod 700 "$tmp"
trap 'rm -rf "$tmp"' EXIT

# ── 1. ask the app: newest B2 object + newest audit row ──
docker_rc=0
docker exec -i -e "PREFIX_OVERRIDE=${PREFIX_OVERRIDE-__unset__}" "$WEB" \
    python manage.py shell > "$tmp/probe.out" 2> "$tmp/probe.err" <<'PY' || docker_rc=$?
import json, os
out = {}
try:
    import boto3
    from botocore.config import Config
    from books.core.backup.storage import B2Config
    cfg = B2Config.from_env()
    po = os.environ.get("PREFIX_OVERRIDE", "__unset__")
    prefix = cfg.prefix if po == "__unset__" else po
    out["bucket"], out["prefix"] = cfg.bucket, prefix
    c = boto3.client("s3", endpoint_url=cfg.endpoint_url,
                     aws_access_key_id=cfg.key_id,
                     aws_secret_access_key=cfg.application_key,
                     config=Config(signature_version="s3v4"))
    kw = {"Bucket": cfg.bucket}
    if prefix:
        kw["Prefix"] = prefix
    objs = []
    for page in c.get_paginator("list_objects_v2").paginate(**kw):
        objs += [o for o in (page.get("Contents") or []) if o["Key"].endswith(".sql.age")]
    out["b2_count"] = len(objs)
    if objs:
        n = max(objs, key=lambda o: o["LastModified"])
        out["b2_key"], out["b2_size"] = n["Key"], n["Size"]
        out["b2_time"] = n["LastModified"].isoformat()
    else:
        out["b2_error"] = f"no *.sql.age objects under prefix {prefix!r} in bucket {cfg.bucket!r}"
except Exception as e:
    out["b2_error"] = f"cannot list bucket: {type(e).__name__}: {str(e)[:200]}"
try:
    from books.audit.models import AuditLog
    r = AuditLog.objects.filter(action="backup_created").order_by("-timestamp").first()
    if r is None:
        out["audit_error"] = "no backup_created row in audit_log"
    else:
        out["audit_key"], out["audit_time"] = r.entity_id, r.timestamp.isoformat()
except Exception as e:
    out["audit_error"] = f"cannot read audit table: {type(e).__name__}: {str(e)[:200]}"
print("CANARY_JSON " + json.dumps(out))
PY

# ── 2. evaluate on the host ──
python3 - "$tmp/probe.out" "$THRESHOLD_HOURS" "$docker_rc" > "$tmp/eval.env" <<'PY'
import json, sys, shlex
from datetime import datetime, timezone
path, thr, drc = sys.argv[1], float(sys.argv[2]), int(sys.argv[3])
d, fails = {}, []
try:
    line = [l for l in open(path) if l.startswith("CANARY_JSON ")][-1]
    d = json.loads(line[len("CANARY_JSON "):])
except Exception as e:
    fails.append(f"no result from the web container (docker exec rc={drc}; {type(e).__name__})")
if d.get("b2_error"): fails.append(d["b2_error"])
if d.get("audit_error"): fails.append(d["audit_error"])
now = datetime.now(timezone.utc)
age_h = None
if d.get("b2_time"):
    age_h = (now - datetime.fromisoformat(d["b2_time"])).total_seconds() / 3600
notes = []
if d.get("b2_key") and d.get("audit_key"):
    b2_name = d["b2_key"].rsplit("/", 1)[-1]
    if b2_name != d["audit_key"]:
        notes.append(f"B2 and audit_log DISAGREE: newest B2 object {b2_name}, newest audit backup_created {d['audit_key']} at {d['audit_time']}")
if fails:
    status = "CANNOT_TELL"
elif age_h is not None and age_h > thr:
    status = "STALE"
else:
    status = "HEALTHY"
size = d.get("b2_size")
desc = (f"newest backup {d.get('b2_key')} ({size} bytes, {age_h:.1f} h old, uploaded {d.get('b2_time')})"
        if age_h is not None else "newest backup UNKNOWN")
summary = f"{status}: {desc}; threshold {thr:g} h; bucket {d.get('bucket')} prefix {d.get('prefix')!r}"
if fails: summary += "; FAILURES: " + " | ".join(fails)
if notes: summary += "; " + " | ".join(notes)
if status == "STALE":
    subj = f"[backup-canary] family-books backup is {age_h:.1f} h old (threshold {thr:g} h)"
elif status == "CANNOT_TELL":
    subj = "[backup-canary] family-books backup check could not run - " + fails[0][:120]
else:
    subj = ""
for k, v in (("STATUS", status), ("SUMMARY", summary), ("SUBJECT", subj)):
    print(f"{k}={shlex.quote(v)}")
PY
[ -s "$tmp/eval.env" ] || { log "FAIL: could not evaluate the probe result"; exit 2; }
# shellcheck disable=SC1091
. "$tmp/eval.env"

if [ "$STATUS" = HEALTHY ]; then
    log "$SUMMARY"
    exit 0
fi

log "ALERT $SUMMARY"
errline=$(grep -vE 'AXES|objects imported|^[[:space:]]*$' "$tmp/probe.err" 2>/dev/null | tail -1 | cut -c1-200)
[ -n "$errline" ] && log "web probe stderr (last line): $errline"
case "$STATUS" in STALE) want=1 ;; *) want=2 ;; esac

# ── 3. build and send the alert ──
if [ -f "$BACKUP_LOG" ]; then logtail=$(tail -n 20 "$BACKUP_LOG"); else logtail="(no $BACKUP_LOG on this box yet)"; fi
BODY="$SUMMARY

Last 20 lines of $BACKUP_LOG:
$logtail

Checks: tail $BACKUP_LOG ; cat /etc/cron.d/family-books-backup ; APP_DIR=/opt/family-books /opt/family-books/scripts/cron-backup.sh (manual run)"

if [ "$DRY_RUN" = 1 ]; then
    log "DRY RUN - not sent. Subject: $SUBJECT"
    printf '%s\n' "$BODY" | sed 's/^/    | /'
    exit 4
fi

TOKEN=$(grep -E '^EMAIL_HOST_PASSWORD=' "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '"')
FROM=$(grep -E '^DEFAULT_FROM_EMAIL=' "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '"')
if [ -z "$TOKEN" ] || [ -z "$FROM" ] || [ -z "$TO" ]; then
    log "FAIL: cannot send alert - missing EMAIL_HOST_PASSWORD/DEFAULT_FROM_EMAIL/recipient"
    exit 3
fi
FROM="$FROM" TO="$TO" SUBJECT="$SUBJECT" BODY="$BODY" python3 -c '
import json, os
print(json.dumps({"From": os.environ["FROM"], "To": os.environ["TO"], "Subject": os.environ["SUBJECT"],
                  "TextBody": os.environ["BODY"], "MessageStream": "outbound"}))' > "$tmp/payload.json"
http_code=$(curl -s -o "$tmp/pm.out" -w '%{http_code}' --max-time 30 -X POST 'https://api.postmarkapp.com/email' \
    -H "X-Postmark-Server-Token: $TOKEN" -H 'Content-Type: application/json' -H 'Accept: application/json' \
    --data-binary @"$tmp/payload.json") || http_code=000
unset TOKEN
if [ "$http_code" = 200 ]; then
    log "alert sent: http 200 $(cat "$tmp/pm.out")"
    exit "$want"
fi
log "FAIL: alert POST http $http_code ($(cat "$tmp/pm.out" 2>/dev/null))"
exit 3
