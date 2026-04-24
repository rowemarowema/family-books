"""Account deactivation vs hard-delete (refinement #3 / D.F6)."""
from __future__ import annotations

from decimal import Decimal

import pytest
from django.db.models import ProtectedError

from books.accounting.exceptions import JournalEntryValidationError
from books.accounting.factories import (
    AccountFactory,
    RevenueAccountFactory,
    make_balanced_entry,
)
from books.accounting.models import JournalLine
from books.accounting.posting import post_entry


@pytest.fixture
def cash(db):
    return AccountFactory(account_number="1000", name="Cash")


@pytest.fixture
def revenue(db):
    return RevenueAccountFactory(account_number="4000", name="Sales Revenue")


@pytest.fixture
def cash_with_posted_line(owner, cash, revenue):
    entry = make_balanced_entry(
        debit_account=cash, credit_account=revenue, amount=Decimal("50.00"),
    )
    post_entry(entry, user=owner, reason="seed posted line")
    return cash


@pytest.mark.django_db
def test_deactivate_account_with_posted_lines_succeeds(cash_with_posted_line):
    cash_with_posted_line.is_active = False
    cash_with_posted_line.save()
    cash_with_posted_line.refresh_from_db()
    assert cash_with_posted_line.is_active is False
    # Posted lines intact.
    assert JournalLine.objects.filter(account=cash_with_posted_line).count() == 1


@pytest.mark.django_db
def test_posting_to_deactivated_account_rejected(owner, cash_with_posted_line, revenue):
    cash_with_posted_line.is_active = False
    cash_with_posted_line.save()

    new_entry = make_balanced_entry(
        debit_account=cash_with_posted_line,
        credit_account=revenue,
        amount=Decimal("10.00"),
    )
    with pytest.raises(JournalEntryValidationError) as exc:
        post_entry(new_entry, user=owner)
    assert any("Inactive accounts" in e for e in exc.value.errors_list)


@pytest.mark.django_db
def test_hard_delete_account_with_posted_lines_raises_protected_error(
    cash_with_posted_line,
):
    with pytest.raises(ProtectedError):
        cash_with_posted_line.delete()


@pytest.mark.django_db
def test_hard_delete_account_with_no_posted_lines_succeeds(db):
    orphan = AccountFactory(account_number="9999", name="Orphan")
    orphan.delete()
