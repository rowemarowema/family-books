# Family Books — Acceptance Demo Script

Living document per Batch #2 decision #18: the BUILD_SPEC §11 acceptance
items live here from Stage 1, not assembled at Stage 9. Each item lists:

- **Status** — `passing`, `partial`, or `not yet`.
- **Commands** — exact shell invocations to reproduce.
- **Expected output** — what success looks like.
- **Last verified** — date Mark ran it locally.

Items are added incrementally as each Stage 1 group lands. Stage 1 spans
Groups A–I; the table below grows over time.

---

## §11 acceptance items reachable in Stage 1

### #1 — COA bootstrap (Group E)

**Status:** passing.

**Commands:**

```bash
make migrate
python manage.py seed_default_coa --dry-run
python manage.py seed_default_coa
python manage.py seed_default_coa            # second run refuses
python manage.py reset_coa --confirm-destroy "acceptance demo"
python manage.py seed_default_coa            # re-seeds 640 user; preserves 3 system
```

**Expected output:**

```
DRY RUN: validated 3 system + 640 user accounts from default_coa.json. No rows created.
Seeded 3 system + 640 user accounts (NNN parent links) from default_coa.json.
CommandError: 640 non-system Account row(s) already exist. To re-seed, run `reset_coa --confirm-destroy "<reason>"` first; system accounts are preserved.
COA reset: deleted 640 user account(s); preserved 3 system account(s). Run `seed_default_coa` to re-seed.
Seeded 0 system + 640 user accounts (NNN parent links) from default_coa.json.
```

(`NNN` is the count of accounts that have a parent link in the fixture.)

**Audit trail:** `COA_SEEDED`, `COA_SEED_REFUSED`, `COA_RESET`,
`COA_SEEDED` — four rows in the `audit_log` table after the sequence
above.

**Format:** account numbers follow `{type-prefix}-{4-digit sequence}`
(per ADR-004). System accounts: `3-9000`, `3-9100`, `3-9200`. User
account numbers range from `1-0001` to `5-0164` and beyond.

**Last verified:** _(pending — Mark to run after Group E approval)_

---

### #12 — Authentication + session timeout + audit log (Group C)

**Status:** partial. All plumbing in place; viewer UI lands in Stage 9.

**Commands (auth setup):**

```bash
python manage.py bootstrap_owner --email mark@example.com
# (prompts for password; or pipe via --password-stdin)
python manage.py runserver
# Browse to /admin/ — owner login flow with 2FA enrollment.
```

**Audit verification:**

```bash
python manage.py shell -c "from books.audit.models import AuditLog; \
  print(list(AuditLog.objects.values_list('action', 'entity_type', 'reason')[:20]))"
```

Expected: rows with actions in `{bootstrap_owner, auth_login_failed,
auth_lockout, two_factor_enforcement_*, reset_2fa_devices}`.

**Recovery drill:** see `docs/RECOVERY.md` §1–§7.

**Last verified:** Mark ran `make install && make check` successfully on
Python 3.12.10 in Git Bash (Group B verification) — partial.

---

### Trial-balance tie-out (Group D)

**Status:** passing (engine-level; UI lands in Stage 2+).

**Commands:**

```bash
make test  # books.accounting.* coverage ≥ 80%; trial-balance property test green
```

**Expected output:** `tests/integration/test_trial_balance_tie_out.py`
runs a 75-entry hypothesis property test that asserts
`SUM(debit_amount) == SUM(credit_amount)` after every posted entry.

**Last verified:** 2026-04-24 (Group D commit `21bec9c`).

---

## Items not yet reachable

The remaining BUILD_SPEC §11 items depend on later stages and are
listed here as forward-references so reviewers can see the whole
acceptance surface at a glance:

- Backup / restore drill — Group H.
- Period close / reopen — Stage 2+.
- Investment lot import — Stage 7.
- Wash-sale detector — Stage 8.
- Acceptance dashboard / viewer UI — Stage 9.
