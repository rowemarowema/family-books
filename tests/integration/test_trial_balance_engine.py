"""Tests for compute_trial_balance() — engine layer.

Covers:
- Basic computation (debits vs credits totals balance).
- Sort order (TYPE_RANK outer, display_order/name inner).
- Activity-aware visibility (default vs include_zero=True).
- Hierarchical rollup (parent.rollup = own + sum(children.rollup)).
- Comparative columns (prior_own / prior_rollup populated when prior_as_of set).
- Type filter.
- As-of cutoff (entries posted after as_of are excluded).
- Hypothesis property tests (tie-out under random data; rollup recurrence).
- Real-fixture smoke (default_coa.json + 5 opening balances).
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.core.management import call_command

from books.accounting.factories import (
    AccountFactory,
    EquityAccountFactory,
    ExpenseAccountFactory,
    LiabilityAccountFactory,
    RevenueAccountFactory,
    make_balanced_entry,
)
from books.accounting.models import (
    Account,
    AccountType,
    NormalBalance,
)
from books.accounting.opening_balances import (
    OPENING_BALANCE_EQUITY_ACCOUNT_NUMBER,
    set_opening_balance,
)
from books.accounting.posting import post_entry
from books.accounting.reports.trial_balance import (
    TYPE_ORDER,
    compute_trial_balance,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def opening_balance_equity(db) -> Account:
    return EquityAccountFactory(
        account_number=OPENING_BALANCE_EQUITY_ACCOUNT_NUMBER,
        name="Opening Balance Equity (System)",
        is_system=True,
        display_order=-200,
    )


@pytest.fixture
def cash(db, opening_balance_equity) -> Account:
    return AccountFactory(
        account_number="1-0001",
        name="Cash",
        type=AccountType.ASSET,
        normal_balance=NormalBalance.DEBIT,
    )


@pytest.fixture
def revenue(db, opening_balance_equity) -> Account:
    return RevenueAccountFactory(
        account_number="4-0001",
        name="Sales",
    )


# ---------------------------------------------------------------------------
# Basic shape
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_empty_trial_balance_balances_to_zero():
    tb = compute_trial_balance(as_of=date(2026, 1, 1))
    assert tb.total_debits == Decimal("0.00")
    assert tb.total_credits == Decimal("0.00")
    assert tb.is_balanced
    assert tb.rows == []


@pytest.mark.django_db
def test_trial_balance_balances_after_one_posted_entry(owner, cash, revenue):
    entry = make_balanced_entry(
        debit_account=cash, credit_account=revenue, amount=Decimal("100.00"),
    )
    post_entry(entry, user=owner, reason="test")

    tb = compute_trial_balance(as_of=date(2099, 1, 1))
    assert tb.is_balanced
    assert tb.total_debits == Decimal("100.00")
    assert tb.total_credits == Decimal("100.00")


@pytest.mark.django_db
def test_as_of_cutoff_excludes_later_entries(owner, cash, revenue):
    """Entries posted after as_of must not appear."""
    today = date.today()
    yesterday = today - timedelta(days=1)
    tomorrow = today + timedelta(days=1)

    e1 = make_balanced_entry(
        debit_account=cash, credit_account=revenue, amount=Decimal("100.00"),
        entry_date=yesterday, posting_date=yesterday,
    )
    post_entry(e1, user=owner)
    e2 = make_balanced_entry(
        debit_account=cash, credit_account=revenue, amount=Decimal("50.00"),
        entry_date=tomorrow, posting_date=tomorrow,
    )
    post_entry(e2, user=owner)

    tb_today = compute_trial_balance(as_of=today)
    assert tb_today.total_debits == Decimal("100.00")
    tb_future = compute_trial_balance(as_of=tomorrow)
    assert tb_future.total_debits == Decimal("150.00")


@pytest.mark.django_db
def test_draft_entries_are_excluded(owner, cash, revenue):
    """Only POSTED journal lines count toward the trial balance."""
    make_balanced_entry(
        debit_account=cash, credit_account=revenue, amount=Decimal("100.00"),
    )
    # NOT posted — still draft.

    tb = compute_trial_balance(as_of=date(2099, 1, 1))
    assert tb.total_debits == Decimal("0.00")
    assert tb.total_credits == Decimal("0.00")


# ---------------------------------------------------------------------------
# Sort order — TYPE_RANK outer, (display_order, name) inner
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_type_rank_orders_groups(owner, opening_balance_equity):
    """The 5 types render in conventional order: Asset, Liability,
    Equity, Revenue, Expense."""
    asset = AccountFactory(account_number="1-A", name="A1", type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT)
    liability = LiabilityAccountFactory(account_number="2-A", name="L1")
    equity = EquityAccountFactory(account_number="3-A", name="E1")
    revenue = RevenueAccountFactory(account_number="4-A", name="R1")
    expense = ExpenseAccountFactory(account_number="5-A", name="X1")

    # Post entries that touch every type so they're all visible.
    post_entry(
        make_balanced_entry(debit_account=asset, credit_account=liability, amount=Decimal("10.00")),
        user=owner,
    )
    post_entry(
        make_balanced_entry(debit_account=expense, credit_account=revenue, amount=Decimal("10.00")),
        user=owner,
    )
    # Equity needs activity to appear under default; touch it via opening balance.
    set_opening_balance(equity, amount=Decimal("5.00"), as_of=date(2001, 1, 1), user=owner)

    tb = compute_trial_balance(as_of=date(2099, 1, 1))
    types_in_order = [row.account.type for row in tb.rows if row.account.type in TYPE_ORDER]
    seen = []
    for t in types_in_order:
        if not seen or seen[-1] != t:
            seen.append(t)
    # The first appearance of each type respects TYPE_ORDER.
    assert [t for t in TYPE_ORDER if t in seen] == seen


@pytest.mark.django_db
def test_display_order_then_name_within_type(owner, revenue):
    """Within the same type bucket, rows sort by (display_order, name)."""
    a_alpha = AccountFactory(account_number="1-A", name="Alpha", display_order=0)
    a_zebra = AccountFactory(account_number="1-Z", name="Zebra", display_order=0)
    a_special = AccountFactory(
        account_number="1-S", name="Special", display_order=-100,
    )

    # Each gets activity so all appear.
    for acct in (a_alpha, a_zebra, a_special):
        post_entry(
            make_balanced_entry(
                debit_account=acct, credit_account=revenue, amount=Decimal("1.00"),
            ),
            user=owner,
        )

    tb = compute_trial_balance(as_of=date(2099, 1, 1))
    asset_rows = [r for r in tb.rows if r.account.type == AccountType.ASSET]
    names = [r.account.name for r in asset_rows]
    # display_order=-100 first; then display_order=0 alpha-sorted by name.
    assert names == ["Special", "Alpha", "Zebra"]


# ---------------------------------------------------------------------------
# Activity-aware visibility
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_zero_balance_zero_activity_account_hidden_by_default(owner, cash, revenue):
    untouched = AccountFactory(
        account_number="1-X", name="Untouched", display_order=0,
    )
    post_entry(
        make_balanced_entry(debit_account=cash, credit_account=revenue, amount=Decimal("10.00")),
        user=owner,
    )

    tb = compute_trial_balance(as_of=date(2099, 1, 1))
    visible_pks = {r.account.pk for r in _walk_rows(tb.rows)}
    assert cash.pk in visible_pks
    assert untouched.pk not in visible_pks


@pytest.mark.django_db
def test_include_zero_shows_inactive_no_activity(owner, cash, revenue):
    untouched = AccountFactory(
        account_number="1-X", name="Untouched", display_order=0,
    )
    post_entry(
        make_balanced_entry(debit_account=cash, credit_account=revenue, amount=Decimal("10.00")),
        user=owner,
    )

    tb = compute_trial_balance(as_of=date(2099, 1, 1), include_zero=True)
    visible_pks = {r.account.pk for r in _walk_rows(tb.rows)}
    assert untouched.pk in visible_pks


@pytest.mark.django_db
def test_inactive_account_with_activity_still_visible(owner, cash, revenue):
    """Refinement of Q6: include if balance != 0 OR posted activity in
    the as-of window, regardless of active status."""
    post_entry(
        make_balanced_entry(debit_account=cash, credit_account=revenue, amount=Decimal("10.00")),
        user=owner,
    )
    cash.is_active = False
    cash.save()

    tb = compute_trial_balance(as_of=date(2099, 1, 1))
    visible_pks = {r.account.pk for r in _walk_rows(tb.rows)}
    assert cash.pk in visible_pks


# ---------------------------------------------------------------------------
# Hierarchical rollup
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_rollup_balance_includes_descendants(owner, opening_balance_equity, revenue):
    """Parent.rollup_balance == parent.own_balance + sum(child.rollup_balance)."""
    parent = AccountFactory(
        account_number="1-P", name="Investments", display_order=0,
    )
    child_a = AccountFactory(
        account_number="1-PA", name="Apple", parent_account=parent, display_order=0,
    )
    child_b = AccountFactory(
        account_number="1-PB", name="Bank Of America", parent_account=parent, display_order=0,
    )
    grandchild = AccountFactory(
        account_number="1-PAA", name="AAPL Lot 1", parent_account=child_a, display_order=0,
    )

    set_opening_balance(child_a, amount=Decimal("100.00"), as_of=date(2001, 1, 1), user=owner)
    set_opening_balance(child_b, amount=Decimal("200.00"), as_of=date(2001, 1, 1), user=owner)
    set_opening_balance(grandchild, amount=Decimal("50.00"), as_of=date(2001, 1, 1), user=owner)
    # Parent itself with non-zero balance, to test parent's own_balance contribution.
    set_opening_balance(parent, amount=Decimal("10.00"), as_of=date(2001, 1, 1), user=owner)

    tb = compute_trial_balance(as_of=date(2099, 1, 1))
    asset_rows = [r for r in tb.rows if r.account.pk == parent.pk]
    assert len(asset_rows) == 1
    parent_row = asset_rows[0]

    assert parent_row.own_balance == Decimal("10.00")
    # rollup = parent's 10 + childA(100) + grandchild(50) + childB(200) = 360
    assert parent_row.rollup_balance == Decimal("360.00")


@pytest.mark.django_db
def test_invisible_intermediate_parent_still_shows_in_tree(
    owner, opening_balance_equity, revenue
):
    """If a child has activity but its parent doesn't, the parent
    must still appear so the tree is intact (no orphan rendering)."""
    parent = AccountFactory(
        account_number="1-P", name="Investments", display_order=0,
    )
    child = AccountFactory(
        account_number="1-PA", name="Apple", parent_account=parent, display_order=0,
    )
    set_opening_balance(child, amount=Decimal("100.00"), as_of=date(2001, 1, 1), user=owner)

    tb = compute_trial_balance(as_of=date(2099, 1, 1))
    parent_rows = [r for r in _walk_rows(tb.rows) if r.account.pk == parent.pk]
    assert len(parent_rows) == 1
    parent_row = parent_rows[0]
    assert parent_row.own_balance == Decimal("0.00")
    # Rollup is the sum of the descendant.
    assert parent_row.rollup_balance == Decimal("100.00")


# ---------------------------------------------------------------------------
# Comparative columns
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_prior_as_of_populates_prior_fields(owner, cash, revenue):
    yesterday = date.today() - timedelta(days=2)
    today = date.today()

    e1 = make_balanced_entry(
        debit_account=cash, credit_account=revenue, amount=Decimal("100.00"),
        entry_date=yesterday, posting_date=yesterday,
    )
    post_entry(e1, user=owner)
    e2 = make_balanced_entry(
        debit_account=cash, credit_account=revenue, amount=Decimal("50.00"),
        entry_date=today, posting_date=today,
    )
    post_entry(e2, user=owner)

    tb = compute_trial_balance(as_of=today, prior_as_of=yesterday)
    cash_row = next(r for r in _walk_rows(tb.rows) if r.account.pk == cash.pk)
    assert cash_row.own_balance == Decimal("150.00")  # both entries
    assert cash_row.prior_own_balance == Decimal("100.00")  # only e1 by yesterday


@pytest.mark.django_db
def test_no_prior_as_of_leaves_prior_fields_none(owner, cash, revenue):
    e = make_balanced_entry(debit_account=cash, credit_account=revenue, amount=Decimal("10.00"))
    post_entry(e, user=owner)

    tb = compute_trial_balance(as_of=date(2099, 1, 1))
    cash_row = next(r for r in _walk_rows(tb.rows) if r.account.pk == cash.pk)
    assert cash_row.prior_own_balance is None
    assert cash_row.prior_rollup_balance is None


# ---------------------------------------------------------------------------
# Type filter
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_types_filter_restricts_to_named_types(owner, cash, revenue):
    expense = ExpenseAccountFactory(account_number="5-A", name="Office")
    post_entry(
        make_balanced_entry(debit_account=cash, credit_account=revenue, amount=Decimal("10.00")),
        user=owner,
    )
    post_entry(
        make_balanced_entry(debit_account=expense, credit_account=revenue, amount=Decimal("5.00")),
        user=owner,
    )

    tb = compute_trial_balance(as_of=date(2099, 1, 1), types=[AccountType.ASSET])
    types_present = {r.account.type for r in _walk_rows(tb.rows)}
    assert types_present == {AccountType.ASSET}


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seed", [42, 1234, 9999, 0, 7777])
@pytest.mark.django_db
def test_property_trial_balance_ties_out_under_random_entries(seed, owner):
    """Property: for any sequence of N balanced posted entries with
    arbitrary amounts and account pairings, total_debits ==
    total_credits in the resulting trial balance.

    Group D's 75-entry test proves tie-out at the JournalLine level;
    this re-verifies the invariant survives the engine's tree-
    assembly + activity-aware-filter layers. Parametrized over 5 seeds
    rather than hypothesis-driven because the engine builds DB state
    per-example and hypothesis state accumulation interacts poorly
    with Account.parent_account PROTECT."""
    import random
    rng = random.Random(seed)

    assets = [
        AccountFactory(
            account_number=f"1-S{seed}-{i}", name=f"Asset{i}",
            type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
        )
        for i in range(3)
    ]
    revenues = [
        RevenueAccountFactory(account_number=f"4-S{seed}-{i}", name=f"Rev{i}")
        for i in range(3)
    ]

    for _ in range(rng.randint(5, 15)):
        post_entry(
            make_balanced_entry(
                debit_account=rng.choice(assets),
                credit_account=rng.choice(revenues),
                amount=Decimal(rng.randint(1, 1000)),
            ),
            user=owner,
        )

    tb = compute_trial_balance(as_of=date(2099, 1, 1))
    assert tb.is_balanced
    assert tb.total_debits == tb.total_credits


@pytest.mark.django_db
def test_reversed_opening_balances_net_to_zero_in_trial_balance(
    owner, opening_balance_equity, revenue
):
    """Property: post N opening balances, reverse half, assert the
    trial balance reflects only the un-reversed half's amounts.

    This catches the case Mark flagged: if compute_trial_balance() ever
    starts treating reversed JEs as still-active, an account with a
    reversed opening balance would show its original amount instead of
    netting to zero.

    Mechanics: a reversal's lines flip debit↔credit relative to the
    original. Original Cash = +100 dr; reversal Cash = +100 cr. Same
    account's row in the TB has debits_total=100 AND credits_total=100,
    so own_balance (debits - credits for debit-normal) = 0.
    """
    from books.accounting.opening_balances import (
        reverse_opening_balance,
        set_opening_balance,
    )

    accounts = [
        AccountFactory(
            account_number=f"1-R{i}",
            name=f"AssetR{i}",
            type=AccountType.ASSET,
            normal_balance=NormalBalance.DEBIT,
        )
        for i in range(6)
    ]
    amounts = [Decimal("100.00"), Decimal("200.00"), Decimal("300.00"),
               Decimal("400.00"), Decimal("500.00"), Decimal("600.00")]

    results = []
    for acct, amt in zip(accounts, amounts, strict=True):
        results.append(
            set_opening_balance(
                acct, amount=amt, as_of=date(2001, 1, 1), user=owner,
            )
        )

    # Reverse the first 3.
    for r in results[:3]:
        reverse_opening_balance(r, user=owner, reason="test reversal")

    tb = compute_trial_balance(as_of=date(2099, 1, 1))

    # Reversed accounts: own_balance == 0 (the original and reversal
    # cancel out via summation).
    for acct in accounts[:3]:
        rows = [r for r in _walk_rows(tb.rows) if r.account.pk == acct.pk]
        # Activity-aware filter: balance==0 but lines exist (debits+credits
        # both = original_amount), so account IS visible.
        assert len(rows) == 1
        assert rows[0].own_balance == Decimal("0.00"), (
            f"Reversed account {acct.account_number} should net to zero, "
            f"got own_balance={rows[0].own_balance}"
        )

    # Un-reversed accounts: own_balance == original amount.
    for acct, amt in zip(accounts[3:], amounts[3:], strict=True):
        rows = [r for r in _walk_rows(tb.rows) if r.account.pk == acct.pk]
        assert len(rows) == 1
        assert rows[0].own_balance == amt, (
            f"Un-reversed account {acct.account_number} expected "
            f"own_balance={amt}, got {rows[0].own_balance}"
        )

    # Total tie-out: still balanced (every reversal is itself a balanced
    # entry, so adding reversals can't unbalance the books).
    assert tb.is_balanced

    # OBE: net of remaining 3 un-reversed credits (400+500+600 = 1500).
    obe_row = next(
        r for r in _walk_rows(tb.rows)
        if r.account.pk == opening_balance_equity.pk
    )
    assert obe_row.own_balance == Decimal("1500.00")


@pytest.mark.django_db
def test_rollup_recurrence_holds_for_built_tree(owner, opening_balance_equity, revenue):
    """For every parent in the tree, rollup_balance equals own_balance
    plus the sum of children's rollup_balance values."""
    parent = AccountFactory(account_number="1-P", name="P", display_order=0)
    children = [
        AccountFactory(
            account_number=f"1-P{i}", name=f"C{i}",
            parent_account=parent, display_order=i,
        )
        for i in range(3)
    ]
    for i, c in enumerate(children):
        set_opening_balance(c, amount=Decimal(f"{(i + 1) * 100}.00"),
                            as_of=date(2001, 1, 1), user=owner)
    set_opening_balance(parent, amount=Decimal("50.00"),
                        as_of=date(2001, 1, 1), user=owner)

    tb = compute_trial_balance(as_of=date(2099, 1, 1))

    def assert_recurrence(row):
        expected = row.own_balance + sum(
            (c.rollup_balance for c in row.children), start=Decimal("0.00")
        )
        assert row.rollup_balance == expected, (
            f"{row.account.account_number}: rollup={row.rollup_balance}, "
            f"expected={expected}"
        )
        for c in row.children:
            assert_recurrence(c)

    for root in tb.rows:
        assert_recurrence(root)


# ---------------------------------------------------------------------------
# Real-fixture smoke
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_real_fixture_plus_opening_balances_balances():
    """End-to-end smoke: load default_coa.json, set 5 opening balances,
    compute trial balance, assert is_balanced.

    This is the test that proves the engine works against production-
    shape data — 643 accounts with hierarchy. Per the standing rule:
    every command-line / engine operation that touches user data has
    at least one real-fixture-shape test."""
    call_command("seed_default_coa")

    from django.contrib.auth import get_user_model
    User = get_user_model()
    user = User.objects.create_superuser(
        username="smoke@example.com", email="smoke@example.com", password="x" * 20,
    )

    boa = Account.objects.get(account_number="1-0179")
    checking = Account.objects.get(name="Checking (6708)")
    etrade_cash = Account.objects.get(account_number="1-0270")
    rowe_bowl = Account.objects.get(name="Rowe Bowl")
    apvisa = Account.objects.get(account_number="2-0002")

    set_opening_balance(boa, amount=Decimal("8500.00"), as_of=date(2001, 1, 1), user=user)
    set_opening_balance(checking, amount=Decimal("12000.00"), as_of=date(2001, 1, 1), user=user)
    set_opening_balance(etrade_cash, amount=Decimal("4500.00"), as_of=date(2005, 6, 1), user=user)
    set_opening_balance(rowe_bowl, amount=Decimal("500.00"), as_of=date(2010, 1, 1), user=user)
    set_opening_balance(apvisa, amount=Decimal("650.00"), as_of=date(2005, 6, 1), user=user)

    tb = compute_trial_balance(as_of=date(2099, 1, 1))
    assert tb.is_balanced
    assert tb.total_debits == tb.total_credits == Decimal("26150.00")  # sum of 5 amounts


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _walk_rows(rows):
    for row in rows:
        yield row
        yield from _walk_rows(row.children)
