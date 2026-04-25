# Family Books — Decisions Log

Living architectural-decision-record (ADR) log per Batch #2 decision #15.
Started in Stage 1; maintained every stage. Each entry is binding once
recorded; deviations require a new entry that supersedes the prior one.

For the granular conversation-batch history (12 BUILD_SPEC ambiguity
answers + Stage-1 clarifications + Group E loader amendments), see the
auto-memory file `project_decisions.md` — this document is the
checked-into-the-repo public version.

---

## Index

| ADR | Title | Stage / Group |
|-----|-------|---------------|
| [ADR-001](#adr-001-build-spec-ambiguity-resolutions-batch-1) | BUILD_SPEC ambiguity resolutions (Batch #1) | Stage 1 / pre-A |
| [ADR-002](#adr-002-stage-1-clarifications-batch-2) | Stage 1 clarifications (Batch #2) | Stage 1 / pre-A |
| [ADR-003](#adr-003-handsontable-ce-and-recovery-via-management-commands-batch-3) | Handsontable-CE + recovery via mgmt commands (Batch #3) | Stage 1 / Group C |
| [ADR-004](#adr-004-coa-loader-format-display_order-system-account-rules-batch-4) | COA loader format, `display_order`, system-account rules (Batch #4) | Stage 1 / Group E |

---

## ADR-001 — BUILD_SPEC ambiguity resolutions (Batch #1)

**Status:** Accepted (2026-04-24).

The build specification leaves several v1 questions open. Mark's answers
on the 12 most load-bearing ambiguities:

1. **Period close granularity** — Monthly.
2. **COA numbering at setup** — System-imposed default. Type prefixes:
   1xxx Asset, 2xxx Liability, 3xxx Equity, 4xxx Revenue, 5xxx–9xxx Expense.
   *(Refined by ADR-004: actual format is `{prefix}-{4-digit}`.)*
3. **Backup retention** — 30 daily + 12 monthly + 7 annual, encrypted with
   `age`, on Backblaze B2.
4. **Session idle timeout** — 2 hours idle, 12 hours absolute cap.
5. **Contest tagging** — No tags pre-seeded for contest activity. Mark
   waived the spec's tag-segmentation suggestion.
6. **Wash-sale detection scope** — Cross-account (all brokerage / retirement
   accounts span a given security).
7. **Opening investment lots** — Per-lot import is the primary shape.
8. **Attachment storage** — Backblaze B2.
9. **Transactional email** — Resend.
10. **2FA enforcement** — Plumbing in Stage 1, enforcement gated by
    `REQUIRE_2FA` env var until prod data lands.
11. **Historical data volume** — 25 years, ~25,000 transactions; drives
    the import perf bar (<10 min for 25k rows).
12. **Reconciliation for `other` FinancialAccounts** — Beginning/ending
    balance flow, same as checking/credit card.

**Why this matters:** these are hard requirements; the spec's softer
language on these 12 points is overridden.

---

## ADR-002 — Stage 1 clarifications (Batch #2)

**Status:** Accepted (2026-04-24).

Ten more decisions made before Group A started, locking the Stage 1
shape:

13. **Price providers** — Primary: yfinance (batched). Secondary: FMP free
    tier. Tertiary: manual override. Alpha Vantage rejected. Accounting
    engine never blocks on third-party APIs.
14. **COA setup flow** — Three-path wizard: (a) load default COA, (b)
    import user's COA (descriptions only; system auto-numbers), (c) start
    empty. Stage 1 ships path (a) via management command; the wizard UI
    lands in Group F+.
15. **`docs/DECISIONS.md`** — Living ADR log. *(This file.)*
16. **`docs/SECURITY.md`** — STRIDE-structured threat model, started in
    Stage 1.
17. **`docs/ROLLBACK.md`** — Tested rollback path per stage. `stage-N-pre`
    tag + DB backup at stage start; `stage-N-done` tag + restore drill at
    stage end.
18. **`docs/ACCEPTANCE.md`** — Section-11 demo script as a living
    document, not assembled at Stage 9.
19. **Django admin** — Enabled, restricted to the sole owner, 2FA-gated
    once `REQUIRE_2FA=true`.
20. **Time-zone policy** — UTC stored; America/Chicago displayed; naive
    `DateField` for transaction-date / `entry_date` / `posting_date`.
21. **Grid library** — Handsontable CE. Tabulator (MIT) recommendation
    declined; commercial-license risk noted in DECISIONS so it stays
    discoverable.
22. **`REQUIRE_2FA` un-flip hardening** — Once on in prod, only
    `disable_2fa_enforcement --confirm-disable` can disable it for a
    24-hour grace window. Auto-re-enable after grace expires.
23. **Credential recovery model** — Management commands only (no
    self-service password-reset UI). Full playbook in
    `docs/RECOVERY.md`.

---

## ADR-003 — Handsontable-CE + recovery via management commands (Batch #3)

**Status:** Accepted (2026-04-24).

Two reaffirmations after the Group C bug-cluster review:

- **Handsontable-CE is the chosen grid library** even though the contest
  operation (markrowecontest.com) is commercial and the CE license is
  non-commercial. Mark accepted the risk; switching to Tabulator was
  declined. README + this log mention the decision so it stays visible.
- **Credential recovery stays in management commands.** Render Shell is
  the root of trust (already gated by Render's own 2FA). No allauth
  password-reset UI; recovery is `changepassword` + `reset_owner_2fa` +
  `axes_reset_username` + `disable_2fa_enforcement`.

---

## ADR-004 — COA loader format, `display_order`, system-account rules (Batch #4)

**Status:** Accepted (2026-04-24).

Mark replaced the 70-account starter set with his full 640-account
QuickBooks export. The export reshapes several earlier assumptions:

24. **Default COA source** — `fixtures/default_coa.json` (640 user + 3
    system accounts). Loader is JSON, not YAML. Fixture is the source of
    truth and is checked into the repo.
25. **Account-number format** — `{type-prefix}-{4-digit sequence}`,
    e.g., `1-0001` (first Asset), `5-0164` (an Expense), `3-9000`–
    `3-9200` (system Equity). Format is convention-only at the model
    (no regex validator); enforced by the seed file plus a
    format-drift test.
26. **`display_order` field on Account** — `IntegerField(default=0,
    db_index=True)`. `Account.Meta.ordering` is `(display_order, name)`.
    Composite `(display_order, name)` index covers the default sort.
    Editable in the basic Django admin from Stage 1; polished UI
    (drag-and-drop, bulk reorder) deferred to Group I.
27. **Default sort scope** — Within any grouping (type, parent),
    members sort by `(display_order ASC, name ASC)`. Not a global
    flatten across all accounts.
28. **System accounts sort to top** — The 3 `is_system=True` accounts
    use **negative** `display_order` (-300, -200, -100 for Owner's
    Equity, Opening Balance Equity, Retained Earnings respectively) so
    they sort above all user accounts within the Equity grouping.
29. **Loader idempotency: refuse, not upsert** — `seed_default_coa`
    refuses on a populated COA (any non-system Account row exists),
    pointing at `reset_coa --confirm-destroy "<reason>"`. `reset_coa`
    itself refuses if any posted JournalEntry or any JournalLine
    exists. **Why:** upsert is a side-door around the COA coexistence
    rules + Group D immutability.
30. **Hierarchy resolution** — `parent_account_number` is the sole
    hierarchy key. Loader does **not** parse `full_path` (the
    colon-separated string is human-readable provenance only).

**Reset preserves system accounts.** `reset_coa` deletes only
`is_system=False` rows. System accounts are infrastructure for the
accounting engine, not user data; preserving them avoids a transient
inconsistent state where opening-balance journals would have nowhere to
point. After a reset, `seed_default_coa` is safe to re-run; it detects
existing system rows by `account_number` and skips them.

**Audit-action vocabulary controlled.** Group E commit 1 added an
`AuditAction` TextChoices enum in `books.audit` and migrated the existing
Group C / Group D string-literal `action=` values onto it. A drift test
asserts every distinct `AuditLog.action` value in the DB is a member of
the enum.
