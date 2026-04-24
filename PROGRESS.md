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

### Up next

- **Group D** — core data model (Account, JournalEntry, JournalLine) and
  posting service. AuditLog already landed in Group C, so D shrinks to
  7 tasks.

### Open questions

- None blocking. Mark's Group-C review is next per the agreed cadence.

### Verification to run before approving Group C

```bash
make install         # picks up formtools (added this group)
make migrate         # applies core + audit initial migrations
make test            # runs the 27 integration tests
```

### Stage 1 acceptance-checklist items reachable now

- #12 Authentication + session timeout + audit log: **partial** — all
  plumbing in place; viewer UI lands in Stage 9.
- Section 11 items requiring financial data (trial balance, backup
  drill, etc.) still blocked on Groups F / H.
