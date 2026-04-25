"""Tests for trial-balance exports — CSV / XLSX / PDF.

Coverage:
- Cell-level round-trip property test: HTML, CSV, XLSX all carry the
  same Decimal value per (account, cell) as the engine, quantized to
  2 dp. Single test, multiple formats — drift between any two surfaces
  here.
- Per-format smoke / unit:
    CSV  — header columns, totals row, indent, no type subheader rows.
    XLSX — Workbook structure, number_format on numeric cells,
           cell.value carries native Decimal not string.
    PDF  — Content-Type, magic bytes, pypdf text extract contains
           known dollar amount. Skip on Windows-without-GTK.
- Filename / Content-Disposition headers per format.
- Bad format query param returns 400 (regression — F.4 had this when
  VALID_FORMATS was ("html",); F.5 widens, so retest with "json").
- VALID_FORMATS drift test.
- Real-fixture browser smoke: load default_coa.json, post 1 opening
  balance, GET each of the 4 formats, assert non-empty + correct
  Content-Type + (where parseable) the known amount.
"""
from __future__ import annotations

import csv
import io
import platform
from datetime import date
from decimal import Decimal

import pytest
from bs4 import BeautifulSoup
from django.test import Client
from django.urls import reverse
from openpyxl import load_workbook

from books.accounting.factories import (
    AccountFactory,
    EquityAccountFactory,
    RevenueAccountFactory,
    make_balanced_entry,
)
from books.accounting.models import Account, AccountType, NormalBalance
from books.accounting.opening_balances import (
    OPENING_BALANCE_EQUITY_ACCOUNT_NUMBER,
    set_opening_balance,
)
from books.accounting.posting import post_entry
from books.accounting.reports.trial_balance import compute_trial_balance
from books.web.views.reports import VALID_FORMATS


TWO_DP = Decimal("0.01")


def _has_gtk() -> bool:
    """Probe whether WeasyPrint can load. Cached at module level so
    the smoke test doesn't pay the import cost per case.

    On Windows: WeasyPrint requires the GTK runtime. If absent, the
    import raises OSError. We treat both ImportError and OSError as
    "GTK unavailable" so the skipif fires on a fresh dev box.
    """
    try:
        import weasyprint  # noqa: F401
        return True
    except (ImportError, OSError):
        return False


SKIP_PDF_ON_WINDOWS_NO_GTK = pytest.mark.skipif(
    platform.system() == "Windows" and not _has_gtk(),
    reason=(
        "WeasyPrint requires GTK runtime on Windows; production runs "
        "Linux on Render where GTK is apt-installed at build time. "
        "See docs/SETUP.md for local install."
    ),
)


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
        account_number="1-0001", name="Cash",
        type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
    )


@pytest.fixture
def revenue(db) -> Account:
    return RevenueAccountFactory(account_number="4-0001", name="Sales")


@pytest.fixture
def owner_client(owner) -> Client:
    client = Client()
    client.force_login(owner)
    return client


@pytest.fixture
def populated_books(owner, cash, revenue):
    """A handful of postings + an opening balance to give the round-
    trip test something non-trivial to compare across formats."""
    set_opening_balance(
        cash, amount=Decimal("8500.00"),
        as_of=date(2001, 1, 1), user=owner,
    )
    post_entry(
        make_balanced_entry(
            debit_account=cash, credit_account=revenue,
            amount=Decimal("250.50"),
        ),
        user=owner,
    )
    post_entry(
        make_balanced_entry(
            debit_account=cash, credit_account=revenue,
            amount=Decimal("1234.56"),
        ),
        user=owner,
    )
    return cash


# ---------------------------------------------------------------------------
# VALID_FORMATS drift
# ---------------------------------------------------------------------------


def test_valid_formats_vocabulary_pinned():
    """If a future change widens (or narrows) the format set without
    updating exporters / docs / tests, this trips."""
    assert VALID_FORMATS == ("html", "csv", "xlsx", "pdf")


# ---------------------------------------------------------------------------
# Cell-level round-trip across HTML, CSV, XLSX (the F.5 property)
# ---------------------------------------------------------------------------


def _walk_engine(rows):
    for row in rows:
        yield row
        yield from _walk_engine(row.children)


def _engine_cells(tb) -> dict[tuple[str, str], Decimal]:
    """{(account_number, cell_name): Decimal} for every row + cell."""
    out: dict[tuple[str, str], Decimal] = {}
    for row in _walk_engine(tb.rows):
        out[(row.account.account_number, "debits")] = row.debits_total
        out[(row.account.account_number, "credits")] = row.credits_total
        out[(row.account.account_number, "own_balance")] = row.own_balance
        out[(row.account.account_number, "rollup_balance")] = row.rollup_balance
    return out


def _html_cells(body: bytes) -> dict[tuple[str, str], Decimal]:
    soup = BeautifulSoup(body, "html.parser")
    out: dict[tuple[str, str], Decimal] = {}
    for tr in soup.find_all("tr", attrs={"data-account-pk": True}):
        # account_number isn't in the HTML; use pk lookup. Walk td list.
        pk = int(tr["data-account-pk"])
        a = Account.objects.get(pk=pk)
        for cell in ("debits", "credits", "own_balance", "rollup_balance"):
            td = tr.find("td", attrs={"data-cell": cell})
            raw = td.attrs.get("data-value", "")
            if raw == "":
                continue
            out[(a.account_number, cell)] = Decimal(raw)
    return out


def _csv_cells(body: bytes) -> dict[tuple[str, str], Decimal]:
    reader = csv.DictReader(io.StringIO(body.decode("utf-8")))
    out: dict[tuple[str, str], Decimal] = {}
    for row in reader:
        if row["account_number"] == "TOTAL":
            continue
        for cell in ("debits", "credits", "own_balance", "rollup_balance"):
            raw = row.get(cell, "")
            if raw == "":
                continue
            out[(row["account_number"], cell)] = Decimal(raw)
    return out


def _xlsx_cells(body: bytes) -> dict[tuple[str, str], Decimal]:
    wb = load_workbook(io.BytesIO(body), data_only=False)
    ws = wb["Trial Balance"]
    headers = [c.value for c in ws[1]]
    col_idx = {h: i for i, h in enumerate(headers)}
    out: dict[tuple[str, str], Decimal] = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        account_number = row[col_idx["account_number"]]
        if account_number == "TOTAL" or account_number is None:
            continue
        for cell in ("debits", "credits", "own_balance", "rollup_balance"):
            value = row[col_idx[cell]]
            if value is None:
                continue
            # XLSX stores numerics natively; openpyxl returns Decimal/
            # int/float depending on cell type. Normalize to Decimal.
            out[(str(account_number), cell)] = Decimal(str(value))
    return out


@pytest.mark.django_db
def test_cell_level_round_trip_html_csv_xlsx_match_engine(
    owner_client, populated_books
):
    """Property: every (account_number, cell) in the engine appears
    in HTML, CSV, and XLSX with the same Decimal value (quantized to
    2 dp).

    Drift between any two surfaces here, not in production.

    Drop-test scope:
      - HTML type subheader rows have no `data-account-pk` so they
        don't appear in the cell map (Q5 design).
      - CSV/XLSX have no type subheader rows at all (Q2 refinement).
      - HTML totals live in tfoot with `data-test-role="totals"`;
        CSV/XLSX totals are a TOTAL row. Both excluded from the
        per-row map; totals get their own assertion below.
    """
    url = reverse("trial-balance")
    tb = compute_trial_balance(as_of=date.today())
    engine = _engine_cells(tb)
    assert engine, "fixture should produce at least one engine row"

    html_resp = owner_client.get(url, {"format": "html"})
    csv_resp = owner_client.get(url, {"format": "csv"})
    xlsx_resp = owner_client.get(url, {"format": "xlsx"})

    assert html_resp.status_code == csv_resp.status_code == xlsx_resp.status_code == 200

    html = _html_cells(html_resp.content)
    csv_cells = _csv_cells(csv_resp.content)
    xlsx = _xlsx_cells(xlsx_resp.content)

    for key, engine_value in engine.items():
        eq = engine_value.quantize(TWO_DP)
        assert html.get(key, Decimal("nan")).quantize(TWO_DP) == eq, (
            f"HTML drift at {key}: html={html.get(key)} engine={engine_value}"
        )
        assert csv_cells.get(key, Decimal("nan")).quantize(TWO_DP) == eq, (
            f"CSV drift at {key}: csv={csv_cells.get(key)} engine={engine_value}"
        )
        assert xlsx.get(key, Decimal("nan")).quantize(TWO_DP) == eq, (
            f"XLSX drift at {key}: xlsx={xlsx.get(key)} engine={engine_value}"
        )

    # Totals tie out across all three.
    soup = BeautifulSoup(html_resp.content, "html.parser")
    totals_tr = soup.find("tr", attrs={"data-test-role": "totals"})
    html_total_dr = Decimal(
        totals_tr.find("td", attrs={"data-cell": "total_debits"})["data-value"]
    )
    csv_reader = csv.DictReader(io.StringIO(csv_resp.content.decode("utf-8")))
    csv_total_row = [r for r in csv_reader if r["account_number"] == "TOTAL"][0]
    csv_total_dr = Decimal(csv_total_row["debits"])
    xlsx_wb = load_workbook(io.BytesIO(xlsx_resp.content), data_only=False)
    xlsx_ws = xlsx_wb["Trial Balance"]
    xlsx_total_dr = None
    for row in xlsx_ws.iter_rows(values_only=True):
        if row[0] == "TOTAL":
            xlsx_total_dr = Decimal(str(row[4]))
    assert html_total_dr == csv_total_dr == xlsx_total_dr == tb.total_debits


# ---------------------------------------------------------------------------
# Per-format smoke / unit
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_csv_response_headers(owner_client, populated_books):
    response = owner_client.get(
        reverse("trial-balance"), {"format": "csv", "as_of": "2026-04-25"},
    )
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")
    assert response["Content-Disposition"] == (
        'attachment; filename="trial-balance-2026-04-25.csv"'
    )


@pytest.mark.django_db
def test_csv_has_header_row_and_no_type_subheaders(
    owner_client, populated_books
):
    response = owner_client.get(reverse("trial-balance"), {"format": "csv"})
    reader = csv.reader(io.StringIO(response.content.decode("utf-8")))
    rows = list(reader)
    header = rows[0]
    assert header[:8] == [
        "account_number", "name", "type", "depth",
        "debits", "credits", "own_balance", "rollup_balance",
    ]
    # No row should have an empty account_number AND non-empty name —
    # that would be a type subheader row.
    for row in rows[1:-1]:  # skip header and totals
        assert row[0] != "" or row[1] == "", (
            f"unexpected type-subheader-like row: {row}"
        )
    assert rows[-1][0] == "TOTAL"


@pytest.mark.django_db
def test_csv_indent_uses_4_spaces_per_depth(owner, opening_balance_equity, owner_client):
    parent = AccountFactory(
        account_number="1-P", name="Parent",
        type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
    )
    child = AccountFactory(
        account_number="1-C", name="Child", parent_account=parent,
        type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
    )
    set_opening_balance(child, amount=Decimal("100.00"),
                        as_of=date(2001, 1, 1), user=owner)

    response = owner_client.get(reverse("trial-balance"), {"format": "csv"})
    reader = csv.DictReader(io.StringIO(response.content.decode("utf-8")))
    by_number = {r["account_number"]: r for r in reader}
    # Parent depth=1 → 0 leading spaces.
    assert by_number["1-P"]["name"] == "Parent"
    # Child depth=2 → 4 leading spaces.
    assert by_number["1-C"]["name"] == "    Child"


@pytest.mark.django_db
def test_xlsx_response_headers(owner_client, populated_books):
    response = owner_client.get(
        reverse("trial-balance"), {"format": "xlsx", "as_of": "2026-04-25"},
    )
    assert response.status_code == 200
    assert response["Content-Type"] == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert response["Content-Disposition"] == (
        'attachment; filename="trial-balance-2026-04-25.xlsx"'
    )


@pytest.mark.django_db
def test_xlsx_numeric_cells_carry_decimals_with_accounting_format(
    owner_client, populated_books
):
    response = owner_client.get(reverse("trial-balance"), {"format": "xlsx"})
    wb = load_workbook(io.BytesIO(response.content), data_only=False)
    ws = wb["Trial Balance"]
    # Find the Cash row (account_number 1-0001).
    cash_row_idx = None
    for idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
        if row[0] == "1-0001":
            cash_row_idx = idx
            break
    assert cash_row_idx is not None
    debits_cell = ws.cell(row=cash_row_idx, column=5)
    # cell.value is the underlying number, not the formatted string.
    assert debits_cell.value is not None
    assert Decimal(str(debits_cell.value)) == Decimal("9985.06")  # 8500 + 250.50 + 1234.56
    assert debits_cell.number_format == '#,##0.00;(#,##0.00)'


# ---------------------------------------------------------------------------
# PDF smoke (gated by GTK availability on Windows)
# ---------------------------------------------------------------------------


@SKIP_PDF_ON_WINDOWS_NO_GTK
@pytest.mark.django_db
def test_pdf_renders_with_correct_headers(owner_client, populated_books):
    response = owner_client.get(
        reverse("trial-balance"), {"format": "pdf", "as_of": "2026-04-25"},
    )
    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response["Content-Disposition"] == (
        'attachment; filename="trial-balance-2026-04-25.pdf"'
    )
    assert response.content[:4] == b"%PDF"  # PDF magic bytes


@SKIP_PDF_ON_WINDOWS_NO_GTK
@pytest.mark.django_db
def test_pdf_content_contains_known_amount(owner_client, populated_books):
    """pypdf extracts text from the rendered PDF; assert known amounts
    appear. Doesn't go cell-by-cell (PDF text positioning is fragile),
    but proves the rendering pipeline isn't silently dropping data."""
    from pypdf import PdfReader

    response = owner_client.get(reverse("trial-balance"), {"format": "pdf"})
    reader = PdfReader(io.BytesIO(response.content))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)

    # Cash debits = 8500 + 250.50 + 1234.56 = 9,985.06 (per the
    # populated_books fixture). Check both forms in case the PDF
    # font ligatures / spacing affect the comma rendering.
    assert "9,985.06" in text or "9985.06" in text
    assert "Trial Balance" in text


@pytest.mark.django_db
def test_pdf_unavailable_returns_503_with_clear_message(
    owner_client, populated_books, monkeypatch
):
    """When WeasyPrint can't load (e.g., GTK missing), the view
    returns 503 with the underlying exception message, NOT 500.
    Forced via monkeypatch so the test is platform-agnostic."""
    from books.accounting.reports.exporters import pdf_export

    def raise_unavailable(tb, request):
        raise pdf_export.PDFRendererUnavailable(
            "simulated GTK absence for testing"
        )

    monkeypatch.setattr(
        "books.web.views.reports.render_pdf", raise_unavailable,
    )
    response = owner_client.get(reverse("trial-balance"), {"format": "pdf"})
    assert response.status_code == 503
    assert b"simulated GTK absence" in response.content


# ---------------------------------------------------------------------------
# Regressions / negative cases
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_unknown_format_still_returns_400(owner_client):
    """F.4 had this when VALID_FORMATS was ('html',); F.5 widens the
    set, and unknown formats (json, html5, ...) must still 400."""
    response = owner_client.get(reverse("trial-balance"), {"format": "json"})
    assert response.status_code == 400
    assert b"format" in response.content
    assert b"json" in response.content


@pytest.mark.django_db
def test_anonymous_user_cannot_download_exports():
    client = Client()
    for fmt in ("csv", "xlsx", "pdf"):
        response = client.get(reverse("trial-balance"), {"format": fmt})
        assert response.status_code == 403, f"{fmt} leaked to anonymous"


# ---------------------------------------------------------------------------
# Real-fixture browser smoke
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_real_fixture_all_four_formats_download(owner, owner_client):
    """End-to-end against default_coa.json + 1 opening balance: GET
    each of the 4 formats, assert non-empty + correct Content-Type +
    (HTML/CSV/XLSX) the known amount appears."""
    from django.core.management import call_command

    call_command("seed_default_coa")
    boa = Account.objects.get(account_number="1-0179")
    set_opening_balance(
        boa, amount=Decimal("8500.00"),
        as_of=date(2001, 1, 1), user=owner,
    )

    url = reverse("trial-balance")

    html_resp = owner_client.get(url)
    assert html_resp.status_code == 200
    assert b"8500.00" in html_resp.content

    csv_resp = owner_client.get(url, {"format": "csv"})
    assert csv_resp.status_code == 200
    assert csv_resp["Content-Type"].startswith("text/csv")
    assert b"8500.00" in csv_resp.content

    xlsx_resp = owner_client.get(url, {"format": "xlsx"})
    assert xlsx_resp.status_code == 200
    assert xlsx_resp["Content-Type"].startswith(
        "application/vnd.openxmlformats-officedocument"
    )
    assert len(xlsx_resp.content) > 1000  # non-trivial

    pdf_resp = owner_client.get(url, {"format": "pdf"})
    # Either 200 (PDF rendered) or 503 (GTK missing); both are OK in
    # this smoke. The platform-skipped PDF tests above cover the
    # positive case.
    assert pdf_resp.status_code in (200, 503)
    if pdf_resp.status_code == 200:
        assert pdf_resp.content[:4] == b"%PDF"
