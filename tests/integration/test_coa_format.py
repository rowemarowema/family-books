"""Format drift detection on the default COA fixture.

If the fixture file is edited without updating Batch #4 (account-number
format, system-account convention, type vocabulary), this test trips so
the inconsistency surfaces in CI rather than at first-load time.

The patterns here are:
  - User accounts: `^[1-5]-\\d{4}$`  (1-Asset, 2-Liab, 3-Eq, 4-Rev, 5-Exp)
  - System accounts: `^3-9\\d{3}$`   (Equity scaffolding only)

Type vocabulary must be the AccountType TextChoices; loader lowercases
the JSON's capitalized values to match.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from django.conf import settings

from books.accounting.models import AccountType, NormalBalance


USER_ACCOUNT_NUMBER_PATTERN = re.compile(r"^[1-5]-\d{4}$")
SYSTEM_ACCOUNT_NUMBER_PATTERN = re.compile(r"^3-9\d{3}$")


@pytest.fixture(scope="module")
def fixture_data() -> dict:
    path = Path(settings.BASE_DIR) / "fixtures" / "default_coa.json"
    with open(path, "r", encoding="utf-8") as fp:
        return json.load(fp)


def test_fixture_top_level_shape(fixture_data):
    assert "metadata" in fixture_data
    assert "system_accounts" in fixture_data
    assert "accounts" in fixture_data


def test_system_account_count(fixture_data):
    assert len(fixture_data["system_accounts"]) == 3


def test_user_account_count(fixture_data):
    """Stage 1 ships with Mark's full QuickBooks export — 640 rows.

    If this changes, update Batch #4 metadata and the verification block
    in PROGRESS.md so the count stays sourced from a single place.
    """
    assert len(fixture_data["accounts"]) == 640


def test_user_account_numbers_match_format(fixture_data):
    bad = [
        a["account_number"]
        for a in fixture_data["accounts"]
        if not USER_ACCOUNT_NUMBER_PATTERN.match(a["account_number"])
    ]
    assert not bad, f"Account numbers do not match {{prefix}}-{{4-digit}}: {bad}"


def test_system_account_numbers_match_format(fixture_data):
    bad = [
        a["account_number"]
        for a in fixture_data["system_accounts"]
        if not SYSTEM_ACCOUNT_NUMBER_PATTERN.match(a["account_number"])
    ]
    assert not bad


def test_system_accounts_are_all_equity(fixture_data):
    """Hard rule from Batch #4: system accounts are Equity-only in v1."""
    for a in fixture_data["system_accounts"]:
        assert a["type"] == "Equity"
        assert a["is_system"] is True


def test_system_accounts_have_negative_display_order(fixture_data):
    """Negative display_order sorts above user accounts (default 0)."""
    for a in fixture_data["system_accounts"]:
        assert a["display_order"] < 0, (
            f"System account {a['account_number']} has display_order="
            f"{a['display_order']}; expected negative to sort to top."
        )


def test_user_accounts_are_not_system(fixture_data):
    leaked = [a["account_number"] for a in fixture_data["accounts"] if a["is_system"]]
    assert not leaked, f"is_system=True on rows in 'accounts' array: {leaked}"


def test_account_types_are_in_textchoices_vocabulary(fixture_data):
    valid = {choice.label for choice in AccountType}  # capitalized labels
    bad = {
        a["account_number"]: a["type"]
        for a in fixture_data["system_accounts"] + fixture_data["accounts"]
        if a["type"] not in valid
    }
    assert not bad, f"Unknown type values: {bad}"


def test_normal_balance_matches_type(fixture_data):
    expected_by_type = {
        "Asset": "Debit",
        "Expense": "Debit",
        "Liability": "Credit",
        "Equity": "Credit",
        "Revenue": "Credit",
    }
    bad = []
    for a in fixture_data["system_accounts"] + fixture_data["accounts"]:
        want = expected_by_type[a["type"]]
        if a["normal_balance"] != want:
            bad.append((a["account_number"], a["type"], a["normal_balance"]))
    assert not bad, f"Type/normal_balance drift: {bad}"


def test_account_numbers_are_unique(fixture_data):
    nums = [
        a["account_number"]
        for a in fixture_data["system_accounts"] + fixture_data["accounts"]
    ]
    assert len(set(nums)) == len(nums), "Duplicate account_number in fixture"


def test_account_number_field_max_length_satisfied(fixture_data):
    """The Account.account_number field is 16 chars; fixture must fit."""
    too_long = [
        a["account_number"]
        for a in fixture_data["system_accounts"] + fixture_data["accounts"]
        if len(a["account_number"]) > 16
    ]
    assert not too_long
