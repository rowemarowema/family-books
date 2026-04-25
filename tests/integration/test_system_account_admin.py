"""Admin form gating for system accounts.

Pairs with the .clean() rules in books/accounting/models.py:
- is_system is always readonly (declarative on AccountAdmin).
- is_active is readonly when the obj being edited is a system account
  (AccountAdmin.get_readonly_fields handles the per-row case).

If the field were merely .clean()-rejected without being removed from
the form, the user would see a validation error after submitting; this
test lock the friendlier "field is greyed out" behavior.
"""
from __future__ import annotations

import pytest
from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory

from books.accounting.admin import AccountAdmin
from books.accounting.factories import AccountFactory, EquityAccountFactory
from books.accounting.models import Account, AccountType, NormalBalance


def _stub_request():
    rf = RequestFactory()
    request = rf.get("/admin/accounting/account/1/change/")

    class _StubUser:
        is_active = True
        is_staff = True
        is_superuser = True

        def has_perm(self, perm, obj=None):
            return True

        def has_perms(self, perms, obj=None):
            return True

    request.user = _StubUser()
    return request


@pytest.mark.django_db
def test_is_active_is_readonly_for_system_account():
    sys_acct = EquityAccountFactory(
        account_number="3-9000",
        name="Owner's Equity",
        is_system=True,
        display_order=-300,
    )
    admin = AccountAdmin(Account, AdminSite())
    readonly = admin.get_readonly_fields(_stub_request(), obj=sys_acct)
    assert "is_system" in readonly
    assert "is_active" in readonly


@pytest.mark.django_db
def test_is_active_is_editable_for_user_account():
    user_acct = AccountFactory(
        account_number="1-0001",
        name="Cash",
        type=AccountType.ASSET,
        normal_balance=NormalBalance.DEBIT,
    )
    admin = AccountAdmin(Account, AdminSite())
    readonly = admin.get_readonly_fields(_stub_request(), obj=user_acct)
    assert "is_system" in readonly
    assert "is_active" not in readonly


@pytest.mark.django_db
def test_form_excludes_is_active_when_editing_system_account():
    """Functional check: the rendered admin form drops is_active from
    base_fields when editing a system account."""
    sys_acct = EquityAccountFactory(
        account_number="3-9100",
        name="Opening Balance Equity",
        is_system=True,
        display_order=-200,
    )
    admin = AccountAdmin(Account, AdminSite())
    form_class = admin.get_form(_stub_request(), obj=sys_acct, change=True)
    assert "is_active" not in form_class.base_fields
    assert "is_system" not in form_class.base_fields  # always readonly


@pytest.mark.django_db
def test_form_keeps_is_active_when_editing_user_account():
    user_acct = AccountFactory(
        account_number="2-0001",
        name="Trade Payable",
        type=AccountType.LIABILITY,
        normal_balance=NormalBalance.CREDIT,
    )
    admin = AccountAdmin(Account, AdminSite())
    form_class = admin.get_form(_stub_request(), obj=user_acct, change=True)
    assert "is_active" in form_class.base_fields
