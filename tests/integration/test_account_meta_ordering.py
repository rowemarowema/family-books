"""Account ordering, index, and admin-field-exposure tests.

Three things in one module — they all hang off the Group E commit-1
schema change:

1. Meta.ordering drift — locks Account._meta.ordering to
   ["display_order", "name"] so a future `git revert` can't quietly
   restore the old single-column sort.
2. Index drift — confirms the composite (display_order, name) index
   exists in PG; same `pg_indexes` introspection style as Group D's
   constraint drift tests.
3. Admin form exposure — refinement #3 from the Group E breakdown.
   `display_order` MUST appear in the admin change form (editable);
   `is_system` MUST be readonly. Tests the ModelAdmin directly so we
   don't have to satisfy the 2FA login flow in this test.
"""
from __future__ import annotations

import pytest
from django.contrib.admin.sites import AdminSite
from django.db import connection
from django.test import RequestFactory

from books.accounting.admin import AccountAdmin
from books.accounting.factories import AccountFactory
from books.accounting.models import Account

# --- Meta.ordering drift -------------------------------------------------


def test_account_meta_ordering_is_display_order_then_name():
    assert Account._meta.ordering == ["display_order", "name"]


@pytest.mark.django_db
def test_default_queryset_sorts_by_display_order_then_name():
    """Negative display_order sorts above default (0); name breaks ties."""
    AccountFactory(account_number="A1", name="Zebra", display_order=0)
    AccountFactory(account_number="A2", name="Aardvark", display_order=0)
    AccountFactory(account_number="A3", name="Anything", display_order=-100)

    ordered = list(Account.objects.values_list("account_number", flat=True))
    assert ordered == ["A3", "A2", "A1"]


@pytest.mark.django_db
def test_negative_display_order_sorts_above_default():
    """Property-shaped: any negative display_order sorts before 0."""
    a_user = AccountFactory(account_number="U1", name="User", display_order=0)
    a_sys = AccountFactory(
        account_number="S1", name="System", display_order=-1
    )
    ordered = list(Account.objects.values_list("pk", flat=True))
    assert ordered == [a_sys.pk, a_user.pk]


# Property #3 from the breakdown — ordering invariant under hypothesis.
# `unique=True` on the integers strategy avoids ties (which would let
# `name` decide and complicate the assertion). Capping `max_examples` keeps
# CI runtime predictable; this is the cheap kind of hypothesis test.
import hypothesis  # noqa: E402
from hypothesis import strategies as st  # noqa: E402


@hypothesis.given(
    # Bound to ±1000 — well inside Postgres INTEGER (±2^31-1) AND within
    # the practical range admins would ever type. The semantic property
    # we're proving is "negative always sorts above non-negative under
    # the default queryset ordering"; that property is independent of
    # absolute magnitude, so a tight range gives faster shrinking and
    # avoids generating values that overflow the column type.
    sys_order=st.integers(min_value=-1000, max_value=-1),
    user_order=st.integers(min_value=0, max_value=1000),
)
@hypothesis.settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[hypothesis.HealthCheck.function_scoped_fixture],
)
@pytest.mark.django_db
def test_property_negative_always_sorts_above_non_negative(
    sys_order: int, user_order: int
):
    """For any (negative, non-negative) display_order pair, the negative
    row sorts first under the default queryset ordering."""
    Account.objects.all().delete()
    sys_a = AccountFactory(
        account_number="S-PROP", name="System", display_order=sys_order
    )
    user_a = AccountFactory(
        account_number="U-PROP", name="User", display_order=user_order
    )
    ordered = list(Account.objects.values_list("pk", flat=True))
    assert ordered == [sys_a.pk, user_a.pk], (
        f"sys_order={sys_order} user_order={user_order} did not sort sys first"
    )


# --- Index drift ---------------------------------------------------------


@pytest.mark.django_db
def test_composite_index_exists_in_pg():
    """The `(display_order, name)` index must be present and in that order.

    Parses the parenthesized column list out of pg_indexes.indexdef
    rather than substring-searching the whole CREATE INDEX text — the
    word "name" appears in the index identifier
    (`account_disporder_name_idx`) and would always match before
    reaching the column list.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT indexdef FROM pg_indexes
            WHERE tablename = 'account'
              AND indexname = 'account_disporder_name_idx'
            """
        )
        row = cursor.fetchone()

    assert row is not None, (
        "Composite index `account_disporder_name_idx` is missing. "
        "Did migration 0003_account_display_order get applied?"
    )
    indexdef = row[0]

    # Postgres returns indexdef like:
    #   CREATE INDEX account_disporder_name_idx ON public.account
    #     USING btree (display_order, name)
    # Pull out the parenthesized column list and split on commas.
    after_using = indexdef.split("USING btree", 1)[1]
    col_list_text = after_using.strip().strip("()")
    cols = [c.strip().strip('"') for c in col_list_text.split(",")]

    assert cols == ["display_order", "name"], (
        f"Composite index has wrong column order: {cols} (raw: {indexdef!r})"
    )


# --- Admin form exposure (refinement #3) --------------------------------


def _admin_request_factory():
    """Minimal request stand-in for ModelAdmin.get_form/get_fieldsets."""
    rf = RequestFactory()
    request = rf.get("/admin/accounting/account/add/")
    # ModelAdmin.get_form may consult request.user for permissions; we
    # supply an anonymous-but-authenticated stub that says "yes" to the
    # standard checks. Real auth is exercised by test_admin_owner_only.
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


def test_admin_exposes_display_order_in_fields():
    assert "display_order" in AccountAdmin.fields
    assert "display_order" in AccountAdmin.list_display


def test_admin_marks_is_system_readonly():
    assert "is_system" in AccountAdmin.readonly_fields


@pytest.mark.django_db
def test_admin_form_renders_display_order_as_editable():
    """get_form() includes display_order in base_fields; is_system is excluded
    because it's readonly. This is the functional check that the field is
    actually editable through the admin change form."""
    admin = AccountAdmin(Account, AdminSite())
    request = _admin_request_factory()
    form_class = admin.get_form(request)
    assert "display_order" in form_class.base_fields, (
        "display_order missing from admin form — it would not be editable."
    )
    assert "is_system" not in form_class.base_fields, (
        "is_system appears as editable; it must be readonly."
    )
