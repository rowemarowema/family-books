"""post_entry service — happy path + every rejection branch."""
from __future__ import annotations

from decimal import Decimal

import pytest
from django.utils import timezone

from books.accounting.exceptions import (
    JournalEntryValidationError,
    PostedEntryImmutable,
)
from books.accounting.factories import (
    AccountFactory,
    CreditLineFactory,
    DebitLineFactory,
    JournalEntryFactory,
    RevenueAccountFactory,
    make_balanced_entry,
)
from books.accounting.models import JournalEntryStatus
from books.accounting.posting import post_entry
from books.audit.models import AuditLog


@pytest.fixture
def cash(db):
    return AccountFactory(account_number="1000", name="Cash")


@pytest.fixture
def revenue(db):
    return RevenueAccountFactory(account_number="4000", name="Sales Revenue")


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_balanced_entry_posts(owner, cash, revenue):
    entry = make_balanced_entry(
        debit_account=cash,
        credit_account=revenue,
        amount=Decimal("100.00"),
    )
    posted = post_entry(entry, user=owner, reason="initial sale")

    assert posted.status == JournalEntryStatus.POSTED
    assert posted.posted_at is not None
    assert (timezone.now() - posted.posted_at).total_seconds() < 5


@pytest.mark.django_db
def test_posting_writes_audit_row_with_full_snapshot(owner, cash, revenue):
    entry = make_balanced_entry(
        debit_account=cash,
        credit_account=revenue,
        amount=Decimal("250.00"),
    )
    post_entry(entry, user=owner, reason="Customer A invoice 123")

    audit = AuditLog.objects.get(action="post_entry", entity_id=str(entry.pk))
    assert audit.user == owner
    assert audit.reason == "Customer A invoice 123"
    assert audit.before_value == {"status": "draft"}
    after = audit.after_value
    assert after["status"] == "posted"
    assert after["totals"]["debits"] == "250.00"
    assert after["totals"]["credits"] == "250.00"
    assert len(after["lines"]) == 2
    # Each line snapshot has the required keys.
    for line in after["lines"]:
        assert set(line) == {"account_number", "account_name", "debit", "credit", "memo"}


# ---------------------------------------------------------------------------
# Rejection branches
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_posting_already_posted_entry_raises(owner, cash, revenue):
    entry = make_balanced_entry(
        debit_account=cash, credit_account=revenue, amount=Decimal("10.00"),
    )
    post_entry(entry, user=owner)
    with pytest.raises(PostedEntryImmutable):
        post_entry(entry, user=owner)


@pytest.mark.django_db
def test_posting_entry_with_fewer_than_two_lines_raises(owner, cash):
    entry = JournalEntryFactory()
    DebitLineFactory(journal_entry=entry, account=cash, debit_amount=Decimal("10.00"))
    with pytest.raises(JournalEntryValidationError) as exc:
        post_entry(entry, user=owner)
    assert any("at least 2 lines" in e for e in exc.value.errors_list)


@pytest.mark.django_db
def test_posting_unbalanced_entry_raises(owner, cash, revenue):
    entry = JournalEntryFactory()
    DebitLineFactory(journal_entry=entry, account=cash, debit_amount=Decimal("10.00"))
    CreditLineFactory(journal_entry=entry, account=revenue, credit_amount=Decimal("9.99"))
    with pytest.raises(JournalEntryValidationError) as exc:
        post_entry(entry, user=owner)
    assert any("do not equal" in e for e in exc.value.errors_list)


@pytest.mark.django_db
def test_posting_with_inactive_account_raises(owner, cash, revenue):
    cash.is_active = False
    cash.save()
    entry = make_balanced_entry(
        debit_account=cash, credit_account=revenue, amount=Decimal("5.00"),
    )
    with pytest.raises(JournalEntryValidationError) as exc:
        post_entry(entry, user=owner)
    assert any("Inactive accounts" in e for e in exc.value.errors_list)
    assert any("1000" in e for e in exc.value.errors_list)


@pytest.mark.django_db
def test_multiple_errors_aggregate_into_errors_list(owner, cash, revenue):
    """<2 lines + inactive account + unbalanced — all surface together."""
    cash.is_active = False
    cash.save()
    entry = JournalEntryFactory()
    DebitLineFactory(journal_entry=entry, account=cash, debit_amount=Decimal("10.00"))
    # Only one line, which uses an inactive account, so three invariants fail:
    # (a) <2 lines, (b) inactive account, (c) 10.00 debits != 0 credits.
    with pytest.raises(JournalEntryValidationError) as exc:
        post_entry(entry, user=owner)
    reasons = exc.value.errors_list
    assert any("at least 2 lines" in r for r in reasons)
    assert any("Inactive" in r for r in reasons)
    assert any("do not equal" in r for r in reasons)


@pytest.mark.django_db
def test_validation_failure_does_not_partially_mutate(owner, cash, revenue):
    """Atomic: a failed post leaves status='draft' and posted_at=None."""
    entry = JournalEntryFactory()
    DebitLineFactory(journal_entry=entry, account=cash, debit_amount=Decimal("10.00"))
    CreditLineFactory(journal_entry=entry, account=revenue, credit_amount=Decimal("9.99"))
    with pytest.raises(JournalEntryValidationError):
        post_entry(entry, user=owner)
    entry.refresh_from_db()
    assert entry.status == JournalEntryStatus.DRAFT
    assert entry.posted_at is None
    assert AuditLog.objects.filter(entity_id=str(entry.pk), action="post_entry").count() == 0
