"""
Load fixtures/default_coa.json into the Account table.

Usage:
    ./manage.py seed_default_coa
        Loads the default fixture (640 user accounts + 3 system accounts).

    ./manage.py seed_default_coa --dry-run
        Parses + validates the fixture; creates no rows.

    ./manage.py seed_default_coa --fixture path/to/other.json
        Override path for tests / alternative seeds.

Idempotency contract (Batch #4 decision #29):
    Refuses if any non-system Account row already exists. To re-seed,
    run `reset_coa --confirm-destroy "<reason>"` first; system accounts
    are preserved across reset and skipped on re-seed (matched by
    account_number) so the loader is safe to run again immediately.

Refuses on any of the following before touching the DB:
    - JSON parse error / file not found
    - Schema mismatch (missing top-level keys or per-row required fields)
    - Hierarchy cycle, unknown parent reference, or depth > 4
    - Type/normal_balance mismatch (re-uses Account.clean())
    - Fixture row violates system-account rules (is_system=True on
      non-Equity, etc.)

On success writes one AuditLog row with action=COA_SEEDED summarizing the
load. On refusal writes COA_SEED_REFUSED so the audit trail captures both
sides.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from books.accounting.models import (
    Account,
    AccountType,
    MAX_HIERARCHY_DEPTH,
    NormalBalance,
)


REQUIRED_TOP_LEVEL_KEYS = {"system_accounts", "accounts"}
REQUIRED_ROW_KEYS = {
    "account_number",
    "name",
    "type",
    "normal_balance",
    "parent_account_number",
    "is_active",
    "is_system",
    "display_order",
}
DEFAULT_FIXTURE_PATH = "fixtures/default_coa.json"


class Command(BaseCommand):
    help = "Load the default chart of accounts from a JSON fixture."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--fixture",
            default=None,
            help=(
                f"Override fixture path (default: {DEFAULT_FIXTURE_PATH} "
                "relative to BASE_DIR). Test-only escape hatch."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate the fixture and report counts; create nothing.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        # Late import keeps this command importable before migrations run.
        from books.audit.models import AuditAction, AuditLog

        fixture_path = self._resolve_fixture_path(options.get("fixture"))
        dry_run: bool = options.get("dry_run", False)

        data = self._read_and_parse(fixture_path)
        self._validate_schema(data, fixture_path)

        existing_user_count = Account.objects.filter(is_system=False).count()
        if existing_user_count > 0:
            AuditLog.record(
                entity_type="Account",
                action=AuditAction.COA_SEED_REFUSED,
                reason=(
                    f"non-system Account rows present "
                    f"(count={existing_user_count})"
                ),
                after={
                    "existing_user_account_count": existing_user_count,
                    "fixture": str(fixture_path),
                    "dry_run": dry_run,
                },
            )
            raise CommandError(
                f"{existing_user_count} non-system Account row(s) already exist. "
                "To re-seed, run `reset_coa --confirm-destroy \"<reason>\"` "
                "first; system accounts are preserved."
            )

        # In-memory hierarchy validation (cycle, depth, unknown-parent).
        # Doing it before any DB write means refusal preempts inserts.
        self._validate_hierarchy(data["accounts"])

        existing_sys_numbers = set(
            Account.objects.filter(is_system=True).values_list(
                "account_number", flat=True
            )
        )

        sys_objs = [
            self._row_to_account(raw)
            for raw in data["system_accounts"]
            if raw["account_number"] not in existing_sys_numbers
        ]
        user_objs = [self._row_to_account(raw) for raw in data["accounts"]]

        # Per-row validation through full_clean(): catches bad type/normal-balance
        # combos and the system-account rules. Skip uniqueness validation
        # (validate_unique would issue 643 SELECTs); the DB constraint covers it.
        for a in sys_objs + user_objs:
            a.full_clean(exclude=["account_number"])

        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f"DRY RUN: validated {len(sys_objs)} system + "
                    f"{len(user_objs)} user accounts from {fixture_path.name}. "
                    "No rows created."
                )
            )
            return

        with transaction.atomic():
            # Pass 1 — bulk insert with parent_account=None.
            Account.objects.bulk_create(sys_objs + user_objs)

            # Pass 2 — resolve parent FKs by account_number lookup, in-memory.
            id_by_number = dict(
                Account.objects.values_list("account_number", "id")
            )
            updates: list[Account] = []
            for raw in data["accounts"]:
                parent_num = raw["parent_account_number"]
                if not parent_num:
                    continue
                parent_id = id_by_number[parent_num]
                child = Account(
                    pk=id_by_number[raw["account_number"]],
                    parent_account_id=parent_id,
                )
                updates.append(child)

            if updates:
                Account.objects.bulk_update(updates, ["parent_account"])

            AuditLog.record(
                entity_type="Account",
                action=AuditAction.COA_SEEDED,
                reason=f"Loaded from {fixture_path.name}",
                after={
                    "system_inserted": len(sys_objs),
                    "user_inserted": len(user_objs),
                    "parents_resolved": len(updates),
                    "fixture": str(fixture_path),
                },
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {len(sys_objs)} system + {len(user_objs)} user accounts "
                f"({len(updates)} parent links) from {fixture_path.name}."
            )
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_fixture_path(self, override: str | None) -> Path:
        if override:
            p = Path(override)
            if not p.is_absolute():
                p = Path(settings.BASE_DIR) / p
        else:
            p = Path(settings.BASE_DIR) / DEFAULT_FIXTURE_PATH
        if not p.exists():
            raise CommandError(f"Fixture not found: {p}")
        return p

    def _read_and_parse(self, path: Path) -> dict[str, Any]:
        try:
            with open(path, "r", encoding="utf-8") as fp:
                return json.load(fp)
        except json.JSONDecodeError as exc:
            raise CommandError(f"Invalid JSON in {path}: {exc}") from exc
        except OSError as exc:
            raise CommandError(f"Cannot read {path}: {exc}") from exc

    def _validate_schema(self, data: dict[str, Any], path: Path) -> None:
        if not isinstance(data, dict):
            raise CommandError(f"{path}: top-level must be a JSON object.")
        missing_top = REQUIRED_TOP_LEVEL_KEYS - set(data.keys())
        if missing_top:
            raise CommandError(
                f"{path}: missing top-level keys {sorted(missing_top)}."
            )
        if not isinstance(data["system_accounts"], list):
            raise CommandError(f"{path}: 'system_accounts' must be a list.")
        if not isinstance(data["accounts"], list):
            raise CommandError(f"{path}: 'accounts' must be a list.")

        bad_rows: list[str] = []
        for raw in data["system_accounts"] + data["accounts"]:
            if not isinstance(raw, dict):
                bad_rows.append(f"non-object entry: {raw!r}")
                continue
            missing = REQUIRED_ROW_KEYS - set(raw.keys())
            if missing:
                bad_rows.append(
                    f"{raw.get('account_number', '<no account_number>')}: "
                    f"missing keys {sorted(missing)}"
                )
        if bad_rows:
            raise CommandError(
                f"{path}: {len(bad_rows)} row(s) failed schema check:\n  "
                + "\n  ".join(bad_rows[:10])
                + ("\n  ..." if len(bad_rows) > 10 else "")
            )

    def _validate_hierarchy(self, rows: list[dict[str, Any]]) -> None:
        """Walk the parent chain in-memory; trip on cycle / unknown parent /
        depth > MAX_HIERARCHY_DEPTH.

        The Account.clean() depth walk also enforces this on individual
        saves, but bulk_update doesn't run signals, so we do it here for
        the loader path.
        """
        parent_of = {row["account_number"]: row["parent_account_number"] for row in rows}
        for child_num in parent_of:
            depth = 1
            cur = parent_of[child_num]
            seen: set[str] = {child_num}
            while cur is not None:
                if cur in seen:
                    raise CommandError(
                        f"Hierarchy cycle involving account {child_num!r}."
                    )
                if cur not in parent_of:
                    raise CommandError(
                        f"Account {child_num!r} references unknown "
                        f"parent_account_number={cur!r}."
                    )
                seen.add(cur)
                depth += 1
                if depth > MAX_HIERARCHY_DEPTH:
                    raise CommandError(
                        f"Account {child_num!r} hierarchy depth exceeds "
                        f"{MAX_HIERARCHY_DEPTH} levels."
                    )
                cur = parent_of[cur]

    def _row_to_account(self, raw: dict[str, Any]) -> Account:
        """Map a JSON row to an unsaved Account instance.

        Coerces:
          - type/normal_balance: capitalized in JSON ("Asset"/"Debit") →
            lowercase to match TextChoices values.
          - description / tax_category: null → "" (CharField/TextField default).
        Drops:
          - parent_account_number: handled in pass 2; instance gets
            parent_account=None here.
          - full_path: human-readable provenance only.
        """
        return Account(
            account_number=raw["account_number"],
            name=raw["name"],
            type=str(raw["type"]).lower(),
            normal_balance=str(raw["normal_balance"]).lower(),
            parent_account=None,
            is_active=bool(raw["is_active"]),
            is_system=bool(raw["is_system"]),
            display_order=int(raw["display_order"]),
            description=raw.get("description") or "",
            tax_category=raw.get("tax_category") or "",
        )
