"""
Restore an encrypted B2 backup into a target database.

Usage:
    ./manage.py restore_db <backup_id> --into <db-url>

Pipeline:
    1. B2 download backup_id to temp file.
    2. age -d -i $AGE_PRIVATE_KEY_FILE → temp .sql file.
    3. pg_restore --clean --if-exists --no-owner into --into URL.
    4. AuditLog row: BACKUP_RESTORED with backup_id, target host
       (NOT credentials), duration.

Safety:
    --into is REQUIRED (no default; refuses to silently target prod).
    Refuses if --into resolves to the same (host, port, database) as
    $DATABASE_URL UNLESS --confirm-prod-restore is also passed. The
    double-flag pattern matches reset_coa --confirm-destroy and the
    broader deliberate-ops-action shape from decisions #22, #23.

Env vars:
    AGE_PRIVATE_KEY_FILE  path to age private key (e.g.,
                          ~/.config/age/family-books.key, chmod 600).
                          Recovery procedure if lost: docs/ROLLBACK.md.
    PG_RESTORE_BIN        override path to pg_restore (default: "pg_restore")
    AGE_BIN               override path to age (default: "age")
    B2_*                  storage env vars (see books/core/backup/storage.py)
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Decrypt and restore a B2 backup into a target database."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "backup_id",
            help="B2 object key (e.g., db-2026-04-25-030000.sql.age).",
        )
        parser.add_argument(
            "--into",
            required=True,
            help=(
                "Target Postgres URL (postgres://...). REQUIRED — "
                "the command refuses to default to DATABASE_URL."
            ),
        )
        parser.add_argument(
            "--confirm-prod-restore",
            action="store_true",
            help=(
                "Required when --into matches the production DATABASE_URL "
                "host:port:database. Without this flag, the restore "
                "refuses to overwrite prod."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from books.audit.models import AuditAction, AuditLog
        from books.core.backup.storage import build_default_storage

        backup_id: str = options["backup_id"]
        into_url: str = options["into"]
        confirm_prod: bool = options["confirm_prod_restore"]

        self._guard_prod_target(into_url, confirm_prod)

        started = datetime.now(UTC)

        with tempfile.TemporaryDirectory() as tmpdir_str:
            tmpdir = Path(tmpdir_str)
            encrypted_path = tmpdir / "dump.sql.age"
            decrypted_path = tmpdir / "dump.sql"

            storage = build_default_storage()
            storage.download(backup_id, encrypted_path)

            self._age_decrypt(encrypted_path, decrypted_path)
            self._pg_restore(decrypted_path, into_url)

        duration_seconds = (datetime.now(UTC) - started).total_seconds()
        target_host = urlparse(into_url).hostname or "<unknown>"

        AuditLog.record(
            entity_type="Backup",
            entity_id=backup_id,
            action=AuditAction.BACKUP_RESTORED,
            reason=f"restore into {target_host}",
            after={
                "backup_id": backup_id,
                "target_host": target_host,
                "target_database": urlparse(into_url).path.lstrip("/"),
                "duration_seconds": round(duration_seconds, 2),
                "confirmed_prod_restore": confirm_prod,
            },
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Restored {backup_id} into {target_host} "
                f"in {duration_seconds:.1f}s."
            )
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _guard_prod_target(self, into_url: str, confirm_prod: bool) -> None:
        prod_url = os.environ.get("DATABASE_URL", "").strip()
        if not prod_url:
            return  # No DATABASE_URL set → no prod to compare against.

        prod_target = self._target_tuple(prod_url)
        into_target = self._target_tuple(into_url)
        if prod_target == into_target and not confirm_prod:
            raise CommandError(
                f"--into {into_target} matches the production database "
                "(per DATABASE_URL). Pass --confirm-prod-restore to "
                "explicitly authorize overwriting prod. This is a "
                "destructive operation; the safety flag is required."
            )

    def _target_tuple(self, db_url: str) -> tuple[str, int, str]:
        parsed = urlparse(db_url)
        return (
            parsed.hostname or "",
            parsed.port or 5432,
            parsed.path.lstrip("/"),
        )

    def _age_decrypt(
        self, encrypted_path: Path, decrypted_path: Path,
    ) -> None:
        bin_path = os.environ.get("AGE_BIN", "age")
        key_file = os.environ.get("AGE_PRIVATE_KEY_FILE", "").strip()
        if not key_file:
            raise CommandError(
                "AGE_PRIVATE_KEY_FILE env var is required. The private "
                "key decrypts B2 backups. Recovery procedure for a lost "
                "key is in docs/ROLLBACK.md (LastPass-stored copy)."
            )
        if not Path(key_file).exists():
            raise CommandError(
                f"AGE_PRIVATE_KEY_FILE points at {key_file!r} which "
                "does not exist on disk. Restore the key from LastPass "
                "(see docs/ROLLBACK.md) before retrying."
            )

        # S603: env-controlled args + shell=False (default).
        with (
            open(encrypted_path, "rb") as inp,
            open(decrypted_path, "wb") as out,
        ):
            result = subprocess.run(  # noqa: S603
                [bin_path, "-d", "-i", key_file],
                stdin=inp,
                stdout=out,
                stderr=subprocess.PIPE,
                check=False,
            )
        if result.returncode != 0:
            raise CommandError(
                f"age decrypt failed (exit {result.returncode}): "
                f"{result.stderr.decode('utf-8', 'replace').strip()[:500]}"
            )

    def _pg_restore(self, dump_path: Path, into_url: str) -> None:
        bin_path = os.environ.get("PG_RESTORE_BIN", "pg_restore")
        # --clean --if-exists drops existing objects before recreating;
        # --no-owner skips ALTER OWNER statements (target may run as a
        # different role than prod).
        result = subprocess.run(  # noqa: S603
            [bin_path, "--clean", "--if-exists", "--no-owner",
             "--dbname", into_url, str(dump_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise CommandError(
                f"pg_restore failed (exit {result.returncode}): "
                f"{result.stderr.strip()[:500]}"
            )
