# Family Books — rollback runbook

How to recover from each failure mode that's serious enough to need a
runbook. Living document; entries are added when a real failure
exposes a procedure that wasn't yet written down.

Per Batch #2 decision #17: every Stage starts with a `stage-N-pre`
tag + backup, ends with a `stage-N-done` tag and a tested restore
drill. The drill log lives at the bottom of this file.

---

## Decision tree

```
Did a deploy go bad?
├─ Yes → §1 (Render manual-deploy rollback)
│
Did a migration go bad?
├─ Yes — migration is reversible (no data loss in the down) → §2 (`migrate <app> <prev>`)
├─ Yes — migration is irreversible              → §3 (pre-migration backup + restore)
│
Did the laptop die / age key lost?
├─ Yes → §4 (LastPass key recovery)
│
Did Render itself go down?
├─ Yes → §5 (off-site B2 restore into a fresh Postgres)
│
Did the routine backup quietly stop running?
├─ Yes → §6 (audit-log alarm; investigate)
```

---

## §1 — Bad-deploy rollback (Render manual-deploy)

**When:** the deploy succeeded but the new revision misbehaves
(performance, bug, regression). Database schema is unchanged or
forward-compatible.

**Procedure:**

1. SSH to the droplet:
   ```bash
   ssh root@<droplet-ip>
   cd /home/app/family-books
   ```
2. Check out the previous good commit:
   ```bash
   git fetch
   git checkout <previous-good-sha>
   ```
3. Re-deploy with `SKIP_PULL=1` (don't fast-forward git again):
   ```bash
   SKIP_PULL=1 ./scripts/deploy.sh
   ```
   The script rebuilds + restarts the `web` container; postgres
   and nginx stay up. Traffic recovers in ~30s once the new
   container reports healthy.
4. After traffic recovers, `git revert <bad-sha>` locally on your
   laptop; push; the revert PR + CI green + merge → main keeps the
   repo state matching what's deployed.

**Why the SSH-checkout + git revert combo:** the SSH checkout is
the fast operational lever (production traffic recovers in seconds).
The git revert keeps the repo honest — main always reflects
production. Skipping the git revert leaves the droplet on a detached
HEAD pointing at an old SHA, and the next `./scripts/deploy.sh`
fast-forwards back to the bad code.

**Important:** `git checkout <sha>` on the droplet leaves git in
detached-HEAD state. The next deploy.sh `git pull --ff-only` will
fail because there's no upstream branch. The git revert + merge to
main is what fixes this; once main has the revert, `git checkout main`
on the droplet plus a normal deploy.sh resumes the standard flow.

---

## §2 — Reversible-migration rollback

**When:** a migration is bad but its inverse cleanly undoes its
effects (`migrate <app> <prev_migration>` works without data loss).
This is the easy case.

**Procedure:**

1. SSH to the droplet, `cd /home/app/family-books`.
2. Run the reverse migration inside the web container:
   ```bash
   docker compose -f docker-compose.prod.yml --env-file .env.production \
     run --rm --no-deps web python manage.py migrate <app> <prev>
   ```
3. SSH-checkout back to the pre-migration commit (§1 procedure).
4. Investigate root cause locally.

This works for: adding columns with defaults, adding indexes, adding
nullable fields, adding tables. It does NOT work for: dropping
columns/tables, renaming columns, type changes that lose precision
— all "data destructive" migrations are irreversible at the data
layer even if the schema migration has a `migrations.RunPython`
reverse function. For those, use §3.

---

## §3 — Irreversible-migration rollback

**When:** a migration dropped a column, changed a type narrowingly,
or otherwise destroyed data. Reverting the code is necessary but
not sufficient — the data must be restored.

**Pre-migration discipline (binding rule):** before any migration
that's potentially irreversible, run:

```bash
make backup-then-migrate
```

This is `backup_db` then `migrate` in one command. The deliberate-
ops-action shape from decisions #22/#23 — no automation on `make
migrate` alone; the operator runs the explicit composed command
when the migration's risk profile warrants it.

**Procedure (when the worst happens):** all commands run on the
droplet via SSH, in `/home/app/family-books`.

1. Identify the backup taken pre-migration (must have been from the
   `prod/` prefix, NOT a drill in `dev-test/`):
   ```bash
   docker compose -f docker-compose.prod.yml --env-file .env.production \
     exec -T web python manage.py shell -c \
     "from books.audit.models import AuditLog, AuditAction; \
      r = AuditLog.objects.filter(action=AuditAction.BACKUP_CREATED, \
          reason='pre-migration').order_by('-timestamp').first(); \
      print(r.entity_id if r else 'NO PRE-MIGRATION BACKUP FOUND')"
   ```
   The output is the B2 object key.

2. SSH-checkout back to the pre-migration commit (§1 procedure).

3. Restore the backup over the live database. WARNING: this is
   destructive — it overwrites prod with the backup's state.
   Coordinate with users; expect user-visible downtime ~5 minutes.
   ```bash
   docker compose -f docker-compose.prod.yml --env-file .env.production \
     run --rm --no-deps web python manage.py restore_db \
       <backup-object-key> \
       --into "$DATABASE_URL" \
       --confirm-prod-restore
   ```
   `$DATABASE_URL` is set inside the container by docker-compose
   (constructed from POSTGRES_USER/PASSWORD/DB).

4. Verify with a quick sanity check (open the trial balance, look
   for known values).

5. The bad-migration code is now reverted AND the data is back.

---

## §4 — Laptop died / age key recovery

**When:** the local machine is gone, lost, or wiped. The age private
key file at `~/.config/age/family-books.key` no longer exists.
Without it, B2 backups are encrypted but undecryptable.

**This is the failure mode that justifies LastPass redundancy.**

**Procedure:**

1. On a new machine, install age:
   - Linux: `apt install age`
   - macOS: `brew install age`
   - Windows: `choco install age` or scoop

2. Open LastPass, find the Secure Note titled
   "Family Books — age private key". Copy its contents.

3. Write to disk with restrictive permissions:
   ```bash
   mkdir -p ~/.config/age
   echo '<paste here>' > ~/.config/age/family-books.key
   chmod 600 ~/.config/age/family-books.key
   ```

4. Set `AGE_PRIVATE_KEY_FILE` in your shell:
   ```bash
   export AGE_PRIVATE_KEY_FILE="$HOME/.config/age/family-books.key"
   ```

5. Verify with a no-op decrypt of an existing backup:
   ```bash
   python manage.py shell -c "from books.core.backup.storage import build_default_storage; \
     print(build_default_storage().list_objects()[-1].key)"
   ```
   Then `restore_db <key> --into postgres://...drill...` against a
   scratch DB to confirm decryption works end-to-end.

**Failure modes guarded against:**
- Lose laptop + lose key: mitigated by the LastPass copy.
- Private key in repo: mitigated by `.gitignore` of
  `~/.config/age/`, no CI logs ever printing the key contents,
  and the env-var pattern (the key path is in env, the key
  contents are never in env or build logs).

---

## §5 — Droplet is gone / migrate to a different host

**When:** the DigitalOcean droplet is in a sustained outage, has
been compromised, or DO's pricing/policy changes drive a host
migration. Off-site B2 backups are the recovery story.

**Procedure:**

1. Stand up a fresh host (new DO droplet, AWS EC2, Hetzner, etc.)
   with Docker installed per `docs/DEPLOY.md` § 3.
2. Clone the Family Books repo to `/home/app/family-books` on the
   new host. Copy `.env.production.example` → `.env.production`
   and fill in the values (LastPass + B2 console).
3. Bring the stack up minus the data restore:
   ```bash
   docker compose -f docker-compose.prod.yml --env-file .env.production \
     up -d postgres
   ```
   Wait for healthcheck.
4. Pick the latest production backup from B2 (NOT a drill artifact
   — must be from the `prod/` prefix):
   ```bash
   docker compose -f docker-compose.prod.yml --env-file .env.production \
     run --rm --no-deps web python manage.py shell -c \
     "from books.core.backup.storage import build_default_storage; \
      print(build_default_storage().list_objects()[-1].key)"
   ```
5. Restore into the new Postgres:
   ```bash
   docker compose -f docker-compose.prod.yml --env-file .env.production \
     run --rm --no-deps web python manage.py restore_db \
       <backup-object-key> \
       --into "$DATABASE_URL" \
       --confirm-prod-restore
   ```
6. Bring the rest of the stack up:
   ```bash
   docker compose -f docker-compose.prod.yml --env-file .env.production \
     up -d
   ```
7. Update DNS A record `books.markrowecontest.com` → new host IP.
8. Run certbot on the new host (DEPLOY.md § 6).
9. Verify trial balance ties out.

The drill (§drill log below) exercises steps 4–6 against a local
Postgres on the operator's laptop, minus the new-host provisioning.
New-host provisioning is real-cost work; the drill proves the
B2-restore-into-fresh-postgres part works.

---

## §6 — Routine backup quietly stopped

**When:** the nightly cron job stopped firing or started failing
silently. No `BACKUP_CREATED` audit row appears in the application's
audit log.

**Detection:** the nightly cron writes a `BACKUP_CREATED` row.
A simple query catches gaps:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production \
  exec -T web python manage.py shell -c \
  "from books.audit.models import AuditLog, AuditAction; \
   from datetime import timedelta; from django.utils import timezone; \
   yday = timezone.now() - timedelta(days=1); \
   print('OK' if AuditLog.objects.filter(action=AuditAction.BACKUP_CREATED, \
     timestamp__gte=yday).exists() else 'MISSING')"
```

A Group I polish item is to wire this into a monitoring alert
(email, Slack, etc.). For v1, run the query manually as part of
the quarterly drill cadence.

**Procedure when MISSING:**

1. Check the cron log on the droplet:
   ```bash
   tail -50 /var/log/family-books-backup.log
   ```
   Look for the most recent START / DONE pair, or an error message.
2. Check that the cron entry is still installed:
   ```bash
   crontab -l | grep family-books
   ```
3. Common causes: B2 credentials expired; AGE_RECIPIENT changed;
   pg_dump can't reach the DB (DNS / network change); docker daemon
   died; disk full.
4. Fix root cause; trigger a manual run via the same script:
   ```bash
   /home/app/family-books/scripts/cron-backup.sh
   ```
5. Confirm `BACKUP_CREATED` audit row appears.

---

## §drill log — recorded executions

The H.3 drill (`./manage.py drill_rollback --use-b2 --prefix dev-test/`)
is the binding production-readiness check. Quarterly cadence
(Mark's calendar reminder).

**Prefix split (H.5x decision).** Drills always upload to and read
from the `dev-test/` prefix in B2. Production backups (`prod/`) are
never touched by drill runs. This keeps:

- the `prod/` retention horizon (30 + 12 + 7) clean — drill
  artifacts don't push real backups out of the keep set;
- post-mortem queries unambiguous — anything under `prod/` is real
  history; anything under `dev-test/` is operator-initiated test
  traffic;
- the drill itself honest — it still exercises a real round-trip
  through B2 rather than an in-memory mock, just into a sandboxed
  prefix.

The `--prefix` flag is enforced at the `backup_db` layer, so
`drill_rollback` cannot accidentally write to `prod/` even if the
operator's `.env` points there.

Reference values for the spot-check assertion (refinement #3):
- **Account number:** `1-0179` (BOA - Savings).
- **Expected own_balance:** matches the value in the active
  opening-balance JE for that account at drill time. The drill
  computes this pre-backup and asserts the post-restore value
  equals it. Catches "counts match but values corrupted."

### Initial drill (Group H acceptance)

> _Recorded by Mark when the local drill runs. Update this entry
> in place rather than appending new ones; the most recent drill
> is what's load-bearing._

- **Date:** _(pending — Mark to run after H.6 lands)_
- **Duration:** _(pending)_
- **Outcome:** _(pending)_
- **Source counts:** _(pending — pasted from the
  `BACKUP_DRILL_PASSED` audit row's `after_value.source_counts`)_
- **Spot-check value:** _(pending —
  `after_value.spot_check_value`)_
- **Notes:** _(pending)_

### Quarterly cadence

Calendar reminder set; next drill due _(date)_.
