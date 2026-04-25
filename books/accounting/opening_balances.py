"""
Opening-balance service: posts a balanced JE that establishes an
account's starting balance, with the offset to the system "Opening
Balance Equity" account (3-9100, seeded by Group E).

Single public entry point: `set_opening_balance()`. The function builds
a 2-line draft entry, posts it via the Group D `post_entry()` service
(never bypassed), updates the account's display fields
(`opening_balance` / `opening_balance_date`), and writes an AuditLog
row — all in one `transaction.atomic()`.

Reference number convention: every opening JE uses
    reference_number = f"OB:{account_number}:{as_of.isoformat()}"
which is unique per (account, as_of) and is the basis for the
duplicate-refusal check.

Period-close interaction (ADR-006, deferred to Stage 2):
    `_check_period_open(as_of)` is wired in but no-ops today because
    the FiscalPeriod model doesn't exist yet. Stage 2 activates the
    gate by replacing the stub body with the actual query. The
    activation site is marked with `# TODO(stage-2)` so it's
    discoverable.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import transaction

from books.accounting.exceptions import OpeningBalanceError
from books.accounting.models import (
    Account,
    AccountType,
    JournalEntry,
    JournalEntrySource,
    JournalLine,
    NormalBalance,
)
from books.accounting.posting import post_entry, reverse_entry

if TYPE_CHECKING:  # pragma: no cover
    from django.contrib.auth.models import AbstractUser

ZERO = Decimal("0.00")
OPENING_BALANCE_EQUITY_ACCOUNT_NUMBER = "3-9100"


@dataclass(frozen=True)
class OpeningBalanceResult:
    """Returned by set_opening_balance(); carries the posted entry +
    metadata about which side received the user's amount.

    Exactly one of (debit_amount, credit_amount) on the *target* account
    is non-zero; the other is the offset to Opening Balance Equity.
    """

    journal_entry: JournalEntry
    account: Account
    debit_amount: Decimal
    credit_amount: Decimal


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def set_opening_balance(
    account: Account,
    *,
    amount: Decimal,
    as_of: date,
    user: "AbstractUser",
    reason: str = "Opening balance",
    offset_account: Account | None = None,
) -> OpeningBalanceResult:
    """Post a 2-line balanced JE establishing `account`'s opening balance
    as of `as_of`.

    Sign convention: `amount` is always positive. The service routes
    it to debit or credit per `account.normal_balance`; the offset
    flows to `offset_account` (default: system 3-9100 Opening Balance
    Equity).

    Refusal contract — raises OpeningBalanceError on:
        - account.is_system            (system accounts hold the offset)
        - account.type ∉ {Asset, Liability, Equity}
                                       (Revenue/Expense don't carry forward)
        - amount <= 0                  (positive only)
        - duplicate (account, as_of)   (existing OB JE present)
        - period closed                (Stage 2 stub — currently no-op)

    Each refusal writes an OPENING_BALANCE_REFUSED audit row before
    raising so the audit trail captures attempted writes.

    On success: writes one OPENING_BALANCE_SET audit row, updates
    `account.opening_balance` / `opening_balance_date` for display.
    All side effects in a single transaction.atomic().
    """
    # Late import — keeps this module importable before audit migrations run.
    from books.audit.models import AuditAction, AuditLog

    if account.is_system:
        _audit_refusal(
            account=account,
            amount=amount,
            as_of=as_of,
            user=user,
            why="account.is_system",
        )
        raise OpeningBalanceError(
            f"Account {account.account_number!r} is a system account; "
            "system accounts hold the offset side, not user-entered "
            "opening balances."
        )

    if account.type not in {
        AccountType.ASSET,
        AccountType.LIABILITY,
        AccountType.EQUITY,
    }:
        _audit_refusal(
            account=account, amount=amount, as_of=as_of, user=user,
            why=f"account.type={account.type}",
        )
        raise OpeningBalanceError(
            f"Account {account.account_number!r} type is {account.type!r}; "
            "opening balances are only valid for Asset, Liability, and "
            "Equity (carry-forward types). Revenue and Expense reset "
            "every fiscal year and don't take an opening balance."
        )

    if amount is None or amount <= ZERO:
        _audit_refusal(
            account=account, amount=amount, as_of=as_of, user=user,
            why=f"amount={amount}",
        )
        raise OpeningBalanceError(
            f"Opening balance amount must be a positive Decimal; got {amount!r}. "
            "Sign is implied by the account's normal_balance."
        )

    _check_period_open(as_of)

    reference_number = _opening_reference(account, as_of)
    # An opening JE is "active" for this (account, as_of) if it exists
    # AND has no reversal pointing to it. Reversing the original
    # un-blocks re-posting at the same as_of — the contract is
    # "idempotent only via the explicit reverse + re-post path."
    active_existing = JournalEntry.objects.filter(
        reference_number=reference_number,
        reversed_by__isnull=True,
    ).exists()
    if active_existing:
        _audit_refusal(
            account=account, amount=amount, as_of=as_of, user=user,
            why="duplicate (account, as_of)",
        )
        raise OpeningBalanceError(
            f"An opening JE already exists for ({account.account_number}, "
            f"{as_of.isoformat()}). Reverse the existing entry via "
            "reverse_opening_balance() before re-posting."
        )

    if offset_account is None:
        try:
            offset_account = Account.objects.get(
                account_number=OPENING_BALANCE_EQUITY_ACCOUNT_NUMBER,
                is_system=True,
            )
        except Account.DoesNotExist as exc:
            raise OpeningBalanceError(
                f"Default offset account "
                f"{OPENING_BALANCE_EQUITY_ACCOUNT_NUMBER!r} not found. "
                "Run `seed_default_coa` to populate the system accounts."
            ) from exc

    # Decide which side of the JE receives `amount` based on the target
    # account's normal_balance. Asset/Expense (debit-normal) take a
    # debit on the target with the offset crediting OBE; Liability/
    # Equity/Revenue (credit-normal) flip both sides.
    if account.normal_balance == NormalBalance.DEBIT:
        debit_account, credit_account = account, offset_account
        target_debit, target_credit = amount, ZERO
    else:
        debit_account, credit_account = offset_account, account
        target_debit, target_credit = ZERO, amount

    with transaction.atomic():
        entry = JournalEntry.objects.create(
            entry_date=as_of,
            posting_date=as_of,
            memo=reason,
            source=JournalEntrySource.MANUAL,
            reference_number=reference_number,
            created_by=user,
        )
        JournalLine.objects.create(
            journal_entry=entry,
            account=debit_account,
            debit_amount=amount,
            credit_amount=ZERO,
        )
        JournalLine.objects.create(
            journal_entry=entry,
            account=credit_account,
            debit_amount=ZERO,
            credit_amount=amount,
        )
        post_entry(entry, user=user, reason=reason)

        # Refresh from DB to pick up posted_at + status flip from
        # post_entry, which mutates `entry` in place.
        entry.refresh_from_db()

        # Update display fields. These are convenience metadata — the
        # JE is the source of truth for the engine's balance computation.
        account.opening_balance = amount
        account.opening_balance_date = as_of
        account.save(update_fields=["opening_balance", "opening_balance_date"])

        AuditLog.record(
            entity_type="Account",
            entity_id=account.pk,
            action=AuditAction.OPENING_BALANCE_SET,
            user=user,
            after={
                "account_number": account.account_number,
                "amount": str(amount),
                "as_of": as_of.isoformat(),
                "journal_entry_id": entry.pk,
                "reference_number": reference_number,
            },
            reason=reason,
        )

    return OpeningBalanceResult(
        journal_entry=entry,
        account=account,
        debit_amount=target_debit,
        credit_amount=target_credit,
    )


def reverse_opening_balance(
    result: OpeningBalanceResult,
    *,
    user: "AbstractUser",
    reason: str,
    as_of: date | None = None,
) -> JournalEntry:
    """Reverse the opening JE by delegating to Group D's reverse_entry.

    Required for the "I entered the wrong amount" path. After reversal,
    `set_opening_balance()` for the same (account, as_of) becomes
    permissible again — the duplicate-refusal check looks at
    JournalEntry.reference_number, and the reversal carries a different
    reference_number.

    Note that the static fields (`account.opening_balance`,
    `opening_balance_date`) are NOT reset here — the caller decides
    whether to clear them or leave them pointing at the historical
    intent. The trial-balance engine reads from posted JEs, not from
    these fields, so display-only divergence doesn't affect totals.
    """
    return reverse_entry(
        result.journal_entry,
        user=user,
        reason=reason,
        as_of=as_of or result.journal_entry.posting_date,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _opening_reference(account: Account, as_of: date) -> str:
    return f"OB:{account.account_number}:{as_of.isoformat()}"


def _audit_refusal(
    *,
    account: Account,
    amount: Decimal | None,
    as_of: date,
    user: "AbstractUser",
    why: str,
) -> None:
    from books.audit.models import AuditAction, AuditLog

    AuditLog.record(
        entity_type="Account",
        entity_id=account.pk,
        action=AuditAction.OPENING_BALANCE_REFUSED,
        user=user,
        reason=why,
        after={
            "account_number": account.account_number,
            "amount": str(amount) if amount is not None else None,
            "as_of": as_of.isoformat(),
            "why": why,
        },
    )


def _check_period_open(as_of: date) -> None:
    """Period-close gate. Stage 1 stub; Stage 2 wires the actual check.

    The FiscalPeriod model lands in Stage 2 (period close + reopen).
    Until then, this is a deliberate no-op. The activation site is
    marked with TODO(stage-2) so the period-close work in Stage 2
    can find it without grepping the whole codebase.

    See ADR-006 in docs/DECISIONS.md for the deferred design.
    """
    # TODO(stage-2): activate this check when books.periods.FiscalPeriod
    # exists. The intended behavior:
    #
    #     from books.periods.models import FiscalPeriod, FiscalPeriodStatus
    #     overlapping_closed = FiscalPeriod.objects.filter(
    #         status=FiscalPeriodStatus.CLOSED,
    #         end_date__gte=as_of,
    #     ).exists()
    #     if overlapping_closed:
    #         raise OpeningBalanceError(
    #             f"Cannot set opening balance as_of {as_of}; one or more "
    #             "closed fiscal periods cover or follow that date. Reopen "
    #             "the period first."
    #         )
    return None
