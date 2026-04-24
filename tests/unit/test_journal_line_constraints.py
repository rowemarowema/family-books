"""JournalLine DB CHECK constraints + pg_catalog drift detection."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.db import IntegrityError, connection, transaction
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from books.accounting.models import (
    Account,
    AccountType,
    JournalEntry,
    JournalEntryStatus,
    JournalLine,
    NormalBalance,
)


# ---------------------------------------------------------------------------
# pg_catalog drift detection (D.C4 acceptance)
# ---------------------------------------------------------------------------


REQUIRED_CHECK_CONSTRAINTS = {
    "journal_line_debit_non_negative",
    "journal_line_credit_non_negative",
    "journal_line_debit_xor_credit",
}


@pytest.mark.django_db
def test_required_check_constraints_present():
    """Asserts the three CHECK constraints exist on journal_line by name.

    If any future migration drops or renames one of these, this test
    fails — silent loss of the row-level invariant becomes a visible
    regression instead of a silent one.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT conname
            FROM pg_catalog.pg_constraint
            WHERE conrelid = 'journal_line'::regclass
              AND contype = 'c'
            """
        )
        present = {row[0] for row in cursor.fetchall()}
    missing = REQUIRED_CHECK_CONSTRAINTS - present
    assert not missing, f"Missing CHECK constraints on journal_line: {sorted(missing)}"


# ---------------------------------------------------------------------------
# Hypothesis: every (debit, credit) permutation behaves correctly
# ---------------------------------------------------------------------------


@pytest.fixture
def scratch_entry(db):
    """A draft entry + asset account that tests can drop lines into."""
    acct = Account.objects.create(
        account_number="5000",
        name="Scratch",
        type=AccountType.ASSET,
        normal_balance=NormalBalance.DEBIT,
    )
    entry = JournalEntry.objects.create(
        entry_date=date(2026, 4, 24),
        posting_date=date(2026, 4, 24),
        status=JournalEntryStatus.DRAFT,
    )
    return entry, acct


_decimal = st.decimals(
    min_value=Decimal("0.00"),
    max_value=Decimal("10000.00"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)


@pytest.mark.django_db
@given(debit=_decimal, credit=_decimal)
@settings(
    max_examples=200,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
    deadline=None,
)
def test_exactly_one_positive_commits_else_integrity_error(scratch_entry, debit, credit):
    """CHECK constraints catch every illegal (debit, credit) shape."""
    entry, acct = scratch_entry

    dr_pos = debit > 0
    cr_pos = credit > 0
    legal = dr_pos ^ cr_pos  # exactly one positive

    def _create():
        JournalLine.objects.create(
            journal_entry=entry,
            account=acct,
            debit_amount=debit,
            credit_amount=credit,
        )

    if legal:
        # Rollback after the successful insert so hypothesis examples stay
        # isolated and we don't spam the parent entry with lines.
        with transaction.atomic():
            _create()
            transaction.set_rollback(True)
    else:
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                _create()


@pytest.mark.django_db
def test_negative_debit_rejected(scratch_entry):
    entry, acct = scratch_entry
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            JournalLine.objects.create(
                journal_entry=entry,
                account=acct,
                debit_amount=Decimal("-5.00"),
                credit_amount=Decimal("0.00"),
            )


@pytest.mark.django_db
def test_negative_credit_rejected(scratch_entry):
    entry, acct = scratch_entry
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            JournalLine.objects.create(
                journal_entry=entry,
                account=acct,
                debit_amount=Decimal("0.00"),
                credit_amount=Decimal("-5.00"),
            )
