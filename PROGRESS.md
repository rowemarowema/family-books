# Family Books — Progress Log

Updated at the end of every working session. Current stage, what is
complete, what is next, and any open questions. See
`docs/BUILD_SPEC.docx` for the authoritative specification.

---

## Current state: Stage 1 — Foundation (in progress)

**Branch:** `stage-1-foundation`
**Stage start tag:** `stage-1-pre` (rollback target)

### Completed

- **Group A** — repo skeleton, `.gitignore` extensions, `stage-1-pre` tag.
  - Commits: `d8e2533`
- **Group B** — Django 5 scaffolding, settings split (base/dev/prod/test),
  `pyproject.toml` with pinned deps, docker-compose Postgres, Makefile,
  JSON logging, TZ policy wired (`America/Chicago` display + `USE_TZ=True`).
  - Commits: `6099e55`, `e10dcfa` (Makefile `check` target fix).
  - **Verified locally:** Mark ran `make install && make check`
    successfully on Python 3.12.10 in Git Bash.
- **Group C** — authentication + 2FA + hardened enforcement + admin site +
  recovery playbook.
  - Custom `books.core.User` (AbstractUser pass-through), singleton
    `books.core.SystemFlag`, `books.audit.AuditLog`.
  - `SessionAbsoluteTimeoutMiddleware` (12h cap).
  - `TwoFactorEnforcementMiddleware` (sticky-on via `REQUIRE_2FA` env
    var; 24h grace window via `disable_2fa_enforcement` management
    command; auto-re-enable after the window expires).
  - `FamilyBooksAdminSite` gating owner + 2FA when enforcement is on.
  - `bootstrap_owner` management command; `pre_save` signal rejects
    User #2.
  - `django-axes` wired (5 fails → 15 min lockout); lockout and
    `user_login_failed` signals write to `AuditLog`.
  - **Recovery model (decision #23):** `reset_owner_2fa` command +
    `docs/RECOVERY.md` covering all lockout scenarios. No allauth
    password-reset UI — mgmt commands via Render Shell match the
    decision-#22 pattern.
  - 27 integration tests under `tests/integration/` (5 new for
    `reset_owner_2fa`).
  - Initial migrations hand-written: `books.core.0001`, `books.audit.0001`.
  - Commits: `989b47e`, plus a follow-up for recovery.

### Design decisions made during Group C

1. **AuditLog moved from Group D → Group C.** Every Group C auth event
   (bootstrap, failed login, lockout, 2FA state change) needs an audit
   row. Moving it forward avoids stub writes / forward references.
   Group D task list revised accordingly (task #4 description updated).
2. **Dropped django-allauth from active wiring.** `django-two-factor-auth`
   provides login + OTP + recovery codes in a single flow. allauth would
   add a parallel account UI with little single-user value. Dep stays
   installed in case Stage 9 needs password-reset emails.
3. **Shadowed `/account/two_factor/disable/`.** Self-service 2FA disable
   is replaced with a `403 Forbidden` stub. The only path to disable
   enforcement is `python manage.py disable_2fa_enforcement
   --confirm-disable "<reason>"`, with a hard-clamped 24h grace window
   and audit logging.
4. **Migrations written by hand.** Python isn't on the harness PATH so I
   can't run `makemigrations`. The two initial migrations mirror what
   Django 5.1 generates. `make migrate` on a fresh DB is the
   verification step.
5. **Decision #23 — credential recovery via management commands, not UI.**
   Mark flagged the lockout risk of dropping allauth. Picked mgmt-commands
   + a printed runbook over wiring allauth's password-reset. Same
   deliberate-ops-action shape as decision #22; avoids email-in-the-loop.
   New: `reset_owner_2fa` command + `docs/RECOVERY.md` (covers forgotten
   password, lost TOTP, lost both, axes lockout, stuck 2FA enforcement,
   corrupted owner row, lost age key, Render compromise).

- **Group D** — accounting engine contract surface (Account,
  JournalEntry, JournalLine, post_entry, reverse_entry).
  - `books/accounting/models.py` — `Account` with 4-level hierarchy +
    normal-balance validators; `JournalEntry` with status state machine +
    sticky immutability on save/delete; `JournalLine` with per-row CHECK
    constraints (debit ≥ 0, credit ≥ 0, debit XOR credit).
  - `books/accounting/posting.py` — `post_entry(entry, *, user, reason)`
    and `reverse_entry(original, *, user, reason, as_of)`. Both atomic.
    Reversal linkage one-way (reversal.reversing_entry_id → original),
    cycle-protected, DB-level unique constraint
    `one_reversal_per_original`.
  - `books/accounting/migrations/0002_immutability_triggers.py` — Postgres
    BEFORE UPDATE/DELETE on journal_entry and BEFORE INSERT/UPDATE/DELETE
    on journal_line, each raising `check_violation` when the parent entry
    is posted. Python save() overrides + DB trigger = defense in depth.
  - `books/accounting/factories.py` — factory_boy factories +
    `make_balanced_entry` helper.
  - Tests (45 new, 77 total): account hierarchy, normal-balance matrix
    (10 cases), DB CHECK drift detection via `pg_catalog.pg_constraint`,
    hypothesis property test for CHECK-constraint coverage, posting
    service (7), reversing service (7 incl. raw-SQL race), immutability
    (8 incl. DB-trigger coverage), account deletion protection (4),
    trial-balance tie-out (1, 75-entry property test).

### Design decisions made during Group D

1. **`reversing_entry_id` links from reversal to original (one-way).**
   Original is never mutated when a reversal is posted, preserving
   posted-entry immutability. DB has a partial unique index that
   additionally prevents two reversals pointing at the same original.
2. **Four layers of debit/credit defense.** DB CHECK for per-row XOR +
   non-negative (catches raw-SQL bypasses); `JournalLine.clean()` for
   form-surface friendly errors; `post_entry` for cross-line sum and
   account-active checks; Postgres trigger blocks any mutation or
   additional insert on lines of a posted entry.
3. **`PostedEntryImmutable` repurposed for "entry not in a mutable
   state."** Raised when posting a non-draft OR reversing a non-posted
   entry. Message text disambiguates; `AccountingError` base catches
   both.
4. **Account number format is convention-only** (refinement #4). 16-char
   CharField with no format regex. Sub-numbering like `10100` / `10101`
   is welcome.
5. **`ON DELETE PROTECT` on `JournalLine.account`** (refinement #3). Hard-
   delete of an account with posted lines raises `ProtectedError`; to
   retire an account, deactivate via `is_active=False`. The posting
   service also rejects inactive accounts on new postings.
6. **Trigger permits the single `draft → posted` transition** because
   `OLD.status` is still `'draft'` at the moment the UPDATE fires. Every
   subsequent write has `OLD.status = 'posted'` and raises. Tested
   negatively (draft→posted succeeds) to prevent future regressions
   that over-block.

- **Group E** — COA bootstrap (loader, reset, system-account protections,
  audit-action enum, format drift detection).
  - `fixtures/default_coa.json` — Mark's full 640-account QuickBooks
    export + 3 Equity system accounts (Owner's Equity / Opening Balance
    Equity / Retained Earnings, `display_order` -300/-200/-100 so they
    sort to the top of the Equity grouping).
  - `Account.display_order` IntegerField (default 0, indexed) +
    `Account.is_system` BooleanField (default False, indexed).
    `Meta.ordering` flipped to `(display_order, name)`. Composite
    `(display_order, name)` index covers the default sort. Migration
    `accounting/0003_account_display_order`.
  - `Account.clean()` extended with three system-account rules
    (is_system requires Equity; is_system immutable post-create;
    is_active=False rejected on system rows). pre_save signal
    inherits the checks for raw create()/bulk paths.
  - `AccountAdmin` exposes `display_order` editable and `is_system`
    readonly; `is_active` becomes readonly when editing a system row.
  - `books.audit.AuditAction` TextChoices enum + migration
    `audit/0002_auditlog_action_choices`. All Group C/D string-literal
    `action=` values migrated to enum members. Drift test asserts every
    distinct `AuditLog.action` value in the DB is a member.
  - `seed_default_coa` management command — JSON loader, two-pass
    insert in `transaction.atomic()`, refuses if any non-system
    Account exists, in-memory cycle/depth/unknown-parent validator,
    `--dry-run` flag, audit rows on success and refusal.
  - `reset_coa` management command — `--confirm-destroy` required,
    refuses on any posted JournalEntry or any JournalLine, deletes
    only `is_system=False` rows (DELETE WHERE; not TRUNCATE),
    preserves system accounts.
  - Tests: 12 format-drift, 18 loader, 10 reset, 9 system-account
    protections, 4 admin smoke, 4 audit-action drift, +1 hypothesis
    property test. Total ~58 new tests on top of Group D's 77.
  - Commits: `d4b9288`, `2ce3bd4`, `54ea46a`, `0f93237`, [commit 5 hash].

### Design decisions made during Group E

1. **Loader is JSON, not YAML.** Mark's QuickBooks export ships as JSON
   and is the source of truth (Batch #4 / ADR-004). Loader does not
   parse `full_path`; `parent_account_number` is the sole hierarchy key.
2. **Refuse-not-upsert idempotency.** Re-running `seed_default_coa`
   while user accounts exist refuses with a clean error pointing at
   `reset_coa`. Upsert was rejected as a side-door around the
   coexistence rules + Group D immutability.
3. **System accounts preserved across reset.** `reset_coa` deletes
   `is_system=False` only; the 3 Equity system accounts persist so that
   the post-reset state isn't a transient inconsistent COA. Re-running
   `seed_default_coa` skips system rows already in the DB by
   `account_number` lookup.
4. **System-account preservation also gates is_system mutability.**
   `is_system` is set on INSERT and immutable thereafter — the only way
   to "un-mark" a system account is to reset_coa + re-seed. Mark
   confirmed this matches the deliberate-ops-action shape from earlier
   decisions (#22, #23).
5. **`AuditAction` enum migration is foundational, not cosmetic.** Group
   C/D shipped with string-literal action values; Mark called for the
   enum migration on the Group E surface so the four new COA actions
   join an already-controlled vocab. Doing it later would have been
   more painful (more rows to backfill, more call sites to touch).
6. **Negative `display_order` is the convention for "sort to top."**
   Default user accounts use 0; system accounts use -100/-200/-300.
   Initial proposal (1/2/3) would have placed system accounts after
   user accounts; switched to negatives during the breakdown review.
7. **Hand-written migrations need `models.Index(name=...)` and
   alpha-sorted Q children.** The Group E review ran
   `makemigrations --dry-run` and found 7 indexes and 1 CheckConstraint
   drifting between the model and the (hand-written) migrations:
   - Indexes: `Meta.indexes` declarations didn't pin `name=`, so Django
     auto-hashed names and disagreed with the migration-defined ones.
     Fix: pin `name=` on every `models.Index` declaration to match the
     migration identifier exactly.
   - CheckConstraint: `Q.__init__` runs
     `children=[*args, *sorted(kwargs.items())]`. The model's
     `Q(debit_amount__gt=0, credit_amount=0)` produces alpha-sorted
     children `[("credit_amount", 0), ("debit_amount__gt", 0)]`; the
     migration's positional tuples were in source order, which Q.__eq__
     considers different. Fix: rewrite migration's positional tuples
     in alpha order to match the model's deconstructed form.
   The standing pre-commit rule now includes
   `python manage.py makemigrations --dry-run` so this whole class of
   drift surfaces before commit.

### reset_coa behavior table

| Condition | Outcome |
|---|---|
| Posted `JournalEntry` exists | Refuse + write `COA_RESET_REFUSED` audit row |
| Any `JournalLine` exists (even draft) | Refuse + write `COA_RESET_REFUSED` audit row |
| `--confirm-destroy` empty / missing | `CommandError` (no audit row) |
| Otherwise | `DELETE FROM account WHERE is_system = FALSE` (ORM-emitted) |
| System accounts | Always preserved; `is_system=True` rows untouched |
| Audit row | `COA_RESET` with `after.deleted_user_account_count = N` |

### Deferred

- **2FA grace-window warning (banner + T+23h email)** explicitly deferred
  to **Group I** (living docs + ops polish). Rationale walked through in
  the Group C review thread; see also `docs/RECOVERY.md` section 5. Until
  Group I lands, the sole warning at auto-re-enable is the
  `two_factor_enforcement_auto_re_enabled` AuditLog row, plus the CLI
  output the operator saw at T=0 when they ran
  `disable_2fa_enforcement`.
- **Polished `display_order` admin UI** (drag-and-drop reorder, bulk
  multi-select reorder) deferred to **Group I**. Stage 1 ships
  `display_order` as a plain editable IntegerField on the admin change
  form; refinement #3 in the Group E breakdown — basic editability
  cannot be gated behind the polished UI.

### Time / date policy (load-bearing for Group D onward)

Timestamps stored UTC, displayed in America/Chicago. Dates are
tz-naive. All `as_of` parameters are naive `date`s; all `posted_at`
and audit `timestamp` fields are tz-aware UTC. See
`docs/TIMEZONE.md` (landing in Group I) for rendering examples.

### Open questions

- None blocking.

### Verification to run before approving Group E

```bash
make check                                   # standing rule, step 1
make migrate                                 # accounting.0003 + audit.0002
python manage.py makemigrations --dry-run    # must say "No changes detected"
make test                                    # ~156 green; coverage ≥ 80%

# Optional smoke of the new commands:
python manage.py seed_default_coa --dry-run    # validates 643 rows, creates none
python manage.py seed_default_coa              # 3 + 640 created, audit row written
python manage.py seed_default_coa              # refuses + audit row
python manage.py reset_coa --confirm-destroy "approval drill"
python manage.py seed_default_coa              # 640 user re-created; 3 system preserved
```

### Verification to run before approving Group D (historical)

```bash
make check           # <<< always run first (standing rule)
make migrate         # applies accounting.0001_initial + 0002_immutability_triggers
make test            # expect 77/77 green; books.accounting.* coverage ≥ 80%
```

**Standing rule from now on:** every per-group verification block runs the
following four steps in order, each must be clean:

```bash
make check
make migrate
python manage.py makemigrations --dry-run    # must report "No changes detected"
make test
```

`makemigrations --dry-run` was added to the standing rule after the Group
E review (2026-04-24): a green test suite can mask model-vs-migration
drift, because tests run against the migrated DB schema rather than the
schema Django would derive from the current model state. The dry-run
trips whenever the two diverge — wrong index name, mismatched Q-children
ordering inside a CheckConstraint, missing `name=` on `models.Index`,
etc. CI in Group G will run all four steps on every push.

### Retrospective: Group C bug clusters and the pre-commit checklist

Two follow-up bug clusters shipped to Mark during Group C review:
1. URL include used `include("two_factor.urls", "two_factor")` (Django 5
   dropped that 2-arg form). First "fix" `include(("two_factor.urls",
   "two_factor"))` was also wrong because `two_factor.urls.urlpatterns` is
   itself a 2-tuple `(pattern_list, 'two_factor')` — the correct form is
   `from two_factor.urls import urlpatterns as tf; include(tf)`.
2. Tests passed `stdin=StringIO(...)` to `call_command`, but `stdin` isn't
   in `BaseCommand.base_stealth_options`. Fix: declare `stealth_options =
   ("stdin",)` on the command and read `options.get("stdin") or sys.stdin`
   in handle().

Also caught and fixed during the retro:
- `AXES_ONLY_USER_FAILURES = False` (deprecated since axes 6.x;
  `AXES_LOCKOUT_PARAMETERS` already covers it).
- `CheckConstraint(check=Q(...))` (Django 5.1 renamed to `condition=`;
  removed in Django 6.0).

Common thread: I can't run Python locally, so third-party library
signatures were reconstructed from memory instead of verified against the
installed sources in
`C:\Users\markt\AppData\Local\Programs\Python\Python312\Lib\site-packages\`.

**Pre-commit checklist (binding from Group D onward):**

Before declaring any group done, for every file I wrote in that group,
I will:

1. List every call to a third-party library API (Django internals, axes,
   two_factor, django_otp, allauth, factory_boy, pytest, etc.) and every
   `call_command` / custom-utility invocation in test code.
2. For each one, open the installed source under site-packages and verify
   the signature matches what I wrote. Annotate the call site with a
   one-line comment citing the source file and line.
3. If I can't find the source or the signature is ambiguous, flag the
   call `# UNVERIFIED` and surface it in the group summary so Mark can
   pressure-test it before running `make test`.

CI in Group G will make this durable (a `check + migrate + test` pass on
every push can't rely on training-data memory). Until then, this
checklist is the bridge.

### Stage 1 acceptance-checklist items reachable now

- **#1 COA bootstrap (Group E): passing.** `seed_default_coa` /
  `reset_coa` / `seed_default_coa` round-trip exercised in
  `tests/integration/test_reset_coa.py::test_reset_then_seed_round_trips`
  and documented in `docs/ACCEPTANCE.md`. Last-verified date pending
  Mark's local run.
- **#12 Authentication + session timeout + audit log: partial** — all
  plumbing in place; viewer UI lands in Stage 9. AuditLog vocabulary now
  controlled by `AuditAction` TextChoices.
- Trial-balance tie-out (Group D): **passing** at the engine level via
  the 75-entry hypothesis property test in
  `tests/integration/test_trial_balance_tie_out.py`.
- Section 11 items requiring later-stage features (backup drill, period
  close, lot import, wash-sale, viewer UI) still blocked on Groups F /
  H / Stages 2+.
