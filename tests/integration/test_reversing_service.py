"""reverse_entry service — happy path, cycle protection, DB uniqueness."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.db import IntegrityError, connection, transaction

from books.accounting.exceptions import (
    AlreadyReversed,
    CannotReverseAReversal,
    PostedEntryImmutable,
)
from books.accounting.factories import (
    AccountFactory,
    RevenueAccountFactory,
    make_balanced_entry,
)
from books.accounting.models import JournalEntryStatus
from books.accounting.posting import post_entry, reverse_entry
from books.audit.models import AuditLog


@pytest.fixture
def cash(db):
    return AccountFactory(account_number="1000", name="Cash")


@pytest.fixture
def revenue(db):
    return RevenueAccountFactory(account_number="4000", name="Sales Revenue")


@pytest.fixture
def posted_entry(owner, cash, revenue):
    entry = make_balanced_entry(
        debit_account=cash,
        credit_account=revenue,
        amount=Decimal("100.00"),
    )
    return post_entry(entry, user=owner, reason="original")


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_reverse_creates_mirror_entry(owner, posted_entry):
    reversal = reverse_entry(posted_entry, user=owner, reason="data entry error")

    assert reversal.status == JournalEntryStatus.POSTED
    assert reversal.reversing_entry_id == posted_entry.pk
    assert reversal.memo == f"Reversal of entry #{posted_entry.pk}: data entry error"

    original_lines = list(posted_entry.lines.order_by("id"))
    reversal_lines = list(reversal.lines.order_by("id"))

    assert len(reversal_lines) == len(original_lines)
    for orig, rev in zip(original_lines, reversal_lines):
        assert rev.account_id == orig.account_id
        assert rev.debit_amount == orig.credit_amount
        assert rev.credit_amount == orig.debit_amount
        assert rev.memo == orig.memo  # preserved verbatim (refinement #2)


@pytest.mark.django_db
def test_reverse_writes_both_post_and_reverse_audit_rows(owner, posted_entry):
    reversal = reverse_entry(
        posted_entry, user=owner, reason="reversal audit rationale",
    )
    # post_entry AuditLog for the reversal entry itself.
    assert AuditLog.objects.filter(
        action="post_entry", entity_id=str(reversal.pk),
    ).exists()
    # reverse_entry AuditLog with reason verbatim in its own field.
    reverse_audit = AuditLog.objects.get(
        action="reverse_entry", entity_id=str(reversal.pk),
    )
    assert reverse_audit.reason == "reversal audit rationale"
    assert reverse_audit.after_value["original_entry_id"] == posted_entry.pk
    assert reverse_audit.after_value["reversal_entry_id"] == reversal.pk
    assert reverse_audit.user == owner


@pytest.mark.django_db
def test_reverse_defaults_to_today(owner, posted_entry):
    from django.utils import timezone
    reversal = reverse_entry(posted_entry, user=owner, reason="r")
    assert reversal.entry_date == timezone.localdate()
    assert reversal.posting_date == timezone.localdate()


@pytest.mark.django_db
def test_reverse_with_explicit_as_of_honored(owner, posted_entry):
    as_of = date(2025, 12, 31)
    reversal = reverse_entry(posted_entry, user=owner, reason="r", as_of=as_of)
    assert reversal.entry_date == as_of
    assert reversal.posting_date == as_of


# ---------------------------------------------------------------------------
# Rejection branches (cycle protection)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_reverse_draft_entry_raises(owner, cash, revenue):
    draft = make_balanced_entry(
        debit_account=cash, credit_account=revenue, amount=Decimal("10.00"),
    )
    assert draft.status == JournalEntryStatus.DRAFT
    with pytest.raises(PostedEntryImmutable):
        reverse_entry(draft, user=owner, reason="r")


@pytest.mark.django_db
def test_cannot_reverse_a_reversal(owner, posted_entry):
    reversal = reverse_entry(posted_entry, user=owner, reason="r1")
    with pytest.raises(CannotReverseAReversal):
        reverse_entry(reversal, user=owner, reason="r2")


@pytest.mark.django_db
def test_cannot_double_reverse(owner, posted_entry):
    reverse_entry(posted_entry, user=owner, reason="r1")
    with pytest.raises(AlreadyReversed):
        reverse_entry(posted_entry, user=owner, reason="r2")


# ---------------------------------------------------------------------------
# DB-level uniqueness catches racing double-reversal (defense in depth)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_db_uniqueness_blocks_two_reversals_pointing_at_same_original(
    owner, posted_entry,
):
    """If two transactions race to insert a reversal for the same original
    and both bypass the Python pre-check, the partial unique index
    `one_reversal_per_original` rejects the second insert."""
    # Insert the first reversal via raw SQL (mimicking a racing concurrent
    # transaction that already committed).
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO journal_entry
                (entry_date, posting_date, memo, source, reference_number,
                 status, created_at, reversing_entry_id)
            VALUES (%s, %s, '', 'system', '', 'draft', now(), %s)
            """,
            [date.today(), date.today(), posted_entry.pk],
        )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO journal_entry
                        (entry_date, posting_date, memo, source, reference_number,
                         status, created_at, reversing_entry_id)
                    VALUES (%s, %s, '', 'system', '', 'draft', now(), %s)
                    """,
                    [date.today(), date.today(), posted_entry.pk],
                )
