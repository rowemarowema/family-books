"""Accounting-engine exception types.

Every public service in books.accounting raises one of these for
business-rule violations. They subclass ValidationError so Django forms
and the admin surface them with friendly errors; callers can still catch
the specific types.
"""
from __future__ import annotations

from django.core.exceptions import ValidationError


class AccountingError(ValidationError):
    """Base for every accounting-engine business-rule exception."""


class JournalEntryValidationError(AccountingError):
    """A JournalEntry failed pre-post validation (sum imbalance, <2 lines, etc.).

    Attributes:
        errors: list of human-readable reasons.
        entry_id: the entry's pk, or None if unsaved.
    """

    def __init__(self, errors: list[str], entry_id: int | None = None) -> None:
        self.errors_list = list(errors)
        self.entry_id = entry_id
        super().__init__("; ".join(errors))


class PostedEntryImmutable(AccountingError):
    """A save/delete was attempted on a JournalEntry or JournalLine that is
    part of a posted entry. Posted entries are append-only; corrections
    require reverse_entry, not in-place mutation."""


class AlreadyReversed(AccountingError):
    """An attempt was made to reverse an entry that already has a reversal.
    Each original entry can have at most one reversal."""


class CannotReverseAReversal(AccountingError):
    """An attempt was made to reverse an entry that is itself a reversal.
    Reversal chains are not allowed; to undo a reversal, post a corrective
    new entry instead."""


class OpeningBalanceError(AccountingError):
    """An attempt to set an opening balance was rejected.

    Reasons (the message text disambiguates):
      - account.is_system: the 3 system equity accounts hold the offset
        side, not user-entered balances.
      - account.type in (Revenue, Expense): only carry-forward types
        (Asset / Liability / Equity) get opening balances.
      - amount == 0: nothing to post.
      - duplicate (account, as_of): an opening JE already exists for
        this pair; reverse it before re-posting.
      - period closed: the as_of date falls inside a closed period.
        (Stage 2 wires the activation site; today, no FiscalPeriod
        model exists and the check trivially passes.)
    """
