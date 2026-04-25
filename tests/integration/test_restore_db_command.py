"""Tests for `restore_db` management command.

Mocks B2Storage and subprocess (age, pg_restore). Real round-trip
is the H.3 drill, run locally against a real B2 bucket.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from books.audit.models import AuditAction, AuditLog


@pytest.fixture
def fake_subprocess(tmp_path):
    """Fake age + pg_restore. age writes a placeholder; pg_restore
    just succeeds."""
    def _run(cmd, *args, **kwargs):
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        if "age" in cmd[0]:
            stdout = kwargs["stdout"]
            stdout.write(b"FAKE_DECRYPTED_DUMP")
            stdout.flush()
        elif "pg_restore" in cmd[0]:
            result.stdout = ""
        return result
    with patch("subprocess.run", side_effect=_run) as mock:
        yield mock


@pytest.fixture
def fake_storage():
    storage = MagicMock()
    def _download(key, local_path):
        Path(local_path).write_bytes(b"FAKE_ENCRYPTED")
    storage.download.side_effect = _download
    with patch(
        "books.core.backup.storage.build_default_storage",
        return_value=storage,
    ):
        yield storage


@pytest.fixture
def env_setup(monkeypatch, tmp_path):
    """Provides an AGE_PRIVATE_KEY_FILE that exists, plus a sentinel
    DATABASE_URL we can compare against."""
    key_file = tmp_path / "age.key"
    key_file.write_text("AGE-SECRET-KEY-1FAKEFAKEFAKE")
    monkeypatch.setenv("AGE_PRIVATE_KEY_FILE", str(key_file))
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgres://prod_user:prod_pw@prod-host:5432/family_books",
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_restore_into_scratch_db_succeeds(
    env_setup, fake_subprocess, fake_storage,
):
    call_command(
        "restore_db", "db-2026-04-25-030000.sql.age",
        "--into", "postgres://x:y@localhost:5433/family_books_drill",
    )

    fake_storage.download.assert_called_once()
    audits = AuditLog.objects.filter(action=AuditAction.BACKUP_RESTORED)
    assert audits.count() == 1
    after = audits.get().after_value
    assert after["backup_id"] == "db-2026-04-25-030000.sql.age"
    assert after["target_host"] == "localhost"
    assert after["target_database"] == "family_books_drill"
    assert after["confirmed_prod_restore"] is False


# ---------------------------------------------------------------------------
# Safety: --into REQUIRED + prod double-flag
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_into_flag_required(env_setup):
    with pytest.raises(CommandError):
        call_command("restore_db", "db-something.sql.age")


@pytest.mark.django_db
def test_restoring_into_prod_without_confirm_refuses(
    env_setup, fake_subprocess, fake_storage,
):
    """--into with the same (host, port, database) as DATABASE_URL
    requires --confirm-prod-restore."""
    with pytest.raises(CommandError) as exc:
        call_command(
            "restore_db", "db-something.sql.age",
            "--into", "postgres://anyone:any@prod-host:5432/family_books",
        )
    assert "--confirm-prod-restore" in str(exc.value)
    fake_storage.download.assert_not_called()


@pytest.mark.django_db
def test_restoring_into_prod_with_confirm_succeeds(
    env_setup, fake_subprocess, fake_storage,
):
    call_command(
        "restore_db", "db-2026-04-25-030000.sql.age",
        "--into", "postgres://anyone:any@prod-host:5432/family_books",
        "--confirm-prod-restore",
    )
    audit = AuditLog.objects.filter(action=AuditAction.BACKUP_RESTORED).get()
    assert audit.after_value["confirmed_prod_restore"] is True


@pytest.mark.django_db
def test_different_database_name_is_not_prod(
    env_setup, fake_subprocess, fake_storage,
):
    """Same host/port but different database name is NOT prod —
    safety check uses the (host, port, database) tuple, not host alone."""
    call_command(
        "restore_db", "db-2026-04-25-030000.sql.age",
        "--into", "postgres://x:y@prod-host:5432/family_books_drill",
    )
    # No prod confirmation needed.
    fake_storage.download.assert_called_once()


# ---------------------------------------------------------------------------
# Refusal: missing key file
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_missing_age_key_file_refuses(monkeypatch, fake_storage):
    """If AGE_PRIVATE_KEY_FILE points at a path that doesn't exist,
    the command refuses with a hint about LastPass recovery."""
    monkeypatch.setenv("AGE_PRIVATE_KEY_FILE", "/tmp/nope-does-not-exist.key")  # noqa: S108
    monkeypatch.setenv("DATABASE_URL", "postgres://x:y@localhost/test")
    with pytest.raises(CommandError) as exc:
        call_command(
            "restore_db", "db-something.sql.age",
            "--into", "postgres://x:y@localhost:5433/family_books_drill",
        )
    assert "LastPass" in str(exc.value)


@pytest.mark.django_db
def test_unset_age_key_file_env_refuses(monkeypatch, fake_storage):
    monkeypatch.delenv("AGE_PRIVATE_KEY_FILE", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgres://x:y@localhost/test")
    with pytest.raises(CommandError) as exc:
        call_command(
            "restore_db", "db-something.sql.age",
            "--into", "postgres://x:y@localhost:5433/family_books_drill",
        )
    assert "AGE_PRIVATE_KEY_FILE" in str(exc.value)


# ---------------------------------------------------------------------------
# Refusal: subprocess failures
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_age_decrypt_failure_refuses(env_setup, fake_storage):
    def _run(cmd, *args, **kwargs):
        result = MagicMock()
        if "age" in cmd[0]:
            result.returncode = 1
            result.stderr = b"age: bad key"
        else:
            result.returncode = 0
        return result
    with patch("subprocess.run", side_effect=_run):
        with pytest.raises(CommandError) as exc:
            call_command(
                "restore_db", "db-something.sql.age",
                "--into", "postgres://x:y@localhost:5433/family_books_drill",
            )
        assert "age decrypt failed" in str(exc.value)
    # No audit row on failure.
    assert AuditLog.objects.filter(action=AuditAction.BACKUP_RESTORED).count() == 0


@pytest.mark.django_db
def test_pg_restore_failure_refuses(env_setup, fake_storage):
    def _run(cmd, *args, **kwargs):
        result = MagicMock()
        if "age" in cmd[0]:
            result.returncode = 0
            kwargs["stdout"].write(b"FAKE")
            kwargs["stdout"].flush()
            result.stderr = b""
        elif "pg_restore" in cmd[0]:
            result.returncode = 1
            result.stderr = "pg_restore: connection refused"
        return result
    with patch("subprocess.run", side_effect=_run):
        with pytest.raises(CommandError) as exc:
            call_command(
                "restore_db", "db-something.sql.age",
                "--into", "postgres://x:y@localhost:5433/family_books_drill",
            )
        assert "pg_restore failed" in str(exc.value)
    assert AuditLog.objects.filter(action=AuditAction.BACKUP_RESTORED).count() == 0
