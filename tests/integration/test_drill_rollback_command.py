"""Tests for `drill_rollback` management command.

The drill orchestrates backup_db + restore_db + verification queries.
Tests mock the inner commands and the psycopg target connection so
the orchestration logic is exercised without real B2 / scratch DB.

The REAL drill is run-once-locally by Mark against the actual
toolchain (real local Postgres scratch DB, real B2 bucket prefix
dev-test/). That run is recorded in docs/ROLLBACK.md with date +
duration; CI only validates the orchestration code paths.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from books.accounting.factories import AccountFactory, EquityAccountFactory
from books.accounting.models import AccountType, NormalBalance
from books.accounting.opening_balances import (
    OPENING_BALANCE_EQUITY_ACCOUNT_NUMBER,
    set_opening_balance,
)
from books.audit.models import AuditAction, AuditLog

# ---------------------------------------------------------------------------
# Dry-run mode (no B2, no scratch DB; verifies orchestration only)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_drill_dry_run_writes_passed_audit_row(monkeypatch):
    """Without --use-b2, the drill calls backup_db --dry-run and
    skips the restore + verification entirely. Still writes a
    BACKUP_DRILL_PASSED audit row marking the dry-run."""
    # backup_db needs AGE_RECIPIENT + DATABASE_URL even in dry-run.
    monkeypatch.setenv("AGE_RECIPIENT", "age1fake")
    monkeypatch.setenv("DATABASE_URL", "postgres://x:y@localhost/x")

    def _fake_run(cmd, *args, **kwargs):
        result = MagicMock(returncode=0, stderr="")
        if "pg_dump" in cmd[0]:
            from pathlib import Path
            file_idx = cmd.index("--file") + 1
            Path(cmd[file_idx]).write_bytes(b"DUMP")
        elif "age" in cmd[0]:
            kwargs["stdout"].write(b"ENCRYPTED")
            kwargs["stdout"].flush()
        return result

    with patch("subprocess.run", side_effect=_fake_run):
        out = StringIO()
        call_command("drill_rollback", stdout=out)

    assert "PASSED" in out.getvalue()
    assert AuditLog.objects.filter(
        action=AuditAction.BACKUP_DRILL_PASSED,
    ).count() == 1
    audit = AuditLog.objects.filter(
        action=AuditAction.BACKUP_DRILL_PASSED,
    ).get()
    assert audit.after_value["use_b2"] is False


@pytest.mark.django_db
def test_drill_use_b2_requires_scratch_db_env(monkeypatch):
    monkeypatch.delenv("DRILL_SCRATCH_DB", raising=False)
    with pytest.raises(CommandError) as exc:
        call_command("drill_rollback", "--use-b2")
    assert "DRILL_SCRATCH_DB" in str(exc.value)


# ---------------------------------------------------------------------------
# Full drill orchestration (mocked B2 + mocked psycopg target)
# ---------------------------------------------------------------------------


@pytest.fixture
def populated_books(db):
    """Minimal source DB state: 1 system account + 1 user account
    with a posted opening balance. Lets the drill verify a real
    spot-check value (BOA Savings = 8500.00)."""
    EquityAccountFactory(
        account_number=OPENING_BALANCE_EQUITY_ACCOUNT_NUMBER,
        name="Opening Balance Equity",
        is_system=True,
        display_order=-200,
    )
    boa = AccountFactory(
        account_number="1-0179",
        name="BOA - Savings",
        type=AccountType.ASSET,
        normal_balance=NormalBalance.DEBIT,
    )
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user = User.objects.first() or User.objects.create_superuser(
        username="x@x.com", email="x@x.com", password="x" * 20,
    )
    set_opening_balance(
        boa, amount=Decimal("8500.00"),
        as_of=date(2001, 1, 1), user=user,
    )
    return boa


@pytest.fixture
def drill_env(monkeypatch, tmp_path):
    monkeypatch.setenv("AGE_RECIPIENT", "age1fake")
    monkeypatch.setenv("DATABASE_URL", "postgres://x:y@localhost/x")
    key_file = tmp_path / "age.key"
    key_file.write_text("AGE-SECRET-KEY-1FAKE")
    monkeypatch.setenv("AGE_PRIVATE_KEY_FILE", str(key_file))
    monkeypatch.setenv(
        "DRILL_SCRATCH_DB",
        "postgres://x:y@localhost:5433/family_books_drill",
    )


@pytest.fixture
def fake_subprocess(tmp_path):
    """pg_dump, age encrypt, age decrypt, pg_restore — all succeed."""
    def _run(cmd, *args, **kwargs):
        result = MagicMock(returncode=0, stderr="")
        if "pg_dump" in cmd[0]:
            from pathlib import Path
            file_idx = cmd.index("--file") + 1
            Path(cmd[file_idx]).write_bytes(b"DUMP")
        elif "age" in cmd[0]:
            kwargs["stdout"].write(b"FAKE")
            kwargs["stdout"].flush()
        return result
    with patch("subprocess.run", side_effect=_run) as m:
        yield m


@pytest.fixture
def fake_b2():
    storage = MagicMock()
    storage.upload.return_value = 100
    storage.list_objects.return_value = []
    def _download(key, local_path):
        from pathlib import Path
        Path(local_path).write_bytes(b"FAKE")
    storage.download.side_effect = _download
    with patch(
        "books.core.backup.storage.build_default_storage",
        return_value=storage,
    ):
        yield storage


@pytest.fixture
def fake_target_db(populated_books):
    """Mock psycopg.connect for the target DB. Returns counts that
    MATCH the source AT BACKUP TIME (pre-drill) so the drill passes.

    A real pg_dump captures source state at dump time; subsequent
    audit writes (BACKUP_CREATED, BACKUP_RESTORED) by the drill don't
    appear in the restored target. Snapshot the source counts at
    fixture setup so the mock returns the same numbers source_counts
    will see when drill_rollback queries pre-backup."""
    from books.accounting.models import Account, JournalEntry, JournalLine
    from books.audit.models import AuditLog
    snapshot = {
        "account": Account.objects.count(),
        "system_account": Account.objects.filter(is_system=True).count(),
        "journal_entry": JournalEntry.objects.count(),
        "journal_line": JournalLine.objects.count(),
        "audit_log": AuditLog.objects.count(),
    }
    boa_id = populated_books.pk

    cursor = MagicMock()

    def _execute(sql, params=None):
        cursor._last_sql = sql
        cursor._last_params = params

    def _fetchone():
        sql = cursor._last_sql.upper()
        if "FROM ACCOUNT" in sql and "WHERE IS_SYSTEM" in sql:
            return (snapshot["system_account"],)
        if "FROM ACCOUNT" in sql and "ACCOUNT_NUMBER" in sql:
            return (boa_id, "debit")
        if "FROM ACCOUNT" in sql:
            return (snapshot["account"],)
        if "FROM JOURNAL_ENTRY" in sql:
            return (snapshot["journal_entry"],)
        if "FROM JOURNAL_LINE" in sql and "WHERE JL.ACCOUNT_ID" in sql:
            # Spot-check sums for BOA: dr=8500, cr=0
            return (8500, 0)
        if "FROM JOURNAL_LINE" in sql:
            return (snapshot["journal_line"],)
        if "FROM AUDIT_LOG" in sql:
            return (snapshot["audit_log"],)
        return (0,)

    cursor.execute.side_effect = _execute
    cursor.fetchone.side_effect = _fetchone

    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor
    conn.cursor.return_value.__exit__.return_value = None

    with patch(
        "psycopg.connect",
        return_value=MagicMock(__enter__=MagicMock(return_value=conn),
                               __exit__=MagicMock(return_value=None)),
    ):
        yield conn


@pytest.mark.django_db
def test_full_drill_passes_when_counts_match(
    drill_env, fake_subprocess, fake_b2, fake_target_db,
):
    out = StringIO()
    call_command("drill_rollback", "--use-b2", stdout=out)

    assert "PASSED" in out.getvalue()
    audit = AuditLog.objects.filter(
        action=AuditAction.BACKUP_DRILL_PASSED,
    ).get()
    assert audit.after_value["use_b2"] is True
    assert audit.after_value["source_counts"]["account"] == 2
    assert audit.after_value["source_counts"]["journal_line"] == 2
    assert audit.after_value["source_counts"]["system_account"] == 1
    assert audit.after_value["spot_check_value"] == "8500.00"


def _patched_target_db(snapshot, *, override=None):
    """Return a context manager that patches psycopg.connect with a
    cursor returning `snapshot` values for the standard queries.
    `override` lets a single test perturb a specific value to simulate
    a corrupt restore."""
    boa_id = snapshot.pop("boa_id", 1)
    spot_check_sums = snapshot.pop(
        "spot_check_sums", (8500, 0),
    )
    if override:
        snapshot.update(override)

    cursor = MagicMock()

    def _execute(sql, params=None):
        cursor._last_sql = sql

    def _fetchone():
        sql = cursor._last_sql.upper()
        if "FROM ACCOUNT" in sql and "WHERE IS_SYSTEM" in sql:
            return (snapshot["system_account"],)
        if "FROM ACCOUNT" in sql and "ACCOUNT_NUMBER" in sql:
            return (boa_id, "debit")
        if "FROM ACCOUNT" in sql:
            return (snapshot["account"],)
        if "FROM JOURNAL_ENTRY" in sql:
            return (snapshot["journal_entry"],)
        if "FROM JOURNAL_LINE" in sql and "WHERE JL.ACCOUNT_ID" in sql:
            return spot_check_sums
        if "FROM JOURNAL_LINE" in sql:
            return (snapshot["journal_line"],)
        if "FROM AUDIT_LOG" in sql:
            return (snapshot["audit_log"],)
        return (0,)

    cursor.execute.side_effect = _execute
    cursor.fetchone.side_effect = _fetchone

    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor
    conn.cursor.return_value.__exit__.return_value = None

    return patch(
        "psycopg.connect",
        return_value=MagicMock(
            __enter__=MagicMock(return_value=conn),
            __exit__=MagicMock(return_value=None),
        ),
    )


def _source_snapshot(populated_books):
    from books.accounting.models import Account, JournalEntry, JournalLine
    from books.audit.models import AuditLog
    return {
        "account": Account.objects.count(),
        "system_account": Account.objects.filter(is_system=True).count(),
        "journal_entry": JournalEntry.objects.count(),
        "journal_line": JournalLine.objects.count(),
        "audit_log": AuditLog.objects.count(),
        "boa_id": populated_books.pk,
    }


@pytest.mark.django_db
def test_drill_fails_on_count_mismatch(
    drill_env, fake_subprocess, fake_b2, populated_books,
):
    """If the restored DB shows different account count, the drill
    raises with a count-mismatch reason."""
    snapshot = _source_snapshot(populated_books)
    snapshot["account"] = 99  # target reports 99; source has 2 → mismatch

    with _patched_target_db(snapshot):
        with pytest.raises(CommandError):
            call_command("drill_rollback", "--use-b2")

    failed = AuditLog.objects.filter(
        action=AuditAction.BACKUP_DRILL_FAILED,
    ).get()
    assert "mismatch" in failed.reason.lower()


@pytest.mark.django_db
def test_drill_fails_on_missing_system_accounts(
    drill_env, fake_subprocess, fake_b2, populated_books,
):
    """Source has 1 system account (OBE); target reports 0 — the
    system_account key in the count-match dict mismatches. Catches
    'restore dropped is_system=True rows' (refinement #3)."""
    snapshot = _source_snapshot(populated_books)
    snapshot["system_account"] = 0  # restore dropped the system row
    snapshot["account"] -= 1         # also drop from total

    with _patched_target_db(snapshot):
        with pytest.raises(CommandError) as exc:
            call_command("drill_rollback", "--use-b2")
    msg = str(exc.value).lower()
    assert "system_account" in msg or "mismatch" in msg


@pytest.mark.django_db
def test_drill_fails_on_spot_check_mismatch(
    drill_env, fake_subprocess, fake_b2, populated_books,
):
    """If counts match but the BOA spot-check value differs, drill
    fails. Catches the 'counts match but values corrupted' class."""
    snapshot = _source_snapshot(populated_books)
    snapshot["spot_check_sums"] = (999, 0)  # source is (8500, 0); mismatch

    with _patched_target_db(snapshot):
        with pytest.raises(CommandError) as exc:
            call_command("drill_rollback", "--use-b2")
        assert "spot-check" in str(exc.value).lower()
