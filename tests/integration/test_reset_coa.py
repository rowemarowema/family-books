"""Tests for `reset_coa` — refusal contract, system-preservation, audit row.

The behavior table (per refinement #1 in the Group E breakdown):

    Refuses if posted JournalEntry exists      -> CommandError
    Refuses if any JournalLine exists          -> CommandError
    Deletes user accounts (is_system=False)    -> via DELETE WHERE
    Preserves system accounts                  -> never touched
    AuditLog records deleted_user_account_count

Each row is exercised below. The reset → re-seed cycle is also covered
in test_seed_default_coa.py::test_reseed_after_system_only_state_succeeds.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from books.accounting.factories import (
    AccountFactory,
    EquityAccountFactory,
    JournalEntryFactory,
    DebitLineFactory,
    CreditLineFactory,
    make_balanced_entry,
)
from books.accounting.models import (
    Account,
    AccountType,
    JournalEntry,
    JournalEntryStatus,
    JournalLine,
    NormalBalance,
)
from books.accounting.posting import post_entry
from books.audit.models import AuditAction, AuditLog


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_minimal_coa(*, system_count: int = 1, user_count: int = 3) -> None:
    """Skip the full seed_default_coa; just create rows directly so each
    test is independent of loader behavior."""
    for i in range(system_count):
        EquityAccountFactory(
            account_number=f"3-90{i:02d}",
            name=f"System Account {i}",
            is_system=True,
            display_order=-300 + i,
        )
    for i in range(user_count):
        AccountFactory(
            account_number=f"1-{i:04d}",
            name=f"User Account {i}",
            type=AccountType.ASSET,
            normal_balance=NormalBalance.DEBIT,
        )


# ---------------------------------------------------------------------------
# Confirm-destroy flag is mandatory
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_command_requires_confirm_destroy_flag():
    with pytest.raises(CommandError):
        call_command("reset_coa")  # missing --confirm-destroy


@pytest.mark.django_db
def test_command_rejects_empty_confirm_destroy_reason():
    _seed_minimal_coa()
    with pytest.raises(CommandError) as exc:
        call_command("reset_coa", "--confirm-destroy", "   ")
    assert "non-empty" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# Refusal: posted JournalEntry exists
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_refuses_when_posted_entry_exists(owner):
    _seed_minimal_coa(user_count=2)
    cash = Account.objects.get(account_number="1-0000")
    rev = AccountFactory(
        account_number="4-0001",
        name="Revenue",
        type=AccountType.REVENUE,
        normal_balance=NormalBalance.CREDIT,
    )
    entry = make_balanced_entry(
        debit_account=cash, credit_account=rev, amount=Decimal("50.00")
    )
    post_entry(entry, user=owner, reason="test setup")

    with pytest.raises(CommandError) as exc:
        call_command("reset_coa", "--confirm-destroy", "test")
    assert "posted JournalEntry" in str(exc.value)
    # Nothing deleted.
    assert Account.objects.filter(is_system=False).count() == 3  # 2 + rev


@pytest.mark.django_db
def test_posted_entry_refusal_writes_audit_row(owner):
    _seed_minimal_coa(user_count=2)
    cash = Account.objects.get(account_number="1-0000")
    rev = AccountFactory(
        account_number="4-0001",
        name="Revenue",
        type=AccountType.REVENUE,
        normal_balance=NormalBalance.CREDIT,
    )
    entry = make_balanced_entry(
        debit_account=cash, credit_account=rev, amount=Decimal("50.00")
    )
    post_entry(entry, user=owner, reason="test setup")

    with pytest.raises(CommandError):
        call_command("reset_coa", "--confirm-destroy", "test")

    refused = AuditLog.objects.filter(action=AuditAction.COA_RESET_REFUSED)
    assert refused.count() == 1
    assert refused.get().after_value["posted_journal_entries"] == 1


# ---------------------------------------------------------------------------
# Refusal: JournalLine exists (defensive — covers draft entries too)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_refuses_when_draft_journal_lines_exist():
    """A draft entry with lines blocks reset (PROTECT would otherwise
    raise IntegrityError; we surface a friendlier CommandError)."""
    _seed_minimal_coa(user_count=2)
    cash = Account.objects.get(account_number="1-0000")
    other = Account.objects.get(account_number="1-0001")
    entry = JournalEntryFactory()
    DebitLineFactory(
        journal_entry=entry, account=cash, debit_amount=Decimal("10.00")
    )
    CreditLineFactory(
        journal_entry=entry, account=other, credit_amount=Decimal("10.00")
    )
    assert entry.status == JournalEntryStatus.DRAFT
    assert JournalLine.objects.count() == 2

    with pytest.raises(CommandError) as exc:
        call_command("reset_coa", "--confirm-destroy", "test")
    assert "JournalLine" in str(exc.value)


@pytest.mark.django_db
def test_journal_line_refusal_writes_audit_row():
    _seed_minimal_coa(user_count=2)
    cash = Account.objects.get(account_number="1-0000")
    other = Account.objects.get(account_number="1-0001")
    entry = JournalEntryFactory()
    DebitLineFactory(
        journal_entry=entry, account=cash, debit_amount=Decimal("10.00")
    )
    CreditLineFactory(
        journal_entry=entry, account=other, credit_amount=Decimal("10.00")
    )

    with pytest.raises(CommandError):
        call_command("reset_coa", "--confirm-destroy", "test")

    refused = AuditLog.objects.filter(action=AuditAction.COA_RESET_REFUSED)
    assert refused.count() == 1
    assert refused.get().after_value["journal_lines"] == 2


# ---------------------------------------------------------------------------
# Happy path: deletes user accounts, preserves system accounts
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_deletes_only_user_accounts():
    _seed_minimal_coa(system_count=3, user_count=5)
    assert Account.objects.count() == 8

    call_command("reset_coa", "--confirm-destroy", "approval test")

    assert Account.objects.count() == 3
    assert Account.objects.filter(is_system=False).count() == 0
    assert Account.objects.filter(is_system=True).count() == 3


@pytest.mark.django_db
def test_audit_row_records_deleted_user_account_count():
    _seed_minimal_coa(system_count=2, user_count=7)

    call_command("reset_coa", "--confirm-destroy", "approval test")

    audits = AuditLog.objects.filter(action=AuditAction.COA_RESET)
    assert audits.count() == 1
    audit = audits.get()
    assert audit.after_value["deleted_user_account_count"] == 7
    assert audit.reason == "approval test"


@pytest.mark.django_db
def test_preserves_system_account_attributes_unchanged():
    """is_system, display_order, name, normal_balance — none should mutate."""
    _seed_minimal_coa(system_count=1, user_count=2)
    sys_before = Account.objects.get(is_system=True)
    snapshot = {
        "account_number": sys_before.account_number,
        "name": sys_before.name,
        "type": sys_before.type,
        "normal_balance": sys_before.normal_balance,
        "is_system": sys_before.is_system,
        "is_active": sys_before.is_active,
        "display_order": sys_before.display_order,
    }

    call_command("reset_coa", "--confirm-destroy", "test")

    sys_after = Account.objects.get(pk=sys_before.pk)
    for k, v in snapshot.items():
        assert getattr(sys_after, k) == v, f"{k} changed: {v} -> {getattr(sys_after, k)}"


@pytest.mark.django_db
def test_reset_then_seed_round_trips(tmp_path):
    """The full reset → seed cycle: clear user data, re-seed via the loader."""
    import json
    fixture_data = {
        "metadata": {"description": "minimal"},
        "system_accounts": [
            {
                "account_number": "3-9000",
                "name": "Owner's Equity",
                "full_path": "Owner's Equity",
                "type": "Equity",
                "normal_balance": "Credit",
                "parent_account_number": None,
                "is_active": True,
                "is_system": True,
                "display_order": -300,
                "description": None,
                "tax_category": None,
            },
        ],
        "accounts": [
            {
                "account_number": "1-0001",
                "name": "Cash",
                "full_path": "Cash",
                "type": "Asset",
                "normal_balance": "Debit",
                "parent_account_number": None,
                "is_active": True,
                "is_system": False,
                "display_order": 0,
                "description": None,
                "tax_category": None,
            },
        ],
    }
    fix = tmp_path / "coa.json"
    fix.write_text(json.dumps(fixture_data), encoding="utf-8")

    call_command("seed_default_coa", "--fixture", str(fix))
    assert Account.objects.count() == 2

    call_command("reset_coa", "--confirm-destroy", "drill")
    assert Account.objects.count() == 1
    assert Account.objects.filter(is_system=True).count() == 1

    call_command("seed_default_coa", "--fixture", str(fix))
    assert Account.objects.count() == 2
    assert Account.objects.filter(is_system=True).count() == 1
