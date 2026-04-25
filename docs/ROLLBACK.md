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

1. Render dashboard → service → Manual Deploy → pick the prior
   commit SHA (the one that was green before this deploy).
2. Render rebuilds + restarts. ~3 minutes.
3. After traffic recovers, `git revert <bad-sha>` locally; push;
   the revert PR + CI green + merge → main → Render re-deploys
   the reverted code. Now git state matches what's deployed.

**Why the manual revert + git revert combo:** the manual deploy is
the fast operational lever (production traffic recovers in minutes).
The git revert keeps the repo honest — main always reflects
production. Skipping the git revert leaves a "phantom" commit
appearing to be live in main but not actually serving traffic, and
the next merge to main re-deploys the bad code.

---

## §2 — Reversible-migration rollback

**When:** a migration is bad but its inverse cleanly undoes its
effects (`migrate <app> <prev_migration>` works without data loss).
This is the easy case.

**Procedure:**

1. Render dashboard → Shell → `python manage.py migrate <app> <prev>`.
2. Manual-deploy back to the pre-migration commit (§1 procedure).
3. Investigate root cause locally.

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

**Procedure (when the worst happens):**

1. Identify the backup taken pre-migration:
   ```bash
   python manage.py shell -c "from books.audit.models import AuditLog, AuditAction; \
     print(AuditLog.objects.filter(action=AuditAction.BACKUP_CREATED, \
     reason='pre-migration').order_by('-timestamp').first().entity_id)"
   ```
   The output is the B2 object key.

2. Manual-deploy back to the pre-migration commit (§1 procedure).

3. Restore the backup over the live database. WARNING: this is
   destructive — it overwrites prod with the backup's state.
   Coordinate with users; expect user-visible downtime ~5 minutes.
   ```bash
   python manage.py restore_db <backup-object-key> \
       --into "$DATABASE_URL" \
       --confirm-prod-restore
   ```

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

## §5 — Render is down / migrate to a different host

**When:** Render itself is in a sustained outage, OR Render's
business changes in a way that requires migrating off. Off-site B2
backups are the recovery story.

**Procedure:**

1. Stand up a Postgres instance somewhere reachable
   (AWS RDS, Hetzner, local Docker, etc.).
2. Pick the latest backup from B2:
   ```bash
   python manage.py shell -c "from books.core.backup.storage import build_default_storage; \
     print(build_default_storage().list_objects()[-1].key)"
   ```
3. Restore into the new Postgres:
   ```bash
   python manage.py restore_db <backup-object-key> \
       --into "postgres://user:pw@new-host:5432/family_books"
   ```
4. Point the application at the new Postgres (env var swap).
5. Verify trial balance ties out.

The drill (§drill log below) exercises the steps minus the new-host
Postgres provisioning. New-host provisioning is a Stage 2+ runbook
addition if/when migration becomes a real concern.

---

## §6 — Routine backup quietly stopped

**When:** the nightly Cron Job stopped firing or started failing
silently. Render's UI shows successful runs but no
`BACKUP_CREATED` audit row appears in the application's audit log.

**Detection:** the nightly cron writes a `BACKUP_CREATED` row.
A simple query catches gaps:

```bash
python manage.py shell -c "from books.audit.models import AuditLog, AuditAction; \
  from datetime import timedelta; from django.utils import timezone; \
  yday = timezone.now() - timedelta(days=1); \
  print('OK' if AuditLog.objects.filter(action=AuditAction.BACKUP_CREATED, \
    timestamp__gte=yday).exists() else 'MISSING')"
```

A Group I polish item is to wire this into a monitoring alert
(email, Slack, etc.). For v1, run the query manually as part of
the quarterly drill cadence.

**Procedure when MISSING:**

1. Check Render Cron Job dashboard — failed runs visible there.
2. Look at the failed run's logs. Common causes: B2 credentials
   expired; AGE_RECIPIENT changed; pg_dump can't reach the DB
   (network policy change).
3. Fix root cause; trigger a manual run via Render dashboard.
4. Confirm `BACKUP_CREATED` audit row appears.

---

## §drill log — recorded executions

The H.3 drill (`./manage.py drill_rollback --use-b2`) is the binding
production-readiness check. Quarterly cadence (Mark's calendar
reminder).

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
