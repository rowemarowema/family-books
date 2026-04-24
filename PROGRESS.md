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

### Deferred

- **2FA grace-window warning (banner + T+23h email)** explicitly deferred
  to **Group I** (living docs + ops polish). Rationale walked through in
  the Group C review thread; see also `docs/RECOVERY.md` section 5. Until
  Group I lands, the sole warning at auto-re-enable is the
  `two_factor_enforcement_auto_re_enabled` AuditLog row, plus the CLI
  output the operator saw at T=0 when they ran
  `disable_2fa_enforcement`.

### Time / date policy (load-bearing for Group D onward)

Timestamps stored UTC, displayed in America/Chicago. Dates are
tz-naive. All `as_of` parameters are naive `date`s; all `posted_at`
and audit `timestamp` fields are tz-aware UTC. See
`docs/TIMEZONE.md` (landing in Group I) for rendering examples.

### Open questions

- None blocking.

### Verification to run before approving Group D

```bash
make check           # <<< always run first (standing rule)
make migrate         # applies accounting.0001_initial + 0002_immutability_triggers
make test            # expect 77/77 green; books.accounting.* coverage ≥ 80%
```

**Standing rule from now on:** `make check` comes before `make migrate` and
`make test` in every per-group verification block. CI in Group G will make
this durable by running `check` on every push.

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

- #12 Authentication + session timeout + audit log: **partial** — all
  plumbing in place; viewer UI lands in Stage 9.
- Section 11 items requiring financial data (trial balance, backup
  drill, etc.) still blocked on Groups F / H.
