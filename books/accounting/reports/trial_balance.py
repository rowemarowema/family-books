"""
Trial-balance engine. Returns a hierarchical TrialBalance dataclass
that views and exports render. The engine is the single source of
truth for the numbers; everything downstream is rendering.

Sort contract (ADR-004 / Group F refinement #3):
    Within any grouping (type, parent), rows are sorted by
    (display_order ASC, name ASC). The 5 account types are themselves
    sorted in the conventional financial-statement order: Asset,
    Liability, Equity, Revenue, Expense.

Activity-aware default (Group F Q6):
    Rows are included if the account has a non-zero balance OR any
    posted JournalLine in the as-of window. Truly empty inactive
    accounts are hidden by default. Pass include_zero=True to show
    everything.

Two ORM passes max:
    One aggregate query per as_of (the optional prior_as_of triggers
    a second). Tree assembly is in-memory from the cached account
    metadata. No N+1.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.db.models import DecimalField, Sum, Value
from django.db.models.functions import Coalesce

from books.accounting.models import (
    Account,
    AccountType,
    JournalEntryStatus,
    JournalLine,
    NormalBalance,
)

ZERO = Decimal("0.00")

# Conventional financial-statement order. Used for outer ordering of
# groups in the rendered report; accounts within each group sort by
# (display_order, name).
TYPE_ORDER: tuple[str, ...] = (
    AccountType.ASSET.value,
    AccountType.LIABILITY.value,
    AccountType.EQUITY.value,
    AccountType.REVENUE.value,
    AccountType.EXPENSE.value,
)
TYPE_RANK = {t: i for i, t in enumerate(TYPE_ORDER)}


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class TrialBalanceRow:
    """One row of the trial balance — an account plus its computed
    balance (own + rollup), optional prior-period values, and child
    rows (for hierarchical rendering).

    own_balance is the per-account net (debit-positive for debit-normal
    accounts, credit-positive for credit-normal). rollup_balance
    includes self + all descendants.

    For the comparative column, prior_own_balance / prior_rollup_balance
    are populated when the caller supplies prior_as_of; otherwise None.
    """

    account: Account
    debits_total: Decimal
    credits_total: Decimal
    own_balance: Decimal
    rollup_balance: Decimal
    prior_own_balance: Decimal | None = None
    prior_rollup_balance: Decimal | None = None
    depth: int = 1
    children: list[TrialBalanceRow] = field(default_factory=list)


@dataclass(frozen=True)
class TrialBalance:
    """Top-level result: ordered list of root rows (descendants nested),
    plus totals and the as_of metadata.

    `is_balanced` is the load-bearing invariant — extension of Group D's
    75-entry tie-out property. If a trial balance ever returns False
    here, something has bypassed the posting service.
    """

    as_of: date
    prior_as_of: date | None
    rows: list[TrialBalanceRow]
    total_debits: Decimal
    total_credits: Decimal

    @property
    def is_balanced(self) -> bool:
        return self.total_debits == self.total_credits


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def compute_trial_balance(
    *,
    as_of: date,
    prior_as_of: date | None = None,
    include_zero: bool = False,
    types: Iterable[str] | None = None,
) -> TrialBalance:
    """Build the trial balance as of `as_of`.

    Args:
      as_of: cutoff for posted journal lines (posting_date <= as_of).
      prior_as_of: if provided, populates per-row prior_* fields.
      include_zero: if True, include accounts with zero balance and
        zero activity in the window. Default False.
      types: restrict to a subset of AccountType values. None = all.

    Returns:
      TrialBalance with rows ordered by (TYPE_RANK, display_order, name)
      within each grouping. Children are nested under parents.
    """
    sums_now = _aggregate_balances(as_of, types=types)
    sums_prior = (
        _aggregate_balances(prior_as_of, types=types)
        if prior_as_of is not None
        else {}
    )

    accounts_by_pk = {
        a.pk: a
        for a in Account.objects.filter(
            **({"type__in": list(types)} if types else {})
        )
    }

    # Activity-aware filter: include accounts with non-zero own_balance
    # OR any posted activity in the window. Children that get pulled in
    # by their parent's rollup-only contribution still appear (the tree
    # walk preserves them). include_zero=True bypasses the filter.
    visible_pks = _visible_account_pks(
        accounts_by_pk=accounts_by_pk,
        sums=sums_now,
        include_zero=include_zero,
    )

    # Build per-account rows (no children yet; tree assembly comes next).
    rows_by_pk: dict[int, TrialBalanceRow] = {}
    for pk in visible_pks:
        account = accounts_by_pk[pk]
        debits, credits, own = _row_balance(sums_now.get(pk), account.normal_balance)
        prior_own = (
            _row_balance(sums_prior.get(pk), account.normal_balance)[2]
            if prior_as_of is not None
            else None
        )
        rows_by_pk[pk] = TrialBalanceRow(
            account=account,
            debits_total=debits,
            credits_total=credits,
            own_balance=own,
            rollup_balance=own,  # provisional — overwritten by walk
            prior_own_balance=prior_own,
            prior_rollup_balance=prior_own,  # provisional
        )

    # Assemble parent → children tree, calculate rollup_balance, depth.
    roots = _assemble_tree(rows_by_pk, accounts_by_pk)

    # Order: TYPE_RANK outer, (display_order, name) inner. Recursive.
    _sort_rows(roots)

    # Totals are sums of row debits / credits across all visible rows
    # (own — not rollup, since rollup would double-count).
    total_debits = sum(
        (row.debits_total for row in _walk(roots)), start=ZERO
    )
    total_credits = sum(
        (row.credits_total for row in _walk(roots)), start=ZERO
    )

    return TrialBalance(
        as_of=as_of,
        prior_as_of=prior_as_of,
        rows=roots,
        total_debits=total_debits,
        total_credits=total_credits,
    )


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _aggregate_balances(
    as_of: date, *, types: Iterable[str] | None = None
) -> dict[int, dict[str, Decimal]]:
    """Single ORM aggregate query: per-account sum of debit/credit
    on posted lines with posting_date <= as_of.

    Returns: {account_pk: {"debits": Decimal, "credits": Decimal}}.
    """
    qs = JournalLine.objects.filter(
        journal_entry__status=JournalEntryStatus.POSTED,
        journal_entry__posting_date__lte=as_of,
    )
    if types:
        qs = qs.filter(account__type__in=list(types))

    decimal_field = DecimalField(max_digits=18, decimal_places=2)
    rows = qs.values("account").annotate(
        debits=Coalesce(Sum("debit_amount"), Value(ZERO), output_field=decimal_field),
        credits=Coalesce(Sum("credit_amount"), Value(ZERO), output_field=decimal_field),
    )

    return {
        row["account"]: {
            "debits": row["debits"] or ZERO,
            "credits": row["credits"] or ZERO,
        }
        for row in rows
    }


def _row_balance(
    sums: dict[str, Decimal] | None, normal_balance: str,
) -> tuple[Decimal, Decimal, Decimal]:
    """Translate aggregated sums into (debits, credits, own_balance).

    own_balance is positive when the account holds the side it's
    supposed to (debit-normal: debits > credits; credit-normal: credits
    > debits). A debit-normal account with own_balance < 0 indicates an
    abnormal balance; the report shows it as-is.
    """
    if sums is None:
        return ZERO, ZERO, ZERO
    debits = sums["debits"]
    credits = sums["credits"]
    if normal_balance == NormalBalance.DEBIT:
        own = debits - credits
    else:
        own = credits - debits
    return debits, credits, own


def _visible_account_pks(
    *,
    accounts_by_pk: dict[int, Account],
    sums: dict[int, dict[str, Decimal]],
    include_zero: bool,
) -> set[int]:
    """Activity-aware visibility filter."""
    if include_zero:
        return set(accounts_by_pk.keys())

    visible: set[int] = set()
    for pk in accounts_by_pk:
        s = sums.get(pk)
        if s is None:
            continue  # no activity at all
        # Either non-zero balance or non-zero activity
        if s["debits"] != ZERO or s["credits"] != ZERO:
            visible.add(pk)

    # Pull in ancestors of every visible account so the tree renders
    # cleanly — a leaf with no parent in the visible set would
    # otherwise appear as a top-level orphan.
    visible_with_ancestors = set(visible)
    for pk in visible:
        cur = accounts_by_pk[pk].parent_account_id
        while cur is not None and cur in accounts_by_pk and cur not in visible_with_ancestors:
            visible_with_ancestors.add(cur)
            cur = accounts_by_pk[cur].parent_account_id
    return visible_with_ancestors


# ---------------------------------------------------------------------------
# Tree assembly
# ---------------------------------------------------------------------------


def _assemble_tree(
    rows_by_pk: dict[int, TrialBalanceRow],
    accounts_by_pk: dict[int, Account],
) -> list[TrialBalanceRow]:
    """Wire children into parents; compute rollup_balance recursively.

    A row whose parent is in the visible set becomes that parent's
    child; otherwise it's a root. Rollup includes self + all
    descendants regardless of own visibility.
    """
    roots: list[TrialBalanceRow] = []
    for pk, row in rows_by_pk.items():
        parent_pk = accounts_by_pk[pk].parent_account_id
        if parent_pk is not None and parent_pk in rows_by_pk:
            rows_by_pk[parent_pk].children.append(row)
        else:
            roots.append(row)

    # Compute rollup_balance + depth via post-order traversal.
    def visit(row: TrialBalanceRow, depth: int) -> tuple[Decimal, Decimal | None]:
        row.depth = depth
        rollup = row.own_balance
        prior_rollup: Decimal | None = (
            row.prior_own_balance if row.prior_own_balance is not None else None
        )
        for child in row.children:
            child_rollup, child_prior = visit(child, depth + 1)
            rollup += child_rollup
            if prior_rollup is not None and child_prior is not None:
                prior_rollup += child_prior
            elif child_prior is not None:
                prior_rollup = child_prior
        row.rollup_balance = rollup
        row.prior_rollup_balance = prior_rollup
        return rollup, prior_rollup

    for root in roots:
        visit(root, 1)
    return roots


def _sort_rows(rows: list[TrialBalanceRow]) -> None:
    """In-place sort: TYPE_RANK at the outer level, (display_order, name)
    inside each type grouping; recursively sort children."""
    rows.sort(
        key=lambda r: (
            TYPE_RANK.get(r.account.type, 99),
            r.account.display_order,
            r.account.name,
        )
    )
    for row in rows:
        # Children are within a single parent, so they share the same
        # type bucket — but TYPE_RANK is harmless and lets a future
        # mixed-type tree degrade gracefully.
        row.children.sort(
            key=lambda r: (
                TYPE_RANK.get(r.account.type, 99),
                r.account.display_order,
                r.account.name,
            )
        )
        _sort_rows(row.children)


def _walk(rows: list[TrialBalanceRow]):
    """Depth-first iterator across the row tree."""
    for row in rows:
        yield row
        yield from _walk(row.children)
