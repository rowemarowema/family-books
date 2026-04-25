"""Tests for /reports/trial-balance/ HTTP view.

Coverage:
- URL drift: reverse("trial-balance") resolves.
- Auth integration: anonymous → 403; force_login owner → 200.
- Cell-level tie-out: BeautifulSoup parses rendered HTML; every
  data-value attribute equals the engine field exactly. Decimal
  precision contract (R2): both sides quantized to 2 decimals.
- Sort: display_order honored in render order matches engine order.
- Type subheader structural drift: all 5 type-headers present (R-Q5).
- Comparative columns rendered when prior_as_of supplied.
- Negative balance: data-value carries raw negative; display wraps
  in parens.
- Bad query params (R3): one test per validation path with a clear
  message naming the bad param.
- Real-fixture smoke: load default_coa.json, post 1 opening balance,
  GET the URL, verify the row appears.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Iterable

import pytest
from bs4 import BeautifulSoup
from django.core.management import call_command
from django.test import Client
from django.urls import reverse

from books.accounting.factories import (
    AccountFactory,
    EquityAccountFactory,
    LiabilityAccountFactory,
    RevenueAccountFactory,
    make_balanced_entry,
)
from books.accounting.models import Account, AccountType, NormalBalance
from books.accounting.opening_balances import (
    OPENING_BALANCE_EQUITY_ACCOUNT_NUMBER,
    set_opening_balance,
)
from books.accounting.posting import post_entry
from books.accounting.reports.trial_balance import (
    TYPE_ORDER,
    compute_trial_balance,
)


TWO_DP = Decimal("0.01")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def opening_balance_equity(db) -> Account:
    return EquityAccountFactory(
        account_number=OPENING_BALANCE_EQUITY_ACCOUNT_NUMBER,
        name="Opening Balance Equity (System)",
        is_system=True,
        display_order=-200,
    )


@pytest.fixture
def cash(db, opening_balance_equity) -> Account:
    return AccountFactory(
        account_number="1-0001",
        name="Cash",
        type=AccountType.ASSET,
        normal_balance=NormalBalance.DEBIT,
    )


@pytest.fixture
def revenue(db) -> Account:
    return RevenueAccountFactory(account_number="4-0001", name="Sales")


@pytest.fixture
def owner_client(owner) -> Client:
    """Logged-in client for the owner. SystemFlag.two_factor_enforcement
    is OFF by default in tests, so the owner can reach the view without
    a verified OTP device — the post-bootstrap pre-2FA-setup path."""
    client = Client()
    client.force_login(owner)
    return client


# ---------------------------------------------------------------------------
# URL drift
# ---------------------------------------------------------------------------


def test_trial_balance_url_resolves_to_expected_path():
    """If anyone renames the path, exports (F.5) and any external
    bookmark break. Pinning the path here makes a rename a visible
    test failure."""
    assert reverse("trial-balance") == "/reports/trial-balance/"


# ---------------------------------------------------------------------------
# Auth integration (decorator unit tests live next door; here we just
# verify the wiring through the URL)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_anonymous_user_gets_403_at_url():
    response = Client().get(reverse("trial-balance"))
    assert response.status_code == 403


@pytest.mark.django_db
def test_owner_can_reach_view(owner_client):
    response = owner_client.get(reverse("trial-balance"))
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Render order / sort contract (engine returns rows in (TYPE_RANK,
# display_order, name) order; the template iterates in that order)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_display_order_honored_in_render(owner, owner_client, revenue):
    """Set distinct display_order values inside one type bucket; the
    rendered HTML must list them in (display_order, name) order."""
    a_alpha = AccountFactory(
        account_number="1-A", name="Alpha", display_order=0,
    )
    a_zebra = AccountFactory(
        account_number="1-Z", name="Zebra", display_order=0,
    )
    a_special = AccountFactory(
        account_number="1-S", name="Special", display_order=-100,
    )
    for acct in (a_alpha, a_zebra, a_special):
        post_entry(
            make_balanced_entry(
                debit_account=acct, credit_account=revenue,
                amount=Decimal("1.00"),
            ),
            user=owner,
        )

    response = owner_client.get(reverse("trial-balance"))
    soup = BeautifulSoup(response.content, "html.parser")
    asset_pks = [
        int(tr["data-account-pk"])
        for tr in soup.find_all("tr", attrs={"data-account-pk": True})
        if Account.objects.get(pk=int(tr["data-account-pk"])).type
        == AccountType.ASSET
    ]
    # Special (display_order=-100) first; Alpha then Zebra (both 0).
    assert asset_pks == [a_special.pk, a_alpha.pk, a_zebra.pk]


# ---------------------------------------------------------------------------
# Type subheader structural drift (R-Q5)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_all_five_type_subheaders_present(owner_client):
    """Empty trial balance still renders all 5 type headers. If a
    future change drops an empty section, this trips."""
    response = owner_client.get(reverse("trial-balance"))
    soup = BeautifulSoup(response.content, "html.parser")
    headers = soup.find_all("tr", attrs={"data-test-role": "type-header"})
    type_values = [tr["data-type"] for tr in headers]
    assert type_values == list(TYPE_ORDER)


@pytest.mark.django_db
def test_type_headers_lack_data_account_pk(owner_client):
    """Confirms the cell-level test's `data-account-pk` filter skips
    type-header rows without modification."""
    response = owner_client.get(reverse("trial-balance"))
    soup = BeautifulSoup(response.content, "html.parser")
    headers = soup.find_all("tr", attrs={"data-test-role": "type-header"})
    for header in headers:
        assert "data-account-pk" not in header.attrs


# ---------------------------------------------------------------------------
# Cell-level tie-out (the property test for F.4)
# ---------------------------------------------------------------------------


def _walk_engine(rows):
    for row in rows:
        yield row
        yield from _walk_engine(row.children)


def _decimal_attr(td, name: str = "data-value") -> Decimal | None:
    raw = td.attrs.get(name, "")
    if raw == "":
        return None
    return Decimal(raw)


@pytest.mark.django_db
def test_cell_level_tie_out_engine_to_html(
    owner, owner_client, opening_balance_equity, revenue
):
    """Property: every cell rendered in the HTML carries data-value
    equal to the engine's TrialBalanceRow field, comparing as Decimals
    quantized to 2 places.

    Decimal precision contract (R2): the engine stores 2-decimal-place
    Decimals (DecimalField max_digits=18, decimal_places=2). The
    template renders via floatformat:2 for display but emits the raw
    Decimal in `data-value`. Tests parse `data-value` and quantize
    explicitly to TWO_DP so any future drift in either side trips a
    visible failure rather than a silent rounding-difference pass."""
    cash_acct = AccountFactory(
        account_number="1-0001", name="Cash",
        type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
    )
    set_opening_balance(
        cash_acct, amount=Decimal("8500.00"),
        as_of=date(2001, 1, 1), user=owner,
    )

    response = owner_client.get(reverse("trial-balance"))
    soup = BeautifulSoup(response.content, "html.parser")

    tb = compute_trial_balance(as_of=date.today())
    engine_rows = list(_walk_engine(tb.rows))
    html_rows = soup.find_all("tr", attrs={"data-account-pk": True})

    assert len(html_rows) == len(engine_rows)

    for engine_row, tr in zip(engine_rows, html_rows):
        assert int(tr["data-account-pk"]) == engine_row.account.pk

        for cell, engine_field in [
            ("debits", "debits_total"),
            ("credits", "credits_total"),
            ("own_balance", "own_balance"),
            ("rollup_balance", "rollup_balance"),
        ]:
            td = tr.find("td", attrs={"data-cell": cell})
            html_value = _decimal_attr(td)
            engine_value = getattr(engine_row, engine_field)
            assert html_value is not None
            assert html_value.quantize(TWO_DP) == engine_value.quantize(TWO_DP), (
                f"cell={cell} pk={engine_row.account.pk}: "
                f"html={html_value!r} engine={engine_value!r}"
            )

    # Totals tie-out
    totals_tr = soup.find("tr", attrs={"data-test-role": "totals"})
    td_dr = totals_tr.find("td", attrs={"data-cell": "total_debits"})
    td_cr = totals_tr.find("td", attrs={"data-cell": "total_credits"})
    assert _decimal_attr(td_dr).quantize(TWO_DP) == tb.total_debits.quantize(TWO_DP)
    assert _decimal_attr(td_cr).quantize(TWO_DP) == tb.total_credits.quantize(TWO_DP)


@pytest.mark.django_db
def test_negative_balance_data_value_carries_raw_display_uses_parens(
    owner, owner_client, opening_balance_equity, revenue
):
    """A debit-normal account with credits > debits shows a negative
    own_balance. The displayed text wraps it in parens (no minus
    sign); `data-value` carries the raw negative. The cell-level test
    reads `data-value` so display formatting is irrelevant to
    correctness — but a regression that drops the parens or drops the
    `data-value` would still trip this test."""
    cash_acct = AccountFactory(
        account_number="1-0001", name="Cash",
        type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
    )
    # Post an entry that credits (debit-normal) Cash → balance goes negative.
    entry = make_balanced_entry(
        debit_account=revenue, credit_account=cash_acct,
        amount=Decimal("100.00"),
    )
    post_entry(entry, user=owner)

    response = owner_client.get(reverse("trial-balance"))
    soup = BeautifulSoup(response.content, "html.parser")

    cash_tr = soup.find("tr", attrs={"data-account-pk": str(cash_acct.pk)})
    own_balance_td = cash_tr.find("td", attrs={"data-cell": "own_balance"})

    assert _decimal_attr(own_balance_td) == Decimal("-100.00")
    displayed = own_balance_td.get_text(strip=True)
    assert displayed.startswith("(") and displayed.endswith(")")
    assert "-" not in displayed


# ---------------------------------------------------------------------------
# Comparative columns
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_comparative_columns_rendered_when_prior_as_of_supplied(
    owner, owner_client, opening_balance_equity, revenue
):
    cash_acct = AccountFactory(
        account_number="1-0001", name="Cash",
        type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
    )
    yesterday = date.today() - timedelta(days=2)
    today = date.today()

    e1 = make_balanced_entry(
        debit_account=cash_acct, credit_account=revenue, amount=Decimal("100.00"),
        entry_date=yesterday, posting_date=yesterday,
    )
    post_entry(e1, user=owner)
    e2 = make_balanced_entry(
        debit_account=cash_acct, credit_account=revenue, amount=Decimal("50.00"),
        entry_date=today, posting_date=today,
    )
    post_entry(e2, user=owner)

    response = owner_client.get(
        reverse("trial-balance"),
        {"as_of": today.isoformat(), "prior_as_of": yesterday.isoformat()},
    )
    soup = BeautifulSoup(response.content, "html.parser")
    cash_tr = soup.find("tr", attrs={"data-account-pk": str(cash_acct.pk)})

    prior_td = cash_tr.find("td", attrs={"data-cell": "prior_own_balance"})
    assert prior_td is not None
    assert _decimal_attr(prior_td) == Decimal("100.00")

    now_td = cash_tr.find("td", attrs={"data-cell": "own_balance"})
    assert _decimal_attr(now_td) == Decimal("150.00")


@pytest.mark.django_db
def test_no_comparative_column_without_prior_as_of(owner_client):
    response = owner_client.get(reverse("trial-balance"))
    soup = BeautifulSoup(response.content, "html.parser")
    # "Prior Balance" header absent.
    headers = [th.get_text(strip=True) for th in soup.find_all("th")]
    assert "Prior Balance" not in headers


# ---------------------------------------------------------------------------
# Bad query parameter error messages (R3)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_bad_as_of_returns_400_naming_param(owner_client):
    response = owner_client.get(reverse("trial-balance"), {"as_of": "banana"})
    assert response.status_code == 400
    body = response.content.decode()
    assert "as_of" in body
    assert "YYYY-MM-DD" in body
    assert "banana" in body


@pytest.mark.django_db
def test_bad_prior_as_of_returns_400_naming_param(owner_client):
    response = owner_client.get(
        reverse("trial-balance"), {"prior_as_of": "not-a-date"},
    )
    assert response.status_code == 400
    body = response.content.decode()
    assert "prior_as_of" in body
    assert "not-a-date" in body


@pytest.mark.django_db
def test_unknown_type_returns_400_naming_param(owner_client):
    response = owner_client.get(
        reverse("trial-balance"), {"types": "asset,bogus"},
    )
    assert response.status_code == 400
    body = response.content.decode()
    assert "types" in body
    assert "bogus" in body


@pytest.mark.django_db
def test_bad_format_returns_400_naming_param(owner_client):
    response = owner_client.get(reverse("trial-balance"), {"format": "json"})
    assert response.status_code == 400
    body = response.content.decode()
    assert "format" in body
    assert "json" in body


# ---------------------------------------------------------------------------
# Type filter and include_zero passthrough
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_types_filter_restricts_rendered_rows(owner, owner_client, revenue):
    cash_acct = AccountFactory(
        account_number="1-0001", name="Cash",
        type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
    )
    post_entry(
        make_balanced_entry(
            debit_account=cash_acct, credit_account=revenue,
            amount=Decimal("10.00"),
        ),
        user=owner,
    )

    response = owner_client.get(
        reverse("trial-balance"), {"types": "liability"},
    )
    soup = BeautifulSoup(response.content, "html.parser")
    rows = soup.find_all("tr", attrs={"data-account-pk": True})
    assert rows == []  # asset filtered out


@pytest.mark.django_db
def test_include_zero_query_param_surfaces_empty_accounts(
    owner_client, opening_balance_equity
):
    AccountFactory(
        account_number="1-X", name="Untouched",
        type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
    )
    response = owner_client.get(
        reverse("trial-balance"), {"include_zero": "1"},
    )
    soup = BeautifulSoup(response.content, "html.parser")
    rows = soup.find_all("tr", attrs={"data-account-pk": True})
    pks = {int(tr["data-account-pk"]) for tr in rows}
    untouched = Account.objects.get(account_number="1-X")
    assert untouched.pk in pks


# ---------------------------------------------------------------------------
# Real-fixture smoke
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_real_fixture_plus_opening_balance_renders(owner, owner_client):
    """End-to-end: load default_coa.json, post one opening balance,
    GET the trial-balance URL, sanity-check the response.

    Per the standing rule: every command-line / HTTP user-data
    operation has a real-fixture-shape test before it ships."""
    call_command("seed_default_coa")

    boa = Account.objects.get(account_number="1-0179")
    set_opening_balance(
        boa, amount=Decimal("8500.00"),
        as_of=date(2001, 1, 1), user=owner,
    )

    response = owner_client.get(reverse("trial-balance"))
    assert response.status_code == 200

    soup = BeautifulSoup(response.content, "html.parser")
    boa_tr = soup.find("tr", attrs={"data-account-pk": str(boa.pk)})
    assert boa_tr is not None
    debit_td = boa_tr.find("td", attrs={"data-cell": "debits"})
    assert _decimal_attr(debit_td) == Decimal("8500.00")

    obe = Account.objects.get(
        account_number=OPENING_BALANCE_EQUITY_ACCOUNT_NUMBER,
    )
    obe_tr = soup.find("tr", attrs={"data-account-pk": str(obe.pk)})
    credit_td = obe_tr.find("td", attrs={"data-cell": "credits"})
    assert _decimal_attr(credit_td) == Decimal("8500.00")

    # Totals tie out.
    totals_tr = soup.find("tr", attrs={"data-test-role": "totals"})
    td_dr = totals_tr.find("td", attrs={"data-cell": "total_debits"})
    td_cr = totals_tr.find("td", attrs={"data-cell": "total_credits"})
    assert _decimal_attr(td_dr) == Decimal("8500.00")
    assert _decimal_attr(td_cr) == Decimal("8500.00")
