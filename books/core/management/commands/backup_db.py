"""
Encrypted Postgres backup → Backblaze B2.

Pipeline:
    1. pg_dump --format=custom of $DATABASE_URL to a temp file.
    2. age -r $AGE_RECIPIENT < dump > dump.age (encrypted).
    3. boto3 upload to B2 at <prefix>/db-YYYY-MM-DD-HHMMSS.sql.age.
    4. Apply retention policy (30 daily + 12 monthly + 7 annual);
       delete keys outside the keep set.
    5. AuditLog row: BACKUP_CREATED with object key, size, duration,
       and the count of pruned objects.

Env vars:
    DATABASE_URL          (Django DATABASES default)
    AGE_RECIPIENT         age public key for encryption
    B2_ENDPOINT_URL etc.  (see books/core/backup/storage.py)
    PG_DUMP_BIN           override path to pg_dump (default: "pg_dump")
    AGE_BIN               override path to age (default: "age")

Failure modes:
    - pg_dump returns non-zero          → CommandError (pre-encrypt; nothing
                                          uploaded)
    - age fails / AGE_RECIPIENT missing → CommandError (pre-upload)
    - B2 upload fails                   → CommandError (no audit row)
    - Retention prune fails             → log warning + continue (the
                                          backup itself succeeded; pruning
                                          retries on the next run)

The dry-run flag short-circuits the upload + retention but still runs
pg_dump + encrypt to verify the toolchain. Useful for verifying the
toolchain locally without spending B2 storage.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Create an encrypted Postgres backup and upload to Backblaze B2."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--reason",
            default="manual",
            help="AuditLog reason. Cron sets this to 'scheduled'.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Run pg_dump + encrypt; skip B2 upload and retention.",
        )
        parser.add_argument(
            "--prefix",
            default=None,
            help=(
                "Override B2_PREFIX env. drill_rollback passes "
                "dev-test/ so drill artifacts never co-mingle with "
                "production backup history. See docs/ROLLBACK.md."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from books.audit.models import AuditAction, AuditLog
        from books.core.backup.retention import select_for_deletion
        from books.core.backup.storage import (
            build_default_storage,
            make_object_key,
        )

        reason: str = options["reason"]
        dry_run: bool = options["dry_run"]
        prefix_override: str | None = options.get("prefix")
        started = datetime.now(UTC)

        db_url = self._resolve_database_url()
        recipient = self._resolve_recipient()

        with tempfile.TemporaryDirectory() as tmpdir_str:
            tmpdir = Path(tmpdir_str)
            dump_path = tmpdir / "dump.sql"
            encrypted_path = tmpdir / "dump.sql.age"

            self._pg_dump(db_url, dump_path)
            self._age_encrypt(recipient, dump_path, encrypted_path)

            object_key = make_object_key(started)
            size_bytes = encrypted_path.stat().st_size

            if dry_run:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"DRY RUN: would upload {size_bytes} bytes "
                        f"as {object_key}. No B2 traffic."
                    )
                )
                return

            storage = build_default_storage(prefix_override=prefix_override)
            storage.upload(encrypted_path, object_key)

            # Retention prune. Failures here are logged but don't abort
            # the run — the backup itself already succeeded.
            pruned: list[str] = []
            try:
                existing = storage.list_objects()
                pruned = select_for_deletion(existing, today=started.date())
                for key in pruned:
                    storage.delete(key)
            except Exception as exc:
                self.stderr.write(
                    self.style.WARNING(
                        f"Retention prune failed: {exc}. The backup "
                        "itself succeeded; next run will re-attempt prune."
                    )
                )

        duration_seconds = (datetime.now(UTC) - started).total_seconds()

        AuditLog.record(
            entity_type="Backup",
            entity_id=object_key,
            action=AuditAction.BACKUP_CREATED,
            reason=reason,
            after={
                "object_key": object_key,
                "size_bytes": size_bytes,
                "duration_seconds": round(duration_seconds, 2),
                "retention_pruned_count": len(pruned),
                "retention_pruned_keys": pruned,
            },
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Backup uploaded: {object_key} ({size_bytes} bytes) "
                f"in {duration_seconds:.1f}s; pruned {len(pruned)} old object(s)."
            )
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_database_url(self) -> str:
        url = os.environ.get("DATABASE_URL")
        if url:
            return url
        # Fall back to Django settings — useful in dev where the env
        # var may not be set.
        from django.conf import settings
        db = settings.DATABASES["default"]
        engine = db.get("ENGINE", "")
        if "postgres" not in engine:
            raise CommandError(
                f"backup_db requires Postgres; got ENGINE={engine}."
            )
        return (
            f"postgresql://{db.get('USER', '')}:{db.get('PASSWORD', '')}"
            f"@{db.get('HOST', 'localhost')}:{db.get('PORT', '5432')}"
            f"/{db.get('NAME', '')}"
        )

    def _resolve_recipient(self) -> str:
        recipient = os.environ.get("AGE_RECIPIENT", "").strip()
        if not recipient:
            raise CommandError(
                "AGE_RECIPIENT env var is required. The recipient is the "
                "age public key (e.g., age1...) used to encrypt the dump. "
                "See docs/ROLLBACK.md for key management."
            )
        return recipient

    def _pg_dump(self, db_url: str, dump_path: Path) -> None:
        bin_path = os.environ.get("PG_DUMP_BIN", "pg_dump")
        # S603: subprocess args come from env vars (DATABASE_URL,
        # PG_DUMP_BIN) — operator-controlled, not user input. shell=False
        # (default) eliminates injection risk via the args. The bin_path
        # default "pg_dump" relies on PATH; we trust the deploy env.
        result = subprocess.run(  # noqa: S603
            [bin_path, "--format=custom", "--no-owner", "--file", str(dump_path), db_url],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise CommandError(
                f"pg_dump failed (exit {result.returncode}): "
                f"{result.stderr.strip()[:500]}"
            )

    def _age_encrypt(
        self, recipient: str, dump_path: Path, encrypted_path: Path,
    ) -> None:
        bin_path = os.environ.get("AGE_BIN", "age")
        # S603: same posture as _pg_dump above — env-controlled args,
        # shell=False, operator trust boundary.
        with open(dump_path, "rb") as inp, open(encrypted_path, "wb") as out:
            result = subprocess.run(  # noqa: S603
                [bin_path, "-r", recipient],
                stdin=inp,
                stdout=out,
                stderr=subprocess.PIPE,
                check=False,
            )
        if result.returncode != 0:
            raise CommandError(
                f"age failed (exit {result.returncode}): "
                f"{result.stderr.decode('utf-8', 'replace').strip()[:500]}"
            )
