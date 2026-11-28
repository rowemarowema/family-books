# scripts/ops

Operator files for **family-books-prod** (174.138.76.241). The app never runs
them. Each file says what it is and where it is installed:

| File | What it is | Where it is installed |
|---|---|---|
| `cert-expiry-canary.sh` | Daily TLS-certificate canary for books.markrowecontest.com. It checks the cert on disk and the cert nginx serves, and emails `CANARY_ALERT_TO` through Postmark when either is near expiry or they differ. | `/usr/local/bin/cert-expiry-canary.sh` (root, 755). Cron `/etc/cron.d/cert-expiry-canary` runs it at 08:17 UTC and appends to `/var/log/cert-expiry-canary.log`. |
| `backup-staleness-canary.sh` | Daily check that the newest `*.sql.age` in B2, under the web container's `B2_PREFIX`, is under 48 h old. It cross-checks the newest `backup_created` audit row and emails `CANARY_ALERT_TO` when the backup is stale or the check cannot run. | `/usr/local/bin/backup-staleness-canary.sh` (root, 755). Cron `/etc/cron.d/backup-staleness-canary` runs it at 15:07 UTC and appends to `/var/log/backup-staleness-canary.log`. |
| `pg-counts.sh` | Exact per-table row counts plus trigger and function counts, run inside a postgres container. Diff two runs to prove a dump/restore moved everything. | Not installed. Piped in with `docker exec -i <container> sh -s < scripts/ops/pg-counts.sh` (see the Dec 6 runbook). |
| `box-compose.diff` | Record of the hand edit made to the box's `docker-compose.prod.yml` on Nov 21 2026: the nginx 1.30.7 digest pin and the age-key mount on web. Both parts were merged in #1 and #2. | Not installed. Historical record only. |
| `volumes-only.patch` | The age-key-mount half of that edit on its own, as merged in #2. The Postgres 17 PR removes the mount again. | Not installed. Historical record only. |

## CANARY_ALERT_TO

Both canaries read the alert address from **`CANARY_ALERT_TO` in
`/opt/family-books/.env.production`**, the same file their Postmark token comes
from. No address is written in this repo.

- **Add the line before installing** either script:
  `CANARY_ALERT_TO=<address>`.
- **If the line is missing, empty or not an address,** every run logs a
  `CONFIG ERROR: CANARY_ALERT_TO …` line and exits **5** before checking
  anything. That includes `DRY_RUN=1` runs.
- **Check that it is set without printing it:**
  `grep -c '^CANARY_ALERT_TO=.*@' /opt/family-books/.env.production` should print `1`.
- **Test without sending:** `DRY_RUN=1` on both scripts.
  `PREFIX_OVERRIDE=<prefix>` points the staleness check at another B2 prefix
  for one run.

## Installing a new copy

```sh
cd /opt/family-books
f=backup-staleness-canary            # or cert-expiry-canary
cp -p /usr/local/bin/$f.sh /root/backups/$f.sh.$(date -u +%Y%m%dT%H%M%SZ)
bash -n scripts/ops/$f.sh && install -m 755 -o root -g root scripts/ops/$f.sh /usr/local/bin/$f.sh && md5sum scripts/ops/$f.sh /usr/local/bin/$f.sh
DRY_RUN=1 /usr/local/bin/$f.sh; echo "rc=$?"   # 0 = healthy, 5 = CANARY_ALERT_TO missing
```
