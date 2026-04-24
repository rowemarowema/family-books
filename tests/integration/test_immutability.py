"""Posted entries are immutable — Python overrides + Postgres triggers."""
from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.utils import InternalError

from books.accounting.exceptions import PostedEntryImmutable
from books.accounting.factories import (
    AccountFactory,
    RevenueAccountFactory,
    make_balanced_entry,
)
from books.accounting.models import JournalEntry, JournalEntryStatus, JournalLine
from books.accounting.posting import post_entry


@pytest.fixture
def cash(db):
    return AccountFactory(account_number="1000", name="Cash")


@pytest.fixture
def revenue(db):
    return RevenueAccountFactory(account_number="4000", name="Sales Revenue")


@pytest.fixture
def posted_entry(owner, cash, revenue):
    entry = make_balanced_entry(
        debit_account=cash, credit_account=revenue, amount=Decimal("100.00"),
    )
    return post_entry(entry, user=owner, reason="immutability test setup")


# ---------------------------------------------------------------------------
# Python overrides
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_save_on_posted_entry_raises(posted_entry):
    posted_entry.memo = "changed"
    with pytest.raises(PostedEntryImmutable):
        posted_entry.save()


@pytest.mark.django_db
def test_delete_on_posted_entry_raises(posted_entry):
    with pytest.raises(PostedEntryImmutable):
        posted_entry.delete()


@pytest.mark.django_db
def test_save_on_line_of_posted_entry_raises(posted_entry):
    line = posted_entry.lines.first()
    line.memo = "changed"
    with pytest.raises(PostedEntryImmutable):
        line.save()


@pytest.mark.django_db
def test_delete_line_of_posted_entry_raises(posted_entry):
    line = posted_entry.lines.first()
    with pytest.raises(PostedEntryImmutable):
        line.delete()


# ---------------------------------------------------------------------------
# DB triggers catch ORM paths that bypass save()
# ---------------------------------------------------------------------------


_TRIGGER_EXCEPTIONS = (IntegrityError, InternalError)


@pytest.mark.django_db
def test_queryset_update_on_posted_entry_blocked_by_trigger(posted_entry):
    with pytest.raises(_TRIGGER_EXCEPTIONS):
        with transaction.atomic():
            JournalEntry.objects.filter(pk=posted_entry.pk).update(memo="bypass")


@pytest.mark.django_db
def test_queryset_update_on_line_of_posted_entry_blocked_by_trigger(posted_entry):
    line = posted_entry.lines.first()
    with pytest.raises(_TRIGGER_EXCEPTIONS):
        with transaction.atomic():
            JournalLine.objects.filter(pk=line.pk).update(debit_amount=Decimal("999.99"))


@pytest.mark.django_db
def test_raw_sql_update_on_posted_line_blocked_by_trigger(posted_entry):
    line = posted_entry.lines.first()
    with pytest.raises(_TRIGGER_EXCEPTIONS):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE journal_line SET debit_amount = 9999 WHERE id = %s",
                    [line.pk],
                )


@pytest.mark.django_db
def test_insert_new_line_into_posted_entry_blocked_by_trigger(posted_entry, cash):
    """Posted entries are append-only — no adding lines after post."""
    with pytest.raises(_TRIGGER_EXCEPTIONS):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO journal_line
                        (journal_entry_id, account_id, debit_amount, credit_amount, memo)
                    VALUES (%s, %s, 1, 0, 'smuggled')
                    """,
                    [posted_entry.pk, cash.pk],
                )


@pytest.mark.django_db
def test_raw_sql_delete_on_posted_entry_blocked_by_trigger(posted_entry):
    with pytest.raises(_TRIGGER_EXCEPTIONS):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM journal_entry WHERE id = %s",
                    [posted_entry.pk],
                )


# ---------------------------------------------------------------------------
# Draft -> Posted transition IS permitted (negative guard against over-blocking)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_draft_to_posted_transition_is_permitted(owner, cash, revenue):
    """post_entry must NOT be blocked by our immutability layer. It flips
    status draft → posted with a single save; old_status in DB is still
    'draft' at the moment of the save."""
    entry = make_balanced_entry(
        debit_account=cash, credit_account=revenue, amount=Decimal("5.00"),
    )
    posted = post_entry(entry, user=owner, reason="sanity")
    assert posted.status == JournalEntryStatus.POSTED
