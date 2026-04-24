"""
Posting and reversing services.

The single entry point for moving a JournalEntry from draft → posted is
post_entry(). It validates the full set of accounting invariants, writes
an AuditLog row, and flips status + posted_at atomically.

reverse_entry() creates a mirror-image JournalEntry whose lines swap
debit ↔ credit relative to the original, links it back to the original
via reversing_entry_id, and posts it in one call.

Both functions assume they're invoked with an authenticated user — that
user is stamped on the created_by and AuditLog rows for traceability.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from books.accounting.exceptions import (
    AlreadyReversed,
    CannotReverseAReversal,
    JournalEntryValidationError,
    PostedEntryImmutable,
)
from books.accounting.models import (
    JournalEntry,
    JournalEntrySource,
    JournalEntryStatus,
    JournalLine,
)
from books.audit.models import AuditLog

ZERO = Decimal("0.00")


# ---------------------------------------------------------------------------
# post_entry
# ---------------------------------------------------------------------------


@transaction.atomic
def post_entry(entry: JournalEntry, *, user, reason: str = "") -> JournalEntry:
    """Post a draft entry.

    Args:
        entry: the JournalEntry to post. Must currently have status='draft'
            and at least two JournalLine rows.
        user: the authenticated user performing the posting. Stamped on
            the AuditLog row.
        reason: optional caller-supplied reason; stored verbatim in
            AuditLog.reason.

    Returns:
        The same entry instance, refreshed from DB with status='posted'
        and posted_at set.

    Raises:
        PostedEntryImmutable:          entry.status is not 'draft'.
        JournalEntryValidationError:   one or more invariants failed.
                                       .errors_list carries human-readable
                                       reasons.
    """
    if entry.status != JournalEntryStatus.DRAFT:
        raise PostedEntryImmutable(
            f"JournalEntry #{entry.pk} is {entry.status!r}; only draft entries can be posted."
        )

    errors: list[str] = []
    lines = list(entry.lines.select_related("account").all())

    if len(lines) < 2:
        errors.append(f"Entry must have at least 2 lines (has {len(lines)}).")

    inactive = [line for line in lines if not line.account.is_active]
    if inactive:
        numbers = ", ".join(sorted({line.account.account_number for line in inactive}))
        errors.append(f"Inactive accounts cannot be posted to: {numbers}.")

    total_debits = sum((line.debit_amount for line in lines), start=ZERO)
    total_credits = sum((line.credit_amount for line in lines), start=ZERO)
    if total_debits != total_credits:
        errors.append(
            f"Debits ({total_debits}) do not equal credits ({total_credits})."
        )

    if errors:
        raise JournalEntryValidationError(errors=errors, entry_id=entry.pk)

    now = timezone.now()
    entry.status = JournalEntryStatus.POSTED
    entry.posted_at = now
    entry.save(update_fields=["status", "posted_at"])

    AuditLog.record(
        entity_type="JournalEntry",
        entity_id=entry.pk,
        action="post_entry",
        user=user,
        before={"status": JournalEntryStatus.DRAFT.value},
        after=_snapshot_after_post(entry, lines, total_debits, total_credits),
        reason=reason,
    )

    return entry


# ---------------------------------------------------------------------------
# reverse_entry
# ---------------------------------------------------------------------------


@transaction.atomic
def reverse_entry(
    original: JournalEntry,
    *,
    user,
    reason: str,
    as_of: date | None = None,
) -> JournalEntry:
    """Create and post a reversal of a previously-posted entry.

    Args:
        original: the posted entry to reverse. Must be status='posted' and
            must not itself be a reversal.
        user: the authenticated user.
        reason: required; explains why the reversal is being posted.
            Stored in AuditLog.reason and embedded in the reversal memo.
        as_of: the date to use for entry_date and posting_date on the
            new reversal entry. Defaults to today. Caller may back-date
            into an open period; period-close enforcement lives in
            Group E's transactional layer and will gate as_of then.

    Returns:
        The newly created and posted reversal entry.

    Raises:
        PostedEntryImmutable:       original is not in 'posted' state.
        CannotReverseAReversal:     original is itself a reversal
                                    (reversing_entry_id is set).
        AlreadyReversed:            a reversal for `original` already
                                    exists.
    """
    if original.status != JournalEntryStatus.POSTED:
        raise PostedEntryImmutable(
            f"JournalEntry #{original.pk} has status "
            f"{original.status!r}; only posted entries can be reversed."
        )
    if original.reversing_entry_id is not None:
        raise CannotReverseAReversal(
            f"JournalEntry #{original.pk} is itself a reversal of "
            f"#{original.reversing_entry_id}; reversal chains are not allowed."
        )
    if JournalEntry.objects.filter(reversing_entry_id=original.pk).exists():
        raise AlreadyReversed(
            f"JournalEntry #{original.pk} already has a reversal."
        )

    as_of = as_of or timezone.localdate()

    reversal = JournalEntry.objects.create(
        entry_date=as_of,
        posting_date=as_of,
        memo=f"Reversal of entry #{original.pk}: {reason}",
        source=JournalEntrySource.SYSTEM,
        reference_number=original.reference_number,
        status=JournalEntryStatus.DRAFT,
        created_by=user if getattr(user, "pk", None) is not None else None,
        reversing_entry=original,
    )

    # Mirror each original line: swap debit ↔ credit, preserve account and memo.
    original_lines = list(original.lines.select_related("account").order_by("id"))
    for line in original_lines:
        JournalLine.objects.create(
            journal_entry=reversal,
            account=line.account,
            debit_amount=line.credit_amount,
            credit_amount=line.debit_amount,
            memo=line.memo,  # preserved verbatim
        )

    # Post via the shared posting service, with an embedded reason that
    # points at the original.
    post_entry(
        reversal,
        user=user,
        reason=f"Reversal of JE #{original.pk}: {reason}",
    )

    # Second AuditLog row specifically for the reversal action (post_entry
    # already wrote a 'post_entry' row; this one captures the reversal
    # semantics explicitly).
    reversal_lines = list(reversal.lines.select_related("account").order_by("id"))
    AuditLog.record(
        entity_type="JournalEntry",
        entity_id=reversal.pk,
        action="reverse_entry",
        user=user,
        before=None,
        after={
            "original_entry_id": original.pk,
            "reversal_entry_id": reversal.pk,
            "as_of": as_of.isoformat(),
            "lines": [_line_snapshot(line) for line in reversal_lines],
        },
        reason=reason,
    )

    return reversal


# ---------------------------------------------------------------------------
# Snapshot helpers — produce the AuditLog JSON payloads described in the
# Group D design doc.
# ---------------------------------------------------------------------------


def _snapshot_after_post(
    entry: JournalEntry,
    lines: list[JournalLine],
    total_debits: Decimal,
    total_credits: Decimal,
) -> dict:
    return {
        "status": JournalEntryStatus.POSTED.value,
        "entry_date": entry.entry_date.isoformat(),
        "posting_date": entry.posting_date.isoformat(),
        "source": entry.source,
        "reference_number": entry.reference_number,
        "memo": entry.memo,
        "reversing_entry_id": entry.reversing_entry_id,
        "lines": [_line_snapshot(line) for line in lines],
        "totals": {
            "debits": str(total_debits),
            "credits": str(total_credits),
        },
    }


def _line_snapshot(line: JournalLine) -> dict:
    return {
        "account_number": line.account.account_number,
        "account_name": line.account.name,
        "debit": str(line.debit_amount),
        "credit": str(line.credit_amount),
        "memo": line.memo,
    }
