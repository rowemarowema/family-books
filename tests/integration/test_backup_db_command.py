"""Tests for `backup_db` management command.

Mocks subprocess (pg_dump + age) and the B2Storage class. Verifies:
- Pipeline orchestration (pg_dump → age → upload → prune)
- Refusal contract (missing AGE_RECIPIENT, pg_dump failure, age failure)
- Audit row shape
- Retention pruning is invoked with correct arguments
- --dry-run skips upload + prune

Real B2 round-trip is the H.3 drill, run locally against a real
bucket — not in CI (no credentials, no need for paid B2 traffic per
PR run).
"""
from __future__ import annotations

import io
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from books.audit.models import AuditAction, AuditLog
from books.core.backup.retention import BackupObject


@pytest.fixture
def fake_pg_dump():
    """Patch subprocess.run for pg_dump + age. The fake pg_dump writes
    a placeholder file; the fake age copies it (so the encrypted file
    has predictable size)."""
    def _run(cmd, *args, **kwargs):
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        # Detect which command and write the appropriate output file.
        if "pg_dump" in cmd[0] or cmd[0].endswith("pg_dump"):
            # --file <path> is the last but one arg; final arg is db_url.
            file_idx = cmd.index("--file") + 1
            Path(cmd[file_idx]).write_bytes(b"FAKE_PG_DUMP_BYTES")
        elif "age" in cmd[0]:
            # age reads from stdin, writes to stdout. The wrapper passes
            # open file handles via stdin= and stdout=. Read input,
            # write a "fake-encrypted" version (just prepend a header).
            stdin = kwargs["stdin"]
            stdout = kwargs["stdout"]
            stdout.write(b"FAKE_AGE_HEADER:")
            stdout.write(stdin.read())
            stdout.flush()
        return result
    with patch("subprocess.run", side_effect=_run) as mock:
        yield mock


@pytest.fixture
def fake_storage():
    """Mock B2Storage; tracks upload/list/delete calls."""
    storage = MagicMock()
    storage.upload.return_value = 100
    storage.list_objects.return_value = []
    # Patch at the source module — the command does a late import via
    # `from books.core.backup.storage import build_default_storage`,
    # so the bound name lives on the source module, not on the
    # command's namespace.
    with patch(
        "books.core.backup.storage.build_default_storage",
        return_value=storage,
    ):
        yield storage


@pytest.fixture
def env_setup(monkeypatch):
    monkeypatch.setenv("AGE_RECIPIENT", "age1testrecipientpublickey")
    monkeypatch.setenv("DATABASE_URL", "postgres://x:y@localhost/test")


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_prefix_flag_overrides_b2_prefix_env(env_setup, fake_pg_dump):
    """`--prefix dev-test/` invokes build_default_storage with
    prefix_override="dev-test/". Used by drill_rollback to keep
    drill artifacts out of the production backup-history prefix."""
    storage = MagicMock()
    storage.upload.return_value = 100
    storage.list_objects.return_value = []
    with patch(
        "books.core.backup.storage.build_default_storage",
        return_value=storage,
    ) as factory:
        call_command("backup_db", "--prefix", "dev-test/")

    # The factory was called with the override.
    factory.assert_called_once_with(prefix_override="dev-test/")


@pytest.mark.django_db
def test_no_prefix_flag_uses_env_default(env_setup, fake_pg_dump):
    """Without --prefix, build_default_storage receives None and
    falls back to whatever B2_PREFIX env says (the cron path)."""
    storage = MagicMock()
    storage.upload.return_value = 100
    storage.list_objects.return_value = []
    with patch(
        "books.core.backup.storage.build_default_storage",
        return_value=storage,
    ) as factory:
        call_command("backup_db")

    factory.assert_called_once_with(prefix_override=None)


@pytest.mark.django_db
def test_backup_uploads_and_writes_audit(env_setup, fake_pg_dump, fake_storage):
    out = io.StringIO()
    call_command("backup_db", stdout=out)

    # Storage was called.
    assert fake_storage.upload.called
    upload_args = fake_storage.upload.call_args
    local_path = upload_args[0][0]
    object_key = upload_args[0][1]
    assert isinstance(local_path, Path)
    assert object_key.startswith("db-")
    assert object_key.endswith(".sql.age")

    # Audit row.
    audits = AuditLog.objects.filter(action=AuditAction.BACKUP_CREATED)
    assert audits.count() == 1
    after = audits.get().after_value
    assert after["object_key"] == object_key
    assert after["size_bytes"] > 0
    assert after["retention_pruned_count"] == 0  # empty list_objects → no prune


@pytest.mark.django_db
def test_backup_prunes_old_objects(env_setup, fake_pg_dump, fake_storage):
    """After upload, the command lists all B2 objects and deletes
    those outside the retention window."""
    today = date.today()
    # 50 daily backups going back 50 days. Retention keeps last 30
    # daily; 20 should be deleted.
    from datetime import timedelta
    fake_storage.list_objects.return_value = [
        BackupObject(
            key=f"db-{(today - timedelta(days=i)).isoformat()}-000000.sql.age",
            taken_on=today - timedelta(days=i),
        )
        for i in range(50)
    ]

    call_command("backup_db")

    # Some objects should have been deleted (those outside the daily
    # window). Exact count depends on monthly/annual coverage too.
    assert fake_storage.delete.called
    delete_count = fake_storage.delete.call_count
    assert delete_count > 0

    # Audit row records the prune count.
    audit = AuditLog.objects.filter(action=AuditAction.BACKUP_CREATED).get()
    assert audit.after_value["retention_pruned_count"] == delete_count


@pytest.mark.django_db
def test_dry_run_skips_upload_and_prune(env_setup, fake_pg_dump, fake_storage):
    out = io.StringIO()
    call_command("backup_db", "--dry-run", stdout=out)

    assert "DRY RUN" in out.getvalue()
    fake_storage.upload.assert_not_called()
    fake_storage.list_objects.assert_not_called()
    fake_storage.delete.assert_not_called()
    # No audit row in dry-run.
    assert AuditLog.objects.filter(action=AuditAction.BACKUP_CREATED).count() == 0


# ---------------------------------------------------------------------------
# Refusal contract
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_missing_age_recipient_refuses(monkeypatch, fake_pg_dump, fake_storage):
    monkeypatch.delenv("AGE_RECIPIENT", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgres://x:y@localhost/test")
    with pytest.raises(CommandError) as exc:
        call_command("backup_db")
    assert "AGE_RECIPIENT" in str(exc.value)


@pytest.mark.django_db
def test_pg_dump_failure_refuses(env_setup, fake_storage):
    """Simulate pg_dump returning non-zero. Backup must abort before
    upload + before any audit row is written."""
    def _run(cmd, *args, **kwargs):
        result = MagicMock()
        if "pg_dump" in cmd[0] or cmd[0].endswith("pg_dump"):
            result.returncode = 1
            result.stderr = "FATAL: connection refused"
        else:
            result.returncode = 0
            result.stderr = ""
        return result
    with patch("subprocess.run", side_effect=_run):
        with pytest.raises(CommandError) as exc:
            call_command("backup_db")
        assert "pg_dump failed" in str(exc.value)
        assert "connection refused" in str(exc.value)
    fake_storage.upload.assert_not_called()
    assert AuditLog.objects.filter(action=AuditAction.BACKUP_CREATED).count() == 0


@pytest.mark.django_db
def test_age_failure_refuses(env_setup, fake_storage):
    def _run(cmd, *args, **kwargs):
        result = MagicMock()
        if "pg_dump" in cmd[0] or cmd[0].endswith("pg_dump"):
            file_idx = cmd.index("--file") + 1
            Path(cmd[file_idx]).write_bytes(b"DUMP")
            result.returncode = 0
            result.stderr = ""
        elif "age" in cmd[0]:
            result.returncode = 1
            result.stderr = b"age: bad recipient"
        return result
    with patch("subprocess.run", side_effect=_run):
        with pytest.raises(CommandError) as exc:
            call_command("backup_db")
        assert "age failed" in str(exc.value)
    fake_storage.upload.assert_not_called()


# ---------------------------------------------------------------------------
# Retention prune failure is non-fatal
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_retention_failure_does_not_abort_backup(
    env_setup, fake_pg_dump, fake_storage,
):
    """If listing or deleting B2 objects fails AFTER successful upload,
    the backup itself is still considered successful — the audit row
    is written and the next run retries the prune."""
    fake_storage.list_objects.side_effect = RuntimeError("B2 unavailable")

    call_command("backup_db")

    fake_storage.upload.assert_called_once()
    audits = AuditLog.objects.filter(action=AuditAction.BACKUP_CREATED)
    assert audits.count() == 1
    # Prune count is 0 because the prune step crashed before doing
    # anything.
    assert audits.get().after_value["retention_pruned_count"] == 0
