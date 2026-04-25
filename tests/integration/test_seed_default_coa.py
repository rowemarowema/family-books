"""Tests for `seed_default_coa` — loader behavior, refusal contract,
audit rows, and the four property tests committed in the Group E
breakdown.

Property tests use small synthetic fixtures rather than the full 643-row
file so the parametrized matrix stays fast. The real fixture is
exercised by `test_loads_real_fixture_into_643_rows` and the format
drift suite in test_coa_format.py.
"""
from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from typing import Any

import pytest
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError

from books.accounting.factories import AccountFactory
from books.accounting.models import Account, AccountType, NormalBalance
from books.audit.models import AuditAction, AuditLog


REAL_FIXTURE = Path(settings.BASE_DIR) / "fixtures" / "default_coa.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_fixture(tmp_path: Path, data: dict[str, Any]) -> Path:
    p = tmp_path / "coa.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _minimal_fixture() -> dict[str, Any]:
    """Smallest valid fixture: 1 system + 3 user accounts with one parent link."""
    return {
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
            {
                "account_number": "1-0002",
                "name": "Checking",
                "full_path": "Cash:Checking",
                "type": "Asset",
                "normal_balance": "Debit",
                "parent_account_number": "1-0001",
                "is_active": True,
                "is_system": False,
                "display_order": 0,
                "description": None,
                "tax_category": None,
            },
            {
                "account_number": "1-0003",
                "name": "Savings",
                "full_path": "Cash:Savings",
                "type": "Asset",
                "normal_balance": "Debit",
                "parent_account_number": "1-0001",
                "is_active": True,
                "is_system": False,
                "display_order": 0,
                "description": None,
                "tax_category": None,
            },
        ],
    }


# ---------------------------------------------------------------------------
# Real-fixture round-trip (Property #1)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_loads_real_fixture_into_643_rows():
    """Property #1: every fixture row round-trips. Loading the canonical
    fixture deterministically produces 640 user + 3 system rows."""
    out = StringIO()
    call_command("seed_default_coa", stdout=out)

    assert Account.objects.count() == 643
    assert Account.objects.filter(is_system=True).count() == 3
    assert Account.objects.filter(is_system=False).count() == 640
    assert "Seeded 3 system + 640 user accounts" in out.getvalue()


@pytest.mark.django_db
def test_real_fixture_dry_run_creates_nothing():
    out = StringIO()
    call_command("seed_default_coa", "--dry-run", stdout=out)
    assert Account.objects.count() == 0
    assert "DRY RUN" in out.getvalue()


@pytest.mark.django_db
def test_real_fixture_writes_coa_seeded_audit_row():
    call_command("seed_default_coa")
    audits = AuditLog.objects.filter(action=AuditAction.COA_SEEDED)
    assert audits.count() == 1
    after = audits.get().after_value
    assert after["system_inserted"] == 3
    assert after["user_inserted"] == 640


@pytest.mark.django_db
def test_real_fixture_resolves_parent_links():
    """Spot-check: a known leaf account has its parent FK populated."""
    call_command("seed_default_coa")
    # Use the loader's own data to pick a child with a parent.
    data = json.loads(REAL_FIXTURE.read_text(encoding="utf-8"))
    child_with_parent = next(
        (a for a in data["accounts"] if a["parent_account_number"]), None
    )
    if child_with_parent is None:
        pytest.skip("No parent links in fixture; nothing to verify")
    child = Account.objects.get(account_number=child_with_parent["account_number"])
    parent = Account.objects.get(
        account_number=child_with_parent["parent_account_number"]
    )
    assert child.parent_account_id == parent.id


# ---------------------------------------------------------------------------
# Refusal contract (Property #4)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_refuses_when_user_accounts_already_present(tmp_path):
    """Property #4: re-seed refusal is deterministic across any non-system
    pre-state. Even one stray user account trips refusal."""
    AccountFactory(
        account_number="1-9999",
        name="Stray",
        type=AccountType.ASSET,
        normal_balance=NormalBalance.DEBIT,
    )
    fix = _write_fixture(tmp_path, _minimal_fixture())
    with pytest.raises(CommandError) as exc:
        call_command("seed_default_coa", "--fixture", str(fix))
    assert "non-system Account row(s) already exist" in str(exc.value)
    assert "reset_coa" in str(exc.value)


@pytest.mark.parametrize("user_account_count", [1, 5, 50])
@pytest.mark.django_db
def test_refusal_is_independent_of_partial_state_size(tmp_path, user_account_count):
    """Whether 1 or 50 user accounts are present, the refusal contract
    is the same. Locks against future logic that might "accept partial
    state below threshold N"."""
    for i in range(user_account_count):
        AccountFactory(
            account_number=f"1-{i:04d}",
            name=f"Account{i}",
            type=AccountType.ASSET,
            normal_balance=NormalBalance.DEBIT,
        )
    fix = _write_fixture(tmp_path, _minimal_fixture())
    with pytest.raises(CommandError):
        call_command("seed_default_coa", "--fixture", str(fix))


@pytest.mark.django_db
def test_refusal_writes_audit_row(tmp_path):
    AccountFactory(
        account_number="1-9999",
        name="Stray",
        type=AccountType.ASSET,
        normal_balance=NormalBalance.DEBIT,
    )
    fix = _write_fixture(tmp_path, _minimal_fixture())
    with pytest.raises(CommandError):
        call_command("seed_default_coa", "--fixture", str(fix))
    refused = AuditLog.objects.filter(action=AuditAction.COA_SEED_REFUSED)
    assert refused.count() == 1
    assert refused.get().after_value["existing_user_account_count"] == 1


@pytest.mark.django_db
def test_refusal_creates_no_rows(tmp_path):
    """Even after refusal, no fixture rows should leak through."""
    AccountFactory(
        account_number="1-9999",
        name="Stray",
        type=AccountType.ASSET,
        normal_balance=NormalBalance.DEBIT,
    )
    fix = _write_fixture(tmp_path, _minimal_fixture())
    with pytest.raises(CommandError):
        call_command("seed_default_coa", "--fixture", str(fix))
    # Only the stray remains — no system or user row from the fixture got in.
    assert Account.objects.count() == 1


@pytest.mark.django_db
def test_reseed_after_system_only_state_succeeds(tmp_path):
    """The post-reset_coa shape: 3 system accounts present, 0 user.
    Re-running seed should succeed and skip the existing system rows."""
    fixture = _minimal_fixture()
    # Pre-populate the system account exactly as the fixture has it.
    Account.objects.create(
        account_number="3-9000",
        name="Owner's Equity",
        type="equity",
        normal_balance="credit",
        is_active=True,
        is_system=True,
        display_order=-300,
    )
    fix = _write_fixture(tmp_path, fixture)
    call_command("seed_default_coa", "--fixture", str(fix))
    assert Account.objects.count() == 4  # 1 system (preserved) + 3 user
    assert Account.objects.filter(is_system=True).count() == 1


# ---------------------------------------------------------------------------
# Order independence (Property #2)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "shuffle_seed",
    [None, "reverse", 0, 1, 2, 3],
    ids=["original", "reversed", "seed0", "seed1", "seed2", "seed3"],
)
@pytest.mark.django_db
def test_parent_resolution_is_order_independent(tmp_path, shuffle_seed):
    """Property #2: shuffling the `accounts` array produces the same tree.

    Loads a synthetic fixture under several permutations; the resulting
    parent_account_id mapping must match the canonical (original-order)
    load.
    """
    canonical = _minimal_fixture()

    accounts = list(canonical["accounts"])
    if shuffle_seed == "reverse":
        accounts.reverse()
    elif isinstance(shuffle_seed, int):
        import random
        rng = random.Random(shuffle_seed)
        rng.shuffle(accounts)
    # else: keep original order

    permuted = {**canonical, "accounts": accounts}
    fix = _write_fixture(tmp_path, permuted)
    call_command("seed_default_coa", "--fixture", str(fix))

    # Every account's parent must match the fixture, regardless of order.
    for raw in canonical["accounts"]:
        child = Account.objects.get(account_number=raw["account_number"])
        if raw["parent_account_number"] is None:
            assert child.parent_account_id is None
        else:
            parent = Account.objects.get(
                account_number=raw["parent_account_number"]
            )
            assert child.parent_account_id == parent.id


# ---------------------------------------------------------------------------
# Schema / hierarchy validation (refusal before any DB write)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_unknown_parent_reference_refuses(tmp_path):
    fixture = _minimal_fixture()
    fixture["accounts"][1]["parent_account_number"] = "9-9999"  # nonexistent
    fix = _write_fixture(tmp_path, fixture)
    with pytest.raises(CommandError) as exc:
        call_command("seed_default_coa", "--fixture", str(fix))
    assert "unknown" in str(exc.value).lower()
    assert Account.objects.count() == 0


@pytest.mark.django_db
def test_hierarchy_cycle_refuses(tmp_path):
    fixture = _minimal_fixture()
    # 1-0002 → 1-0001 → 1-0002 (cycle)
    fixture["accounts"][0]["parent_account_number"] = "1-0002"
    fix = _write_fixture(tmp_path, fixture)
    with pytest.raises(CommandError) as exc:
        call_command("seed_default_coa", "--fixture", str(fix))
    assert "cycle" in str(exc.value).lower()


@pytest.mark.django_db
def test_hierarchy_depth_5_refuses(tmp_path):
    fixture = _minimal_fixture()
    # Build a 5-deep chain by appending two more accounts.
    fixture["accounts"].append({
        "account_number": "1-0004",
        "name": "L4",
        "full_path": "Cash:Checking:Sub:L4",
        "type": "Asset",
        "normal_balance": "Debit",
        "parent_account_number": "1-0002",  # depth-3 parent
        "is_active": True,
        "is_system": False,
        "display_order": 0,
        "description": None,
        "tax_category": None,
    })
    fixture["accounts"].append({
        "account_number": "1-0005",
        "name": "L5",
        "full_path": "Cash:Checking:Sub:L4:L5",
        "type": "Asset",
        "normal_balance": "Debit",
        "parent_account_number": "1-0004",  # depth-4 parent → child is depth-5
        "is_active": True,
        "is_system": False,
        "display_order": 0,
        "description": None,
        "tax_category": None,
    })
    fix = _write_fixture(tmp_path, fixture)
    with pytest.raises(CommandError) as exc:
        call_command("seed_default_coa", "--fixture", str(fix))
    assert "depth" in str(exc.value).lower()


@pytest.mark.django_db
def test_missing_top_level_key_refuses(tmp_path):
    fix = _write_fixture(tmp_path, {"system_accounts": []})  # no "accounts"
    with pytest.raises(CommandError) as exc:
        call_command("seed_default_coa", "--fixture", str(fix))
    assert "missing" in str(exc.value).lower()


@pytest.mark.django_db
def test_invalid_json_refuses(tmp_path):
    p = tmp_path / "broken.json"
    p.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(CommandError) as exc:
        call_command("seed_default_coa", "--fixture", str(p))
    assert "Invalid JSON" in str(exc.value)


@pytest.mark.django_db
def test_missing_fixture_file_refuses(tmp_path):
    with pytest.raises(CommandError) as exc:
        call_command("seed_default_coa", "--fixture", str(tmp_path / "nope.json"))
    assert "not found" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# Field coercion
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_loader_lowercases_capitalized_type_and_normal_balance(tmp_path):
    fix = _write_fixture(tmp_path, _minimal_fixture())
    call_command("seed_default_coa", "--fixture", str(fix))
    cash = Account.objects.get(account_number="1-0001")
    assert cash.type == AccountType.ASSET
    assert cash.normal_balance == NormalBalance.DEBIT


@pytest.mark.django_db
def test_loader_coerces_null_description_and_tax_category(tmp_path):
    fix = _write_fixture(tmp_path, _minimal_fixture())
    call_command("seed_default_coa", "--fixture", str(fix))
    cash = Account.objects.get(account_number="1-0001")
    assert cash.description == ""
    assert cash.tax_category == ""


@pytest.mark.django_db
def test_loader_preserves_display_order(tmp_path):
    """System accounts use negative display_order; user accounts use 0."""
    fix = _write_fixture(tmp_path, _minimal_fixture())
    call_command("seed_default_coa", "--fixture", str(fix))
    sys_acct = Account.objects.get(account_number="3-9000")
    user_acct = Account.objects.get(account_number="1-0001")
    assert sys_acct.display_order == -300
    assert user_acct.display_order == 0
