"""Tests for the `set_opening_balance` management command.

Coverage:
- Mutual exclusivity of --account / --csv.
- Single-account: success, refusal cases (bad amount/date/account).
- CSV: success, refusal-not-partial (one bad row aborts batch),
  account_number wins over account_path when both present, path-only
  resolution, --dry-run, missing file, missing column, both-empty row.
- Real-fixture smoke: load default_coa.json, run --csv against the
  approved fixtures/sample_opening_balances.csv, verify 6 JEs +
  expected per-account amounts.
"""
from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal
from io import StringIO
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError

from books.accounting.factories import (
    AccountFactory,
    EquityAccountFactory,
    LiabilityAccountFactory,
)
from books.accounting.models import (
    Account,
    AccountType,
    JournalEntry,
    NormalBalance,
)
from books.accounting.opening_balances import (
    OPENING_BALANCE_EQUITY_ACCOUNT_NUMBER,
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


def _write_csv(tmp_path: Path, rows: list[dict[str, str]]) -> Path:
    p = tmp_path / "balances.csv"
    with open(p, "w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(
            fp,
            fieldnames=["account_number", "account_path", "amount", "as_of"],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return p


# ---------------------------------------------------------------------------
# Mutual exclusivity / required args
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_requires_account_or_csv(owner):
    with pytest.raises(CommandError):
        call_command("set_opening_balance")


@pytest.mark.django_db
def test_account_and_csv_mutually_exclusive(owner, cash, tmp_path):
    fix = _write_csv(tmp_path, [])
    with pytest.raises(CommandError):
        call_command(
            "set_opening_balance",
            "--account", "1-0001", "--csv", str(fix),
        )


@pytest.mark.django_db
def test_single_mode_requires_amount_and_as_of(owner, cash):
    with pytest.raises(CommandError) as exc:
        call_command("set_opening_balance", "--account", "1-0001")
    assert "--amount" in str(exc.value)


# ---------------------------------------------------------------------------
# Single-account mode
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_single_account_success(owner, cash):
    out = StringIO()
    call_command(
        "set_opening_balance",
        "--account", "1-0001",
        "--amount", "8500.00",
        "--as-of", "2001-01-01",
        stdout=out,
    )
    assert "Posted opening balance JE" in out.getvalue()
    cash.refresh_from_db()
    assert cash.opening_balance == Decimal("8500.00")
    assert cash.opening_balance_date == date(2001, 1, 1)


@pytest.mark.django_db
def test_single_account_unknown_number_refuses(owner, opening_balance_equity):
    with pytest.raises(CommandError) as exc:
        call_command(
            "set_opening_balance",
            "--account", "9-9999",
            "--amount", "100.00",
            "--as-of", "2001-01-01",
        )
    assert "9-9999" in str(exc.value)


@pytest.mark.django_db
def test_single_account_bad_amount_refuses(owner, cash):
    with pytest.raises(CommandError) as exc:
        call_command(
            "set_opening_balance",
            "--account", "1-0001",
            "--amount", "banana",
            "--as-of", "2001-01-01",
        )
    assert "Decimal" in str(exc.value) or "banana" in str(exc.value)


@pytest.mark.django_db
def test_single_account_negative_amount_refuses(owner, cash):
    with pytest.raises(CommandError) as exc:
        call_command(
            "set_opening_balance",
            "--account", "1-0001",
            "--amount", "-100.00",
            "--as-of", "2001-01-01",
        )
    assert "positive" in str(exc.value).lower()


@pytest.mark.django_db
def test_single_account_bad_date_refuses(owner, cash):
    with pytest.raises(CommandError) as exc:
        call_command(
            "set_opening_balance",
            "--account", "1-0001",
            "--amount", "100.00",
            "--as-of", "yesterday",
        )
    assert "YYYY-MM-DD" in str(exc.value)


@pytest.mark.django_db
def test_single_account_dry_run_creates_nothing(owner, cash):
    out = StringIO()
    call_command(
        "set_opening_balance",
        "--account", "1-0001",
        "--amount", "8500.00",
        "--as-of", "2001-01-01",
        "--dry-run",
        stdout=out,
    )
    assert "DRY RUN" in out.getvalue()
    assert JournalEntry.objects.count() == 0
    cash.refresh_from_db()
    assert cash.opening_balance == Decimal("0.00")


# ---------------------------------------------------------------------------
# CSV mode — single-row baseline
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_csv_single_row_with_account_number(owner, cash, tmp_path):
    fix = _write_csv(tmp_path, [{
        "account_number": "1-0001", "account_path": "",
        "amount": "100.00", "as_of": "2001-01-01",
    }])
    call_command("set_opening_balance", "--csv", str(fix))
    cash.refresh_from_db()
    assert cash.opening_balance == Decimal("100.00")


@pytest.mark.django_db
def test_csv_single_row_with_account_path(owner, cash, tmp_path):
    """Top-level Asset with no parent: full_path == name."""
    fix = _write_csv(tmp_path, [{
        "account_number": "", "account_path": "Cash",
        "amount": "100.00", "as_of": "2001-01-01",
    }])
    call_command("set_opening_balance", "--csv", str(fix))
    cash.refresh_from_db()
    assert cash.opening_balance == Decimal("100.00")


@pytest.mark.django_db
def test_csv_account_number_wins_when_both_present(owner, cash, tmp_path):
    """When both columns are populated AND they agree, account_number
    is canonical. The test verifies this by populating account_path
    with a value that — IF used — would resolve to a *different*
    account; account_number takes precedence so the right account gets
    the balance."""
    other = AccountFactory(
        account_number="1-9999", name="Decoy",
        type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
    )
    # account_number says Cash; account_path says Decoy. account_number wins.
    fix = _write_csv(tmp_path, [{
        "account_number": "1-0001", "account_path": "Decoy",
        "amount": "100.00", "as_of": "2001-01-01",
    }])
    call_command("set_opening_balance", "--csv", str(fix))
    cash.refresh_from_db()
    other.refresh_from_db()
    assert cash.opening_balance == Decimal("100.00")
    assert other.opening_balance == Decimal("0.00")


# ---------------------------------------------------------------------------
# CSV refusal contract
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_csv_missing_file_refuses(owner, tmp_path):
    with pytest.raises(CommandError) as exc:
        call_command(
            "set_opening_balance", "--csv", str(tmp_path / "nope.csv"),
        )
    assert "not found" in str(exc.value).lower()


@pytest.mark.django_db
def test_csv_missing_column_refuses(owner, tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("account_number,amount\n1-0001,100\n", encoding="utf-8")
    with pytest.raises(CommandError) as exc:
        call_command("set_opening_balance", "--csv", str(p))
    assert "missing CSV column" in str(exc.value)


@pytest.mark.django_db
def test_csv_row_missing_both_identifiers_refuses(owner, cash, tmp_path):
    fix = _write_csv(tmp_path, [{
        "account_number": "", "account_path": "",
        "amount": "100.00", "as_of": "2001-01-01",
    }])
    with pytest.raises(CommandError) as exc:
        call_command("set_opening_balance", "--csv", str(fix))
    assert "Row 2" in str(exc.value)
    assert "required" in str(exc.value)


@pytest.mark.django_db
def test_csv_unknown_account_number_refuses_naming_row(owner, tmp_path):
    fix = _write_csv(tmp_path, [{
        "account_number": "9-9999", "account_path": "",
        "amount": "100.00", "as_of": "2001-01-01",
    }])
    with pytest.raises(CommandError) as exc:
        call_command("set_opening_balance", "--csv", str(fix))
    assert "Row 2" in str(exc.value)
    assert "9-9999" in str(exc.value)


@pytest.mark.django_db
def test_csv_unknown_account_path_refuses_naming_row(owner, cash, tmp_path):
    fix = _write_csv(tmp_path, [{
        "account_number": "", "account_path": "Bogus:Path",
        "amount": "100.00", "as_of": "2001-01-01",
    }])
    with pytest.raises(CommandError) as exc:
        call_command("set_opening_balance", "--csv", str(fix))
    assert "Row 2" in str(exc.value)
    assert "Bogus:Path" in str(exc.value)


@pytest.mark.django_db
def test_csv_bad_row_aborts_whole_batch(owner, cash, tmp_path):
    """Refuse-not-partial: a single bad row causes the whole batch
    to roll back. Earlier good rows leave no JEs behind."""
    other = AccountFactory(
        account_number="1-0002", name="Other",
        type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
    )
    fix = _write_csv(tmp_path, [
        {"account_number": "1-0001", "account_path": "",
         "amount": "100.00", "as_of": "2001-01-01"},
        {"account_number": "1-0002", "account_path": "",
         "amount": "200.00", "as_of": "2001-01-01"},
        {"account_number": "9-9999", "account_path": "",
         "amount": "300.00", "as_of": "2001-01-01"},
    ])
    with pytest.raises(CommandError):
        call_command("set_opening_balance", "--csv", str(fix))

    # No JEs landed.
    assert JournalEntry.objects.count() == 0
    cash.refresh_from_db()
    other.refresh_from_db()
    assert cash.opening_balance == Decimal("0.00")
    assert other.opening_balance == Decimal("0.00")


@pytest.mark.django_db
def test_csv_dry_run_creates_nothing(owner, cash, tmp_path):
    fix = _write_csv(tmp_path, [{
        "account_number": "1-0001", "account_path": "",
        "amount": "100.00", "as_of": "2001-01-01",
    }])
    out = StringIO()
    call_command("set_opening_balance", "--csv", str(fix), "--dry-run",
                 stdout=out)
    assert "DRY RUN" in out.getvalue()
    assert JournalEntry.objects.count() == 0


# ---------------------------------------------------------------------------
# Real-fixture smoke (the standing rule)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_real_fixture_csv_smoke(owner):
    """Load default_coa.json, run --csv against the approved
    fixtures/sample_opening_balances.csv, verify 6 JEs + per-account
    amounts.

    This is the test that proves the CLI works end-to-end against
    production-shape data — 643 accounts including parent/child
    hierarchies that exercise the account_path resolver.
    """
    call_command("seed_default_coa")

    sample = Path(settings.BASE_DIR) / "fixtures" / "sample_opening_balances.csv"
    assert sample.exists(), f"Sample CSV missing at {sample}"

    out = StringIO()
    call_command("set_opening_balance", "--csv", str(sample), stdout=out)
    assert "Posted 6 opening balance JE(s)" in out.getvalue()

    # Per-row amounts on the actual fixture accounts.
    expectations = [
        ("1-0179", Decimal("8500.00"), date(2001, 1, 1)),    # BOA - Savings
        ("1-0196", Decimal("12000.00"), date(2001, 1, 1)),   # Checking (6708)
        ("1-0270", Decimal("4500.00"), date(2005, 6, 1)),    # E*Trade Cash
        ("2-0015", Decimal("500.00"), date(2010, 1, 1)),     # Rowe Bowl
        ("2-0002", Decimal("650.00"), date(2005, 6, 1)),     # Amazon Prime VISA
        ("2-0011", Decimal("1200.00"), date(2010, 1, 1)),    # Golf Contest
    ]
    for number, amount, as_of in expectations:
        a = Account.objects.get(account_number=number)
        assert a.opening_balance == amount, (
            f"{number}: got {a.opening_balance}, expected {amount}"
        )
        assert a.opening_balance_date == as_of

    # 6 user-account-side JEs (each opening balance is a 2-line entry).
    assert JournalEntry.objects.count() == 6
