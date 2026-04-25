"""Tests for set_opening_balance() / reverse_opening_balance().

Refusal contract, side selection by normal_balance, display-field
updates, audit rows on both paths, and transactional rollback. Covers
F.C1 + F.C3 (protections) of the Group F breakdown.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from books.accounting.exceptions import OpeningBalanceError
from books.accounting.factories import (
    AccountFactory,
    EquityAccountFactory,
    LiabilityAccountFactory,
    RevenueAccountFactory,
)
from books.accounting.models import (
    Account,
    AccountType,
    JournalEntry,
    JournalEntryStatus,
    NormalBalance,
)
from books.accounting.opening_balances import (
    OPENING_BALANCE_EQUITY_ACCOUNT_NUMBER,
    OpeningBalanceResult,
    reverse_opening_balance,
    set_opening_balance,
)
from books.audit.models import AuditAction, AuditLog

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def opening_balance_equity(db) -> Account:
    """The system OBE account (3-9100). Group F's offset target."""
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
def credit_card(db, opening_balance_equity) -> Account:
    return LiabilityAccountFactory(
        account_number="2-0001",
        name="Credit Card",
    )


# ---------------------------------------------------------------------------
# Refusal contract
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_refuses_opening_balance_on_system_account(owner, opening_balance_equity):
    with pytest.raises(OpeningBalanceError) as exc:
        set_opening_balance(
            opening_balance_equity,
            amount=Decimal("100.00"),
            as_of=date(2001, 1, 1),
            user=owner,
        )
    assert "system account" in str(exc.value).lower()
    assert AuditLog.objects.filter(
        action=AuditAction.OPENING_BALANCE_REFUSED
    ).count() == 1


@pytest.mark.django_db
def test_refuses_opening_balance_on_revenue_account(owner, opening_balance_equity):
    rev = RevenueAccountFactory(account_number="4-0001", name="Sales")
    with pytest.raises(OpeningBalanceError) as exc:
        set_opening_balance(
            rev,
            amount=Decimal("500.00"),
            as_of=date(2001, 1, 1),
            user=owner,
        )
    assert "revenue and expense" in str(exc.value).lower()


@pytest.mark.django_db
def test_refuses_opening_balance_on_expense_account(owner, opening_balance_equity):
    exp = AccountFactory(
        account_number="5-0001",
        name="Office Supplies",
        type=AccountType.EXPENSE,
        normal_balance=NormalBalance.DEBIT,
    )
    with pytest.raises(OpeningBalanceError):
        set_opening_balance(
            exp,
            amount=Decimal("75.00"),
            as_of=date(2001, 1, 1),
            user=owner,
        )


@pytest.mark.parametrize("bad_amount", [Decimal("0.00"), Decimal("-1.00")])
@pytest.mark.django_db
def test_refuses_zero_or_negative_amount(owner, cash, bad_amount):
    with pytest.raises(OpeningBalanceError) as exc:
        set_opening_balance(
            cash,
            amount=bad_amount,
            as_of=date(2001, 1, 1),
            user=owner,
        )
    assert "positive" in str(exc.value).lower()


@pytest.mark.django_db
def test_refuses_duplicate_account_as_of(owner, cash):
    set_opening_balance(
        cash, amount=Decimal("100.00"), as_of=date(2001, 1, 1), user=owner,
    )
    with pytest.raises(OpeningBalanceError) as exc:
        set_opening_balance(
            cash, amount=Decimal("200.00"), as_of=date(2001, 1, 1), user=owner,
        )
    assert "already exists" in str(exc.value).lower()
    assert "reverse" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# Side selection by normal_balance
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_asset_opening_balance_debits_target_credits_obe(
    owner, cash, opening_balance_equity
):
    """Debit-normal account (Asset): user's amount is a DEBIT on the
    asset, offset CREDITS Opening Balance Equity."""
    result = set_opening_balance(
        cash, amount=Decimal("8500.00"), as_of=date(2001, 1, 1), user=owner,
    )
    assert isinstance(result, OpeningBalanceResult)
    assert result.debit_amount == Decimal("8500.00")
    assert result.credit_amount == Decimal("0.00")

    je = result.journal_entry
    assert je.status == JournalEntryStatus.POSTED
    lines = list(je.lines.order_by("id"))
    assert len(lines) == 2

    asset_line = next(line for line in lines if line.account_id == cash.pk)
    obe_line = next(
        line for line in lines if line.account_id == opening_balance_equity.pk
    )
    assert asset_line.debit_amount == Decimal("8500.00")
    assert asset_line.credit_amount == Decimal("0.00")
    assert obe_line.debit_amount == Decimal("0.00")
    assert obe_line.credit_amount == Decimal("8500.00")


@pytest.mark.django_db
def test_liability_opening_balance_credits_target_debits_obe(
    owner, credit_card, opening_balance_equity
):
    """Credit-normal account (Liability): user's amount CREDITS the
    liability, offset DEBITS Opening Balance Equity."""
    result = set_opening_balance(
        credit_card,
        amount=Decimal("3200.00"),
        as_of=date(2001, 1, 1),
        user=owner,
    )
    assert result.debit_amount == Decimal("0.00")
    assert result.credit_amount == Decimal("3200.00")

    lines = list(result.journal_entry.lines.order_by("id"))
    liability_line = next(line for line in lines if line.account_id == credit_card.pk)
    obe_line = next(
        line for line in lines if line.account_id == opening_balance_equity.pk
    )
    assert liability_line.credit_amount == Decimal("3200.00")
    assert liability_line.debit_amount == Decimal("0.00")
    assert obe_line.debit_amount == Decimal("3200.00")
    assert obe_line.credit_amount == Decimal("0.00")


@pytest.mark.django_db
def test_equity_opening_balance_credits_target(owner, opening_balance_equity):
    """Equity is credit-normal — same shape as Liability, but the user
    can target a non-system equity account."""
    user_equity = EquityAccountFactory(
        account_number="3-0001", name="Owner Capital",
    )
    result = set_opening_balance(
        user_equity,
        amount=Decimal("5000.00"),
        as_of=date(2001, 1, 1),
        user=owner,
    )
    assert result.credit_amount == Decimal("5000.00")
    assert result.debit_amount == Decimal("0.00")


# ---------------------------------------------------------------------------
# Display fields + entry shape
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_display_fields_updated_on_account(owner, cash):
    set_opening_balance(
        cash, amount=Decimal("100.00"), as_of=date(2001, 1, 1), user=owner,
    )
    cash.refresh_from_db()
    assert cash.opening_balance == Decimal("100.00")
    assert cash.opening_balance_date == date(2001, 1, 1)


@pytest.mark.django_db
def test_reference_number_uses_ob_prefix(owner, cash):
    result = set_opening_balance(
        cash, amount=Decimal("100.00"), as_of=date(2001, 1, 1), user=owner,
    )
    assert result.journal_entry.reference_number == "OB:1-0001:2001-01-01"


@pytest.mark.django_db
def test_entry_dates_match_as_of(owner, cash):
    as_of = date(2001, 1, 1)
    result = set_opening_balance(
        cash, amount=Decimal("100.00"), as_of=as_of, user=owner,
    )
    assert result.journal_entry.entry_date == as_of
    assert result.journal_entry.posting_date == as_of


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_audit_row_written_on_success(owner, cash):
    result = set_opening_balance(
        cash, amount=Decimal("8500.00"), as_of=date(2001, 1, 1), user=owner,
    )
    audit = AuditLog.objects.get(action=AuditAction.OPENING_BALANCE_SET)
    assert audit.after_value["account_number"] == "1-0001"
    assert audit.after_value["amount"] == "8500.00"
    assert audit.after_value["as_of"] == "2001-01-01"
    assert audit.after_value["journal_entry_id"] == result.journal_entry.pk


@pytest.mark.django_db
def test_audit_row_written_on_each_refusal(owner, cash, opening_balance_equity):
    """Refusal cases ALL write OPENING_BALANCE_REFUSED rows so the audit
    trail captures attempted writes, not just successful ones."""
    # System account
    with pytest.raises(OpeningBalanceError):
        set_opening_balance(
            opening_balance_equity,
            amount=Decimal("1.00"),
            as_of=date(2001, 1, 1),
            user=owner,
        )
    # Zero amount
    with pytest.raises(OpeningBalanceError):
        set_opening_balance(
            cash, amount=Decimal("0"), as_of=date(2001, 1, 1), user=owner,
        )
    # Duplicate
    set_opening_balance(
        cash, amount=Decimal("100.00"), as_of=date(2001, 1, 1), user=owner,
    )
    with pytest.raises(OpeningBalanceError):
        set_opening_balance(
            cash, amount=Decimal("100.00"), as_of=date(2001, 1, 1), user=owner,
        )
    refused = AuditLog.objects.filter(action=AuditAction.OPENING_BALANCE_REFUSED)
    assert refused.count() == 3


# ---------------------------------------------------------------------------
# Reverse path
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_reverse_opening_balance_unblocks_re_setting_at_same_as_of(owner, cash):
    """Contract: re-running set_opening_balance with the same
    (account, as_of) is idempotent ONLY via the explicit reverse +
    re-post path. After reverse_opening_balance(), the original JE
    is rendered cancelable in the trial balance and re-posting at
    the same as_of becomes permissible."""
    first = set_opening_balance(
        cash, amount=Decimal("100.00"), as_of=date(2001, 1, 1), user=owner,
    )

    # Without reversing, re-posting refuses.
    with pytest.raises(OpeningBalanceError):
        set_opening_balance(
            cash, amount=Decimal("200.00"), as_of=date(2001, 1, 1), user=owner,
        )

    # Reverse the original.
    reverse_opening_balance(first, user=owner, reason="wrong amount")

    # Re-posting at the SAME as_of now succeeds. The duplicate check
    # filters out reversed JEs.
    second = set_opening_balance(
        cash, amount=Decimal("200.00"), as_of=date(2001, 1, 1), user=owner,
    )
    assert second.journal_entry.pk != first.journal_entry.pk

    # Post-cycle state assertions — these document the actual contract
    # the duplicate-check filter relies on, not just the row count
    # (which includes the reversal as a third row sharing the ref).
    ref = "OB:1-0001:2001-01-01"
    all_with_ref = JournalEntry.objects.filter(reference_number=ref)

    # 3 JEs share the reference: original, reversal-of-original, re-post.
    # The reversal copies reference_number from its original (Group D's
    # reverse_entry, posting.py:169) which is what created the bug
    # this test fixed in the first place.
    assert all_with_ref.count() == 3

    # Exactly 1 is "active" — neither reversed nor itself a reversal.
    # That's the re-post (`second`); it's what the duplicate-check
    # filter must return on a future invocation.
    active = all_with_ref.filter(
        reversed_by__isnull=True,
        reversing_entry__isnull=True,
    )
    assert list(active.values_list("pk", flat=True)) == [second.journal_entry.pk]

    # The original is reversed: a reversal points back to it.
    first.journal_entry.refresh_from_db()
    assert first.journal_entry.reversed_by.exists()

    # The reversal points at the original via reversing_entry.
    reversal = JournalEntry.objects.get(reversing_entry=first.journal_entry)
    assert reversal.reversing_entry_id == first.journal_entry.pk
    assert reversal.reference_number == ref


@pytest.mark.django_db
def test_different_as_of_always_allowed_regardless_of_reversal(owner, cash):
    """Sanity: setting an opening balance for the same account at a
    different as_of is always permitted, independent of the dup check
    on the prior date."""
    set_opening_balance(
        cash, amount=Decimal("100.00"), as_of=date(2001, 1, 1), user=owner,
    )
    set_opening_balance(
        cash, amount=Decimal("200.00"), as_of=date(2005, 6, 1), user=owner,
    )
    assert (
        JournalEntry.objects.filter(
            reference_number__startswith="OB:1-0001:"
        ).count()
        == 2
    )


# ---------------------------------------------------------------------------
# Transactional rollback
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_no_audit_row_when_obe_account_missing(owner):
    """If the offset account doesn't exist (e.g., system COA not seeded),
    the service raises and writes no audit / JE / display-field changes."""
    cash = AccountFactory(
        account_number="1-0001", name="Cash",
        type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
    )
    with pytest.raises(OpeningBalanceError) as exc:
        set_opening_balance(
            cash, amount=Decimal("100.00"), as_of=date(2001, 1, 1), user=owner,
        )
    assert "not found" in str(exc.value).lower()
    # Nothing written.
    assert JournalEntry.objects.count() == 0
    assert AuditLog.objects.filter(
        action__in=[
            AuditAction.OPENING_BALANCE_SET,
            AuditAction.OPENING_BALANCE_REFUSED,
        ]
    ).count() == 0
    cash.refresh_from_db()
    assert cash.opening_balance == Decimal("0.00")
    assert cash.opening_balance_date is None


# ---------------------------------------------------------------------------
# Period-close stub (Stage 2 wires the real check)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_period_close_check_is_currently_a_noop(owner, cash):
    """The Stage-1 period-close stub doesn't refuse any date. Stage 2
    will activate the check via the FiscalPeriod model. This test
    pins the current behavior so Stage 2's activation work is the
    visible diff."""
    # Date far in the past — would trip if any closed period existed,
    # but no FiscalPeriod model is wired yet.
    set_opening_balance(
        cash, amount=Decimal("1.00"), as_of=date(1999, 1, 1), user=owner,
    )
    # And far in the future.
    set_opening_balance(
        cash, amount=Decimal("1.00"), as_of=date(2099, 1, 1), user=owner,
    )
    assert JournalEntry.objects.count() == 2
