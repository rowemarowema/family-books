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
8. **`reset_coa` walks the user-account tree by depth.** A bulk
   `Account.objects.filter(is_system=False).delete()` triggers
   `Account.parent_account.on_delete=PROTECT` against the first parent
   row encountered, even when the children are also in the queryset —
   PROTECT doesn't reason about what's also being deleted. Fix:
   compute depth in-memory, group by depth, delete deepest-first inside
   one `transaction.atomic()`. Direct ad-hoc `account.delete()` calls
   keep PROTECT semantics; only the orchestrated batch in `reset_coa`
   walks the tree. Surfaced by Mark's smoke run against the real
   640-account fixture; flat unit-test fixtures didn't exercise the
   parent-child PROTECT interaction. New tests:
   `test_reset_coa_handles_simple_parent_child_tree`,
   `test_reset_coa_against_real_640_account_fixture`,
   `test_full_seed_refuse_reset_seed_cycle_against_real_fixture`,
   `test_reset_coa_preserves_parent_account_protect_for_other_callers`.
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

- **Group F** — opening balances + trial balance UI (engine, HTML,
  CSV/XLSX/PDF exports).
  - `books/accounting/opening_balances.py` — `set_opening_balance()`
    posts a balanced 2-line JE per (account, as_of) with offset to
    system Opening Balance Equity (3-9100). Refusal contract on system
    account / type / zero amount / duplicate. Reverse + re-post
    round-trip via `reverse_opening_balance()`. ADR-005 documents the
    JE shape and the duplicate-check filter.
  - Period-close stub `_check_period_open(as_of)` is a no-op today
    with `# TODO(stage-2)` marker; FiscalPeriod model lands in Stage 2.
    ADR-006 documents the deferred contract.
  - `set_opening_balance` management command — single-account or
    `--csv` bulk modes. CSV columns:
    `account_number,account_path,amount,as_of`. account_number wins
    when both ID columns present; account_path resolves by walking
    parent chain in memory. Refuse-not-partial semantics
    (`transaction.atomic()` over the whole batch).
  - `fixtures/sample_opening_balances.csv` — 6 rows, 3 distinct
    `as_of` dates, 3 Asset / 3 Liability, leaf accounts only, anchored
    to verified `default_coa.json` paths.
  - `books/accounting/reports/trial_balance.py` —
    `compute_trial_balance(as_of, prior_as_of=None, include_zero=False,
    types=None)` returns a hierarchical TrialBalance dataclass.
    Single ORM aggregate per as_of, in-memory tree assembly, sort
    contract `(TYPE_RANK, display_order, name)`, activity-aware
    visibility default.
  - `books/web/views/reports.py` + `books/core/auth.py` —
    `@owner_only_with_2fa` decorator (mirrors admin gate; 403 not
    302), `trial_balance_view()` with format dispatch
    (`html|csv|xlsx|pdf`). Owner-only, 2FA-gated when SystemFlag
    enforcement is on. Named-param 400 messages for bad query
    strings.
  - `templates/base.html` (5-line shell) + `templates/reports/
    trial_balance.html` + `_amount_cell.html` partial. Bootstrap 5
    via CDN. `data-cell` / `data-value` attributes drive the cell-
    level tie-out test.
  - Exporters under `books/accounting/reports/exporters/` — CSV
    (RFC-4180 UTF-8), XLSX (openpyxl, accounting number_format),
    PDF (WeasyPrint, lazy import + 503 fallback). Same engine call,
    three rendering surfaces. Cell-level round-trip property test
    proves HTML/CSV/XLSX agree on every cell vs the engine.
  - `docs/SETUP.md` — Windows GTK install path; Render
    `apt-get install libpango-1.0-0 libpangoft2-1.0-0
    libgdk-pixbuf2.0-0` build command (Mark's strict posture: PDF
    is a runtime feature, GTK installed at build).
  - **Sub-commits (6):**
    - `4893c34` F.1 service + audit migration + 24 tests
    - `36fc22d` F.2 CLI + sample CSV + 16 tests
    - `dded24e` F.3 engine + 19 tests (incl. 5 parametrize)
    - `36c1766` F.4 HTML view + decorator + 21 tests
    - `192aaee` F.5 exporters + view dispatch + 15 tests
    - `7ba09af` F.5-fix template comment leak + regression test
  - **Plus two F.1 follow-ups in the same group:** `9b63303`
    (duplicate-check filter fix for reversal-as-self) and `394bc88`
    (assertion-shape fix in the regression test). Both became
    canonical examples in the class-of-bug catalog.

### Design decisions made during Group F

1. **Service is the only public path.** `set_opening_balance()` is the
   sole opening-balance API. The CLI is a thin shell; the HTML form
   in Stage 2 will be the second consumer. No code path reaches
   `JournalEntry` / `JournalLine` directly; everything goes through
   `post_entry()` (Group D) for posting + immutability + audit.
2. **Reference number convention `OB:<account>:<as_of>`** is the basis
   for the duplicate-check filter and for the F.5 export
   `Content-Disposition` filename. Deterministic per (account, as_of).
3. **Duplicate-check filter has TWO conditions, not one.**
   `reversed_by__isnull=True AND reversing_entry__isnull=True`. The
   first excludes originals that have been reversed; the second
   excludes the reversal itself (which inherits the original's
   `reference_number` per Group D `reverse_entry()`). The F.1 bug-pair
   — too-narrow filter shipped paired with too-narrow assertion —
   produced row #3 in the class-of-bug catalog.
4. **Activity-aware default visibility** in the trial balance:
   include if balance != 0 OR posted activity in the as-of window
   (regardless of `is_active`). Hide only zero-balance + zero-activity.
   `?include_zero=1` shows everything. Matches accountant
   expectations for inactive-but-historically-significant accounts.
5. **Sort contract `(TYPE_RANK, display_order, name)`** is the engine's
   responsibility. Templates and exporters render in the order the
   engine returns. The sort is the basis for both the HTML render
   order and the CSV/XLSX row order — single source of truth.
6. **`@owner_only_with_2fa` decorator returns 403, not 302-to-login.**
   Report URLs aren't part of the auth flow; an unauthenticated
   request is a misuse, not a redirect-worthy event. The login URL
   is `/account/login/` via two_factor's LoginView.
7. **CSV/XLSX drop type subheader rows; HTML keeps them.** Downstream
   consumers (pandas, Excel formulas) need clean tables; the `type`
   column does the grouping. HTML keeps the subheaders for visual
   readability. Q2 refinement during the F.5 breakdown review.
8. **`data-value="<raw decimal>"` is the test contract surface;
   displayed text is for humans.** The cell-level tie-out test reads
   `data-value` directly — never parses the displayed text. This
   means the "negatives in parens" display logic could change without
   breaking the test, and conversely, the test catches any drift
   between engine output and the canonical decimal regardless of how
   it's displayed.
9. **WeasyPrint runtime dep + lazy import + 503 fallback** (defense
   in depth). Strict posture per Mark: PDF is required in production;
   Render apt-installs Pango/Cairo system libs at build. The lazy
   import + typed `PDFRendererUnavailable` exception is the soft
   fallback for Windows local dev without GTK; in production it
   should never fire. Group H gets a forward-pointer for a startup
   check that fails fast at boot if GTK is missing.

### Class-of-bug catalog (running list)

Surfaced across Groups E and F. Documented in
`memory/feedback_assertion_strength.md` for cross-session retention.
Each pattern has a different fix shape; the catalog is what makes
recognition automatic.

| # | Pattern | Where surfaced | Fix shape |
|---|---|---|---|
| 1 | **Comment lied about fixture** | Group E depth-5-claiming-depth-4 | Strengthen fixture to match named claim; add boundary test |
| 2 | **Fixture didn't exercise the path** | Group E reset_coa flat fixture | Add real-fixture-shape test; binding standing rule |
| 3 | **Assertion targeted wrong population** | F.1 reverse-cycle `count()==2` | State-filtered counts + FK-shape assertions; same filter as production |
| 4 | **Mis-aimed test (test correct, target wrong)** | F.5 cell test reading `<td>` while leak was in `<tr>` text nodes | Add a separate targeted test, don't broaden the original |

Patterns 1–3: tests asserted the wrong thing. Pattern 4: test asserted
the right thing but in the wrong DOM region. Different fixes — 1–3
strengthen the original test; 4 adds a sibling test.

### Standing rules (binding for the rest of Stage 1)

After Group F, the standing pre-commit checklist + group-completion
gates are:

1. **Four-step pre-commit verification** — `make check && make migrate
   && python manage.py makemigrations --dry-run && make test`. Each
   step must be clean.
2. **UNVERIFIED list = 0** with site-packages citations for every
   third-party API call.
3. **Defense-in-depth tables** for every major invariant in the
   group breakdown.
4. **Property-based tests** where invariants are universal (tie-out,
   ordering, idempotency, etc.).
5. **Constraint and index drift tests** (`pg_constraint` /
   `pg_indexes` introspection) for any DB-level rule.
6. **Real-fixture smoke** — binding before declaring any group done.
   At least one test loads `fixtures/default_coa.json` and exercises
   the group's main code path against it; manual browser smoke for
   any HTTP-facing change.
7. **Cross-service round-trip tests** for any service that interacts
   with another service's contract (e.g., set_opening_balance →
   post_entry → reverse_entry → re-post is exercised end-to-end).
8. **Class-of-bug catalog updates** — when a regression reveals a
   new pattern, add a row to
   `memory/feedback_assertion_strength.md`.

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
- **Period-close gate activation** deferred to **Stage 2**. The
  `_check_period_open(as_of)` stub in
  `books/accounting/opening_balances.py` is a no-op today; Stage 2's
  `FiscalPeriod` model + period-close API will replace the stub body
  with the real query. Activation site is grep-findable via
  `TODO(stage-2)`. ADR-006 documents the deferred contract.
- **Handsontable grid for opening-balance entry** deferred to
  **Stage 2** (per Q2 in the Group F breakdown). F.2 ships the CLI
  + CSV-bulk path; the on-screen grid is the next polish.
- **Generic template-meta-leak test** deferred to **Group I**. The
  F.5 fix added a targeted regression
  (`test_no_template_comment_leaks_in_rendered_body`) that scans the
  trial-balance page for known-bad substrings. Group I will
  generalize this to a single test that walks all named URLs, GETs
  each as the owner, and asserts no template-syntax tokens (`{#`,
  `{% comment %}`, raw `{% if`, raw `{% for`, etc.) appear in any
  response body. One test, broad coverage.
- **Group H** — backups + restore + rollback drill + WeasyPrint
  startup check + render.yaml validation.
  - `books/core/backup/{retention,storage}.py` — pure-Python retention
    policy (30d + 12mo + 7y per ADR-001) and B2 client wrapper.
  - `backup_db` / `restore_db` / `drill_rollback` management commands.
    Pipeline: pg_dump → age encrypt → B2 upload → retention prune →
    audit row. Restore: B2 download → age decrypt → pg_restore.
    Drill: backup → restore-into-scratch → 7-point verification
    (counts × 5 + system-account presence + BOA spot-check value).
  - 4 new AuditAction members: BACKUP_CREATED, BACKUP_RESTORED,
    BACKUP_DRILL_PASSED, BACKUP_DRILL_FAILED. Audit migration 0004
    extends the choice list. Drift test extends to 19 members.
  - WeasyPrint Django check `books.web.W001` (Warning level) +
    `books/web/apps.py` ready() hook. CI workflow inserts
    `make check-deploy` between `make migrate` and the dry-run, so
    GTK-missing on the runner fails the build.
  - `render.yaml` extended: `age` added to apt-install line; new
    Cron Job service `family-books-nightly-backup` runs `backup_db
    --reason scheduled` at 03:00 UTC daily; `check --deploy --fail-
    level WARNING` added to web service build command.
  - `Makefile` adds `make backup-then-migrate` — pre-migration
    discipline target (deliberate-ops-action shape per #22, #23).
    NOT automatic on `make migrate`.
  - `docs/ROLLBACK.md` (new) — full runbook: bad-deploy via Render
    manual-deploy, reversible vs. irreversible migration rollback,
    LastPass age-key recovery for the dead-laptop case, Render-down
    scenario, missed-backup detection. Drill-log section for
    recording quarterly drill executions.
  - **Sub-commits (6 to date):**
    - `0ce9c05` H.1 — backup_db + retention + storage + audit migration
    - `0d8127e` H.2 — restore_db + safety flags
    - `e7e0429` H.3 — drill_rollback + broadened verification
    - `6f71afc` H.4 — WeasyPrint Django check + CI gating
    - `5256444` H.5a — render.yaml updates (initial; UNVERIFIED:1
      pending Render deploy validation)
    - [H.6 hash] — docs/ROLLBACK.md + summary (this commit)
  - **H.5 sub-series continues** if/when the actual Render deploy
    surfaces schema corrections. Each correction lands as a discrete
    commit citing the Render error message it fixes.

### Design decisions made during Group H

1. **Retention semantics use "top-N within horizon," not calendar
   windows.** A calendar-window framing ("everything in last 365
   days") overcounts at boundaries; "top 12 most-recent
   (year, month) buckets within the 12-month horizon" gives the
   exact spec count (max 49) regardless of where today falls in the
   calendar year. Tested by 19 unit tests with synthetic 10-year
   datasets.
2. **age binary, not Python age library.** The Debian stable `age`
   package is the standard; pyrage on PyPI is less mature. Backup/
   restore commands shell out to `age` via subprocess, similar to
   how they shell out to pg_dump / pg_restore. apt-install in
   render.yaml carries the binary into the production image.
3. **Refuse-not-default on `restore_db --into`.** No default to
   `$DATABASE_URL` (silent prod overwrite would be too easy). The
   --confirm-prod-restore double-flag is required when --into
   resolves to the same (host, port, database) tuple as
   $DATABASE_URL — same shape as `reset_coa --confirm-destroy`.
4. **CI doesn't run the real drill.** No B2 credentials, no
   scratch Postgres in the GitHub Actions runner. CI tests the
   orchestration code paths via mocked B2 + mocked psycopg cursor;
   the real drill is run-once-locally by Mark and recorded in
   `docs/ROLLBACK.md` § drill log. Mark's standing distinction:
   "CI proves the code paths work; the local drill proves the
   operational toolchain works end-to-end."
5. **Drill verification is broadened over the breakdown's
   minimum.** Per refinement #3: counts (×5) + system-account
   presence + spot-check value on `1-0179` BOA Savings. The
   spot-check catches "counts match but values corrupted" — class-
   of-bug pattern #3 (encoded count vs. wrong target population),
   applied here as encoded count vs. data integrity.
6. **WeasyPrint check is a Warning, not an Error, locally.** Local
   `make check` doesn't block on missing GTK (Windows dev
   reality). CI `make check-deploy` (`--fail-level WARNING`) and
   render.yaml's build command (`check --deploy --fail-level
   WARNING`) convert it to a build-failing Error. The runtime 503
   fallback in F.5's PDF view becomes "should never fire" rather
   than just "shouldn't fire."
7. **render.yaml is UNVERIFIED until the actual Render deploy.**
   pythonVersion / postgresMajorVersion / cron schedule keys are
   best-known per Render docs. Group H's H.5 sub-series captures
   any corrections as discrete commits citing specific error
   messages. Once the deploy succeeds, a final commit pins the
   verified-on-date as the constraint baseline.

### Class-of-bug catalog (running list, after Group H)

No new patterns surfaced in Group H. The 4-row catalog from
`memory/feedback_assertion_strength.md` continues to apply; pattern
#3 (encoded count vs. wrong target population) was specifically
guarded against by the broadened drill verification (#5 above).

### Deferred

- **WeasyPrint deploy startup check** deferred to **Group H**. The
  F.5 PDF exporter degrades gracefully (lazy import + typed
  `PDFRendererUnavailable` → 503); Group H adds a startup check that
  exercises `import weasyprint` at boot and fails fast if Render's
  apt-install was silently skipped. Defense in depth on top of the
  runtime fallback. **— RESOLVED in H.4 via `make check-deploy`
  in render.yaml's build command.**
- **Automated quarterly drill** deferred to **Group I or Stage 2**.
  H.3 ships a manual drill (Mark runs `drill_rollback --use-b2`
  quarterly per calendar reminder). Automating it requires a
  scratch Render Postgres + scheduled cron, marginal ROI for a
  single-user system. The high-ROI piece is the daily backup itself
  — a missing `BACKUP_CREATED` audit row catches most failure modes
  silently.
- **Backup-failure monitoring alert** deferred to **Group I**.
  Today's detection is the manual query in `docs/ROLLBACK.md` § 6.
  Group I wires it into an alert (email or Slack) so a missed
  nightly backup pages the operator without manual checking.
- **render.yaml schema corrections (H.5b through H.5N)** pending
  the actual Render deploy. Each correction lands as a discrete
  commit citing the specific error message. Once the deploy
  succeeds, a final commit pins the verified-on-date in
  render.yaml as the constraint baseline for future edits.
- **H.3 drill execution recording** pending Mark's local drill
  run. `docs/ROLLBACK.md` § drill log has the placeholder section
  ready to fill in (date, duration, source counts, spot-check
  value).
- **mypy backlog (typecheck demoted to informational)** deferred to
  **Group I**. Adding mypy as a CI stage in Group G surfaced ~71
  pre-existing missing-annotation findings across Groups D-F. None
  are bugs; they're real annotations that need to land. Group G
  posture: typecheck runs in CI with `continue-on-error: true` so
  the count stays visible without blocking. Group I clears the
  backlog and re-promotes typecheck to gating (`continue-on-error`
  removed). See `docs/CI.md` § "Why typecheck is informational" for
  the rationale.
- **N818 exception-name renames** deferred to **Group I**. Ruff
  wants exception class names to end in `Error`. The codebase has
  `PostedEntryImmutable`, `AlreadyReversed`, `CannotReverseAReversal`
  (predicates rather than nouns), and `OpeningBalanceError` (already
  conformant). A targeted rename pass is a Group I task; for now
  N818 is globally ignored with a `pyproject.toml` comment pointing
  at this entry.
- **DJ001 `SystemFlag.setup_coa_mode` null=True → default=""**
  deferred to **Group I** (or Stage 2 if it lands first). The field
  was added in Group C; switching to a non-null default requires a
  data migration to coerce existing NULL rows. Globally ignored in
  ruff config until then.
- **DJ012 model method ordering** deferred to **Group I**. Django
  Style Guide wants `save` before `clean`; the codebase orders
  `clean` first (validation logically runs before save). Stylistic
  preference, not a bug surface; ignored until a broader Django-
  conformance pass becomes worthwhile.

### Time / date policy (load-bearing for Group D onward)

Timestamps stored UTC, displayed in America/Chicago. Dates are
tz-naive. All `as_of` parameters are naive `date`s; all `posted_at`
and audit `timestamp` fields are tz-aware UTC. See
`docs/TIMEZONE.md` (landing in Group I) for rendering examples.

### Open questions

- None blocking.

### Verification to run before approving Group F

```bash
pip install -e .[dev]                        # picks up beautifulsoup4, pypdf
make check                                   # standing rule, step 1
make migrate                                 # accounting.0003 + audit.0002 + 0003
python manage.py makemigrations --dry-run    # must say "No changes detected"
make test                                    # 254 green + 2 PDF skips on Windows-no-GTK

# Real-fixture browser smoke (binding):
python manage.py bootstrap_owner --email mark@example.com
python manage.py seed_default_coa
python manage.py set_opening_balance --csv fixtures/sample_opening_balances.csv
python manage.py runserver
# Browser, all four formats from /reports/trial-balance/?as_of=2026-04-25
# expect: HTML renders Bootstrap table, "Balanced" badge, $27,350 totals;
# CSV/XLSX/PDF download as attachments with sensible filenames; cells
# match HTML.
```

### Verification to run before approving Group E (historical)

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
following four steps in order, each must be clean, plus a real-fixture
smoke for any group that ships a command-line operation that touches
user data:

```bash
make check
make migrate
python manage.py makemigrations --dry-run    # must report "No changes detected"
make test
# + group-specific smoke against a realistic fixture (see below)
```

`makemigrations --dry-run` was added to the standing rule after the Group
E review (2026-04-24): a green test suite can mask model-vs-migration
drift, because tests run against the migrated DB schema rather than the
schema Django would derive from the current model state. The dry-run
trips whenever the two diverge — wrong index name, mismatched Q-children
ordering inside a CheckConstraint, missing `name=` on `models.Index`,
etc. CI in Group G will run all four steps on every push.

**Real-fixture smoke rule** (added 2026-04-24 after `reset_coa` shipped
with a `Account.parent_account.on_delete=PROTECT` interaction bug that
flat unit-test fixtures didn't exercise): for every command-line
operation that touches user data, at least one test must use a fixture
representative of production data shape — hierarchical where the model
has hierarchy, multi-row where the model has volume, with realistic
relationships. A passing test on a 3-row flat fixture is not proof the
operation works on the 643-row hierarchical default_coa.json. Each
group's verification block runs the smoke commands end-to-end against
the real fixture before the group is declared done.

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
  and documented in `docs/ACCEPTANCE.md`. Verified 2026-04-25.
- **Opening balances + trial balance (Group F): passing.** Service +
  CLI + engine + HTML + CSV/XLSX/PDF. Real-fixture render confirmed
  against `default_coa.json` + the 6-row sample CSV; totals tie out
  at $27,350 each side. Documented in `docs/ACCEPTANCE.md`. Verified
  2026-04-25.
- **Backups + restore + rollback drill (Group H): passing
  (in-code) / pending Mark's local drill execution.** backup_db /
  restore_db / drill_rollback management commands; Render Cron Job
  for nightly backup; `docs/ROLLBACK.md` runbook. Real B2 round-trip
  + actual Render deploy validation are Mark's local + deploy steps
  ahead of the next group.
- **#12 Authentication + session timeout + audit log: partial** — all
  plumbing in place; viewer UI lands in Stage 9. AuditLog vocabulary now
  controlled by `AuditAction` TextChoices.
- Trial-balance tie-out (Group D): **passing** at the engine level via
  the 75-entry hypothesis property test in
  `tests/integration/test_trial_balance_tie_out.py`.
- Section 11 items requiring later-stage features (backup drill, period
  close, lot import, wash-sale, viewer UI) still blocked on Groups F /
  H / Stages 2+.
