"""System-account protection rules — Account.clean() + pre_save signal.

Three rules, each tested at both the .full_clean() form-validation
surface and the Account.objects.create() raw-create surface (which goes
through the pre_save signal):

  1. is_system=True is only valid for Equity accounts.
  2. is_system is immutable after create.
  3. is_active=False is rejected on a system account.

JournalLine.account.on_delete=PROTECT (Group D) handles the hard-delete
case; that's covered by tests/integration/test_account_deletion_protection.py.
"""
from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from books.accounting.factories import (
    AccountFactory,
    EquityAccountFactory,
)
from books.accounting.models import Account, AccountType, NormalBalance

# --- Rule 1: is_system requires Equity type --------------------------------


@pytest.mark.django_db
def test_clean_rejects_system_flag_on_asset():
    """An Asset account cannot be is_system=True."""
    a = AccountFactory.build(
        account_number="9-9999",
        name="Bogus System Asset",
        type=AccountType.ASSET,
        normal_balance=NormalBalance.DEBIT,
        is_system=True,
    )
    with pytest.raises(ValidationError) as exc:
        a.full_clean()
    assert "is_system" in exc.value.message_dict


@pytest.mark.django_db
def test_create_rejects_system_flag_on_liability_via_signal():
    """The pre_save signal blocks raw create() with is_system=True on
    a non-Equity row."""
    with pytest.raises(ValidationError) as exc:
        Account.objects.create(
            account_number="9-9998",
            name="Bogus System Liability",
            type=AccountType.LIABILITY,
            normal_balance=NormalBalance.CREDIT,
            is_system=True,
        )
    assert "is_system" in exc.value.message_dict


@pytest.mark.django_db
def test_clean_accepts_system_flag_on_equity():
    """Positive case: an Equity account may be is_system=True."""
    a = EquityAccountFactory.build(
        account_number="3-9000",
        name="Owner's Equity",
        is_system=True,
        display_order=-300,
    )
    a.full_clean()  # does not raise


# --- Rule 2: is_system is immutable after create ---------------------------


@pytest.mark.django_db
def test_cannot_flip_is_system_from_false_to_true():
    """An ordinary user account can't be promoted to a system account."""
    user_acct = AccountFactory(
        account_number="3-0001",
        name="A User Equity Account",
        type=AccountType.EQUITY,
        normal_balance=NormalBalance.CREDIT,
    )
    user_acct.is_system = True
    with pytest.raises(ValidationError) as exc:
        user_acct.full_clean()
    assert "is_system" in exc.value.message_dict


@pytest.mark.django_db
def test_cannot_flip_is_system_from_true_to_false():
    """A system account can't be demoted to a user account."""
    sys_acct = EquityAccountFactory(
        account_number="3-9000",
        name="Owner's Equity",
        is_system=True,
        display_order=-300,
    )
    sys_acct.is_system = False
    with pytest.raises(ValidationError) as exc:
        sys_acct.full_clean()
    assert "is_system" in exc.value.message_dict


@pytest.mark.django_db
def test_signal_blocks_save_after_is_system_flip():
    """Saving directly (skipping full_clean) still trips the signal."""
    sys_acct = EquityAccountFactory(
        account_number="3-9100",
        name="Opening Balance Equity",
        is_system=True,
        display_order=-200,
    )
    sys_acct.is_system = False
    with pytest.raises(ValidationError):
        sys_acct.save()


@pytest.mark.django_db
def test_can_save_system_account_unchanged():
    """Saving a system account without changing is_system is fine
    (e.g., updating display_order through the admin)."""
    sys_acct = EquityAccountFactory(
        account_number="3-9000",
        name="Owner's Equity",
        is_system=True,
        display_order=-300,
    )
    sys_acct.display_order = -400
    sys_acct.save()
    sys_acct.refresh_from_db()
    assert sys_acct.display_order == -400
    assert sys_acct.is_system is True


# --- Rule 3: System accounts cannot be deactivated -------------------------


@pytest.mark.django_db
def test_clean_rejects_deactivating_system_account():
    sys_acct = EquityAccountFactory(
        account_number="3-9200",
        name="Retained Earnings",
        is_system=True,
        display_order=-100,
    )
    sys_acct.is_active = False
    with pytest.raises(ValidationError) as exc:
        sys_acct.full_clean()
    assert "is_active" in exc.value.message_dict


@pytest.mark.django_db
def test_signal_blocks_deactivating_system_account():
    """Direct save() also trips through the pre_save signal."""
    sys_acct = EquityAccountFactory(
        account_number="3-9200",
        name="Retained Earnings",
        is_system=True,
        display_order=-100,
    )
    sys_acct.is_active = False
    with pytest.raises(ValidationError):
        sys_acct.save()


@pytest.mark.django_db
def test_user_account_can_be_deactivated_normally():
    """Sanity: the new rule doesn't accidentally block ordinary deactivation."""
    user_acct = AccountFactory(
        account_number="1-0001",
        name="Cash",
        type=AccountType.ASSET,
        normal_balance=NormalBalance.DEBIT,
    )
    user_acct.is_active = False
    user_acct.full_clean()
    user_acct.save()
    user_acct.refresh_from_db()
    assert user_acct.is_active is False
