"""Account hierarchy depth + normal_balance/type consistency."""
from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from books.accounting.models import Account, AccountType, NormalBalance


# ---------------------------------------------------------------------------
# Hierarchy depth
# ---------------------------------------------------------------------------


def _make_asset(number: str, name: str, parent: Account | None = None) -> Account:
    return Account(
        account_number=number,
        name=name,
        type=AccountType.ASSET,
        normal_balance=NormalBalance.DEBIT,
        parent_account=parent,
    )


@pytest.mark.django_db
def test_4_levels_save_ok_via_full_clean():
    a = _make_asset("1000", "Level1")
    a.full_clean()
    a.save()
    b = _make_asset("1100", "Level2", parent=a)
    b.full_clean()
    b.save()
    c = _make_asset("1110", "Level3", parent=b)
    c.full_clean()
    c.save()
    d = _make_asset("1111", "Level4", parent=c)
    d.full_clean()
    d.save()
    assert Account.objects.count() == 4


@pytest.mark.django_db
def test_5th_level_rejected_via_full_clean():
    a = _make_asset("1000", "Level1")
    a.full_clean(); a.save()
    b = _make_asset("1100", "Level2", parent=a)
    b.full_clean(); b.save()
    c = _make_asset("1110", "Level3", parent=b)
    c.full_clean(); c.save()
    d = _make_asset("1111", "Level4", parent=c)
    d.full_clean(); d.save()
    e = _make_asset("1112", "Level5", parent=d)
    with pytest.raises(ValidationError) as exc:
        e.full_clean()
    assert "4 levels" in str(exc.value)


@pytest.mark.django_db
def test_4_levels_save_ok_via_raw_create_signal_path():
    """Account.objects.create() skips full_clean() — only the pre_save
    signal enforces hierarchy depth on this path."""
    a = Account.objects.create(
        account_number="2000", name="Level1",
        type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
    )
    b = Account.objects.create(
        account_number="2100", name="Level2", parent_account=a,
        type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
    )
    c = Account.objects.create(
        account_number="2110", name="Level3", parent_account=b,
        type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
    )
    d = Account.objects.create(
        account_number="2111", name="Level4", parent_account=c,
        type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
    )
    assert Account.objects.count() == 4


@pytest.mark.django_db
def test_5th_level_rejected_via_raw_create_signal_path():
    a = Account.objects.create(
        account_number="3000", name="Level1",
        type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
    )
    b = Account.objects.create(
        account_number="3100", name="Level2", parent_account=a,
        type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
    )
    c = Account.objects.create(
        account_number="3110", name="Level3", parent_account=b,
        type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
    )
    d = Account.objects.create(
        account_number="3111", name="Level4", parent_account=c,
        type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
    )
    with pytest.raises(ValidationError) as exc:
        Account.objects.create(
            account_number="3112", name="Level5", parent_account=d,
            type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
        )
    assert "4 levels" in str(exc.value)


# ---------------------------------------------------------------------------
# Normal-balance / type consistency
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    "acct_type,normal_balance,should_pass",
    [
        (AccountType.ASSET, NormalBalance.DEBIT, True),
        (AccountType.ASSET, NormalBalance.CREDIT, False),
        (AccountType.EXPENSE, NormalBalance.DEBIT, True),
        (AccountType.EXPENSE, NormalBalance.CREDIT, False),
        (AccountType.LIABILITY, NormalBalance.CREDIT, True),
        (AccountType.LIABILITY, NormalBalance.DEBIT, False),
        (AccountType.EQUITY, NormalBalance.CREDIT, True),
        (AccountType.EQUITY, NormalBalance.DEBIT, False),
        (AccountType.REVENUE, NormalBalance.CREDIT, True),
        (AccountType.REVENUE, NormalBalance.DEBIT, False),
    ],
)
def test_normal_balance_consistency(acct_type, normal_balance, should_pass):
    acct = Account(
        account_number="9000",
        name="Test",
        type=acct_type,
        normal_balance=normal_balance,
    )
    if should_pass:
        acct.full_clean()  # should not raise
    else:
        with pytest.raises(ValidationError):
            acct.full_clean()
