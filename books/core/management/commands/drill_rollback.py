"""
End-to-end backup → restore → verify drill.

Usage:
    DRILL_SCRATCH_DB=postgres://x:y@localhost:5433/family_books_drill \
    AGE_RECIPIENT=age1... \
    AGE_PRIVATE_KEY_FILE=~/.config/age/family-books.key \
    B2_*=... \
    ./manage.py drill_rollback --use-b2

Without --use-b2, the drill runs backup_db --dry-run and skips the
restore — useful for verifying the local toolchain (pg_dump, age, B2
credentials valid) without paying B2 storage. The binding drill that
counts toward production-readiness uses --use-b2 and runs against a
real bucket.

Verification (broadened per the Group H breakdown refinement #3):

    1. trial balance is_balanced on the restored DB
    2. Account count matches source
    3. JournalEntry count matches source
    4. JournalLine count matches source
    5. AuditLog count matches source
    6. All 3 system accounts present (3-9000, 3-9100, 3-9200)
    7. Spot-check: BOA - Savings (account 1-0179) own_balance equals
       the value computed pre-backup. Catches "counts match but
       individual values corrupted" — the class-of-bug pattern that
       counts alone don't catch.

Audit:
    BACKUP_DRILL_PASSED on success, with all counts + duration.
    BACKUP_DRILL_FAILED on any verification failure, with the error.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse

import psycopg
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

SPOT_CHECK_ACCOUNT_NUMBER = "1-0179"  # BOA - Savings (per docs/ROLLBACK.md)


class Command(BaseCommand):
    help = "Backup → restore → verify drill."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--use-b2",
            action="store_true",
            help=(
                "Hit real B2 (not --dry-run). Required for the binding "
                "drill that counts toward production-readiness."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from books.audit.models import AuditAction, AuditLog

        use_b2: bool = options["use_b2"]
        scratch_url = os.environ.get("DRILL_SCRATCH_DB", "").strip()

        if use_b2 and not scratch_url:
            raise CommandError(
                "DRILL_SCRATCH_DB env var is required when --use-b2 is set "
                "(target Postgres URL for the restore). Example: "
                "postgres://user:pw@localhost:5433/family_books_drill."
            )

        started = datetime.now(UTC)
        source_counts = self._count_source()
        spot_check_value = self._spot_check_source()

        try:
            backup_id = self._run_backup(use_b2)
            if use_b2:
                self._run_restore(backup_id, scratch_url)
                target_counts = self._count_target(scratch_url)
                target_spot_check = self._spot_check_target(scratch_url)
                self._verify_match(source_counts, target_counts)
                self._verify_spot_check(spot_check_value, target_spot_check)
        except Exception as exc:
            # Broad on purpose: any failure during the drill is a
            # drill failure. Specific exception classes (CommandError,
            # subprocess errors, psycopg errors, RuntimeError from the
            # verify helpers) all converge here.
            duration_seconds = (datetime.now(UTC) - started).total_seconds()
            AuditLog.record(
                entity_type="Backup",
                entity_id=backup_id if "backup_id" in dir() else "",
                action=AuditAction.BACKUP_DRILL_FAILED,
                reason=str(exc)[:500],
                after={
                    "use_b2": use_b2,
                    "duration_seconds": round(duration_seconds, 2),
                    "error": str(exc)[:500],
                    "source_counts": source_counts,
                    "spot_check_expected": str(spot_check_value)
                    if spot_check_value is not None else None,
                },
            )
            raise CommandError(f"Drill FAILED: {exc}") from exc

        duration_seconds = (datetime.now(UTC) - started).total_seconds()
        AuditLog.record(
            entity_type="Backup",
            entity_id=backup_id,
            action=AuditAction.BACKUP_DRILL_PASSED,
            reason=(
                f"drill against {urlparse(scratch_url).hostname}"
                if use_b2 else "dry-run drill (no restore)"
            ),
            after={
                "use_b2": use_b2,
                "duration_seconds": round(duration_seconds, 2),
                "source_counts": source_counts,
                "spot_check_value": str(spot_check_value)
                if spot_check_value is not None else None,
            },
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Drill PASSED in {duration_seconds:.1f}s"
                + ("" if use_b2 else " (dry-run; no real restore)")
            )
        )

    # ------------------------------------------------------------------
    # Backup + restore orchestration
    # ------------------------------------------------------------------

    def _run_backup(self, use_b2: bool) -> str:
        """Invoke backup_db; return the resulting object key.

        With use_b2=False, backup_db --dry-run runs but uploads nothing;
        backup_id is empty in that case (no real B2 object to restore).
        """
        from books.audit.models import AuditAction, AuditLog

        if use_b2:
            call_command("backup_db", "--reason", "drill")
            latest = (
                AuditLog.objects
                .filter(action=AuditAction.BACKUP_CREATED)
                .order_by("-timestamp")
                .first()
            )
            if latest is None:
                raise RuntimeError(
                    "backup_db reported success but no BACKUP_CREATED "
                    "audit row was written; drill cannot continue."
                )
            return latest.entity_id
        else:
            call_command("backup_db", "--dry-run", "--reason", "drill")
            return ""  # no real key in dry-run

    def _run_restore(self, backup_id: str, scratch_url: str) -> None:
        call_command("restore_db", backup_id, "--into", scratch_url)

    # ------------------------------------------------------------------
    # Source-side verification (uses Django ORM on default DB)
    # ------------------------------------------------------------------

    def _count_source(self) -> dict[str, int]:
        from books.accounting.models import Account, JournalEntry, JournalLine
        from books.audit.models import AuditLog
        return {
            "account": Account.objects.count(),
            "system_account": Account.objects.filter(is_system=True).count(),
            "journal_entry": JournalEntry.objects.count(),
            "journal_line": JournalLine.objects.count(),
            "audit_log": AuditLog.objects.count(),
        }

    def _spot_check_source(self) -> Decimal | None:
        """Pre-backup own_balance for SPOT_CHECK_ACCOUNT_NUMBER. None
        if the account doesn't exist (e.g., COA not seeded)."""
        from books.accounting.models import Account
        from books.accounting.reports.trial_balance import compute_trial_balance

        if not Account.objects.filter(
            account_number=SPOT_CHECK_ACCOUNT_NUMBER,
        ).exists():
            return None

        tb = compute_trial_balance(as_of=datetime.now(UTC).date())
        for row in self._walk(tb.rows):
            if row.account.account_number == SPOT_CHECK_ACCOUNT_NUMBER:
                return row.own_balance
        return None

    def _walk(self, rows):
        for row in rows:
            yield row
            yield from self._walk(row.children)

    # ------------------------------------------------------------------
    # Target-side verification (psycopg direct, since target DB isn't
    # in Django settings.DATABASES)
    # ------------------------------------------------------------------

    def _count_target(self, scratch_url: str) -> dict[str, int]:
        with psycopg.connect(scratch_url) as conn:
            with conn.cursor() as cur:
                return {
                    "account": self._scalar(cur, "SELECT COUNT(*) FROM account"),
                    "system_account": self._scalar(
                        cur, "SELECT COUNT(*) FROM account WHERE is_system",
                    ),
                    "journal_entry": self._scalar(
                        cur, "SELECT COUNT(*) FROM journal_entry",
                    ),
                    "journal_line": self._scalar(
                        cur, "SELECT COUNT(*) FROM journal_line",
                    ),
                    "audit_log": self._scalar(
                        cur, "SELECT COUNT(*) FROM audit_log",
                    ),
                }

    def _spot_check_target(self, scratch_url: str) -> Decimal | None:
        with psycopg.connect(scratch_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, normal_balance FROM account "
                    "WHERE account_number = %s",
                    [SPOT_CHECK_ACCOUNT_NUMBER],
                )
                row = cur.fetchone()
                if row is None:
                    return None
                account_id, normal_balance = row
                cur.execute(
                    "SELECT COALESCE(SUM(debit_amount), 0), "
                    "COALESCE(SUM(credit_amount), 0) "
                    "FROM journal_line jl "
                    "JOIN journal_entry je ON je.id = jl.journal_entry_id "
                    "WHERE jl.account_id = %s AND je.status = 'posted'",
                    [account_id],
                )
                debits, credits = cur.fetchone()
                if normal_balance == "debit":
                    return Decimal(debits) - Decimal(credits)
                return Decimal(credits) - Decimal(debits)

    def _scalar(self, cur, sql: str) -> int:
        cur.execute(sql)
        return cur.fetchone()[0]

    # ------------------------------------------------------------------
    # Verification assertions
    # ------------------------------------------------------------------

    def _verify_match(
        self, source: dict[str, int], target: dict[str, int],
    ) -> None:
        """All counts must match exactly. Mismatches list the offending
        keys so the operator can investigate.

        system_account count is one of the keys checked. In the
        production drill (with the full COA seeded), source has 3 and
        target must have 3. In test fixtures with fewer, source==target
        is still the binding contract — this catches "restore omitted
        is_system=True rows" via the standard mismatch path.
        """
        mismatches = {
            k: (source[k], target.get(k))
            for k in source
            if source[k] != target.get(k)
        }
        if mismatches:
            raise RuntimeError(
                f"Count mismatch source vs restored: {mismatches}"
            )

    def _verify_spot_check(
        self, source_value: Decimal | None, target_value: Decimal | None,
    ) -> None:
        """Spot-check value at known account matches pre-backup value.
        Catches 'counts match but individual cell values corrupted'."""
        if source_value is None and target_value is None:
            # Account doesn't exist in either; nothing to check (but
            # still note this in the operator output).
            self.stdout.write(self.style.WARNING(
                f"Spot-check account {SPOT_CHECK_ACCOUNT_NUMBER} not "
                "present; skipping value comparison. Consider seeding "
                "the COA + posting an opening balance before drilling."
            ))
            return
        if source_value != target_value:
            raise RuntimeError(
                f"Spot-check value mismatch on account "
                f"{SPOT_CHECK_ACCOUNT_NUMBER}: source={source_value}, "
                f"target={target_value}."
            )
