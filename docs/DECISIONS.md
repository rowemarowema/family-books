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
| [ADR-005](#adr-005-opening-balance-journal-entry-shape) | Opening-balance journal-entry shape | Stage 1 / Group F |
| [ADR-006](#adr-006-period-close-stub-deferred-to-stage-2) | Period-close stub deferred to Stage 2 | Stage 1 / Group F |

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

---

## ADR-005 — Opening-balance journal-entry shape

**Status:** Accepted (2026-04-25). Group F.1.

Opening balances are entered via a single public service —
`books.accounting.opening_balances.set_opening_balance()` — which posts
one balanced 2-line journal entry per (account, as_of) pair. The
service never bypasses Group D's `post_entry()`; opening balances are
real journal entries with the same immutability and audit semantics as
any other posting.

**JE shape**

For an account with `normal_balance = DEBIT` (Asset, Expense):
- Line 1: target account, `debit_amount = amount`, `credit_amount = 0`
- Line 2: system Opening Balance Equity (`3-9100`),
  `debit_amount = 0`, `credit_amount = amount`

For an account with `normal_balance = CREDIT` (Liability, Equity):
sides flipped — the user's amount lands as a credit on the target,
offset debits Opening Balance Equity.

The user always supplies a positive `amount`; sign is implied by the
target account's normal_balance. Revenue and Expense accounts are
rejected (carry-forward types only — Asset, Liability, Equity).

**Reference number convention**

Every opening JE uses
`reference_number = f"OB:{account.account_number}:{as_of.isoformat()}"`.
Deterministic per (account, as_of). The reference is the basis for the
duplicate-check filter and the F.5 export `Content-Disposition`
filename.

**Duplicate-check filter** (the F.1 bug-pair lesson is canonical here)

The service refuses re-posting at the same (account, as_of) when an
"active" opening JE exists. "Active" is defined as:

```
JournalEntry.objects.filter(
    reference_number=ref,
    reversed_by__isnull=True,        # not yet reversed
    reversing_entry__isnull=True,    # not itself a reversal
).exists()
```

Both conditions are necessary. Group D's `reverse_entry()` copies the
original's `reference_number` onto the reversal, so without the second
condition the reversal would match the filter and block legitimate
re-posting after `reverse_opening_balance()`. The F.1 commit history
documents the bug-pair: an initial too-narrow filter (missing condition
2) shipped paired with a too-narrow assertion in the regression test
(counted total reference_number rows instead of active rows). Both
"read approximately right" at review time; both failed for the same
shape of reason. See `feedback_assertion_strength.md` for the
class-of-bug catalog this generated.

**Test patterns established**

- Refusal contract: each refusal path (system account, type, zero
  amount, duplicate, period-close stub) writes an
  `OPENING_BALANCE_REFUSED` audit row before raising
  `OpeningBalanceError`. The audit trail captures attempted writes,
  not just successful ones.
- FK-shape assertions: tests verify `original.reversed_by.exists()` and
  `reversal.reversing_entry_id == original.pk` directly, instead of
  inferring via row counts.
- Reverse + re-post round-trip: the contract is "idempotent only via
  the explicit reverse + re-post path"; the regression test posts a JE,
  refuses a same-(account, as_of) re-post, reverses, then succeeds at
  re-posting. Asserts each FK link individually.

---

## ADR-006 — Period-close stub deferred to Stage 2

**Status:** Accepted (2026-04-25). Group F.1.

Opening-balance posting must refuse if `as_of` falls inside a closed
fiscal period. **Today, no `FiscalPeriod` model exists.** Stage 2's
period-close work introduces it. F.1 ships the activation site as a
stub so Stage 2 can drop the real check in without introducing a new
call site.

**Activation site**

`books/accounting/opening_balances.py::_check_period_open(as_of)`.
Currently a deliberate no-op with the intended Stage-2 logic
documented in a `# TODO(stage-2)` comment block:

```python
def _check_period_open(as_of: date) -> None:
    # TODO(stage-2): activate this check when books.periods.FiscalPeriod
    # exists. The intended behavior:
    #
    #     from books.periods.models import FiscalPeriod, FiscalPeriodStatus
    #     overlapping_closed = FiscalPeriod.objects.filter(
    #         status=FiscalPeriodStatus.CLOSED,
    #         end_date__gte=as_of,
    #     ).exists()
    #     if overlapping_closed:
    #         raise OpeningBalanceError(
    #             f"Cannot set opening balance as_of {as_of}; one or more "
    #             "closed fiscal periods cover or follow that date. Reopen "
    #             "the period first."
    #         )
    return None
```

Grep `TODO(stage-2)` to find this and any sibling activation sites
when Stage 2 begins.

**Contract** (binding for the Stage-2 implementation)

Refuse with `OpeningBalanceError` if any `FiscalPeriod` with
`status=CLOSED` and `end_date >= as_of` exists. The "cover or follow"
phrasing means: any closed period whose end is at or after `as_of`
blocks an opening-balance posting at `as_of`. Reopening the period via
the period-close API (Stage 2 deliverable) is the unblock path.

**Why a stub instead of skipping the check entirely**

Two reasons:

1. **Discoverability.** Stage 2's period-close work needs to find every
   call site that should consult `FiscalPeriod`. A `TODO(stage-2)`
   marker at the actual gate is more reliable than a search through
   all of `books.accounting`.
2. **Test pinning.** F.1 includes
   `test_period_close_check_is_currently_a_noop` which exercises both
   far-past and far-future `as_of` dates and confirms neither is
   refused today. When Stage 2 activates the gate, that test will
   need to be updated — making the activation a visible diff in test
   code, not a silent behavior change.
