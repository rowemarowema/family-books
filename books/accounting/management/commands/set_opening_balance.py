"""
Set opening balance(s) on Account(s).

Two modes:

1. Single-account:
       ./manage.py set_opening_balance --account 1-0179 \
           --amount 8500.00 --as-of 2001-01-01

2. CSV bulk:
       ./manage.py set_opening_balance --csv path/to/balances.csv

CSV columns (header row required, comma-separated, UTF-8):

    account_number,account_path,amount,as_of

  - Both `account_number` and `account_path` are optional but at
    least one must be present per row.
  - When both present, `account_number` wins (canonical identifier);
    `account_path` is convenience.
  - `account_path` is the colon-separated full path (e.g.,
    `Investments:E*Trade:E*Trade - Cash`). The loader resolves it to
    a canonical account by walking each Account's parent chain in
    memory.
  - `amount` is a positive Decimal; sign is implied by the account's
    normal_balance.
  - `as_of` is YYYY-MM-DD.

Behavior:
  - CSV bulk runs inside one transaction.atomic(); a single bad row
    aborts the whole batch (refuse-not-partial).
  - --dry-run validates every row without posting any JE. Useful for
    "would this load cleanly?" before committing to the batch.

Both modes write OPENING_BALANCE_SET / OPENING_BALANCE_REFUSED audit
rows via the underlying service; this command is a thin shell over
books.accounting.opening_balances.set_opening_balance().
"""
from __future__ import annotations

import csv
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from books.accounting.exceptions import OpeningBalanceError
from books.accounting.models import Account
from books.accounting.opening_balances import set_opening_balance

REQUIRED_CSV_COLUMNS = {"account_number", "account_path", "amount", "as_of"}


class Command(BaseCommand):
    help = (
        "Set opening balance(s) on Account(s). Single-account or "
        "CSV-bulk modes."
    )

    def add_arguments(self, parser: Any) -> None:
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument(
            "--account",
            help="account_number for single-account mode (e.g., 1-0179).",
        )
        mode.add_argument(
            "--csv",
            help=(
                "Path to a 4-column CSV "
                "(account_number,account_path,amount,as_of) for bulk mode."
            ),
        )

        parser.add_argument(
            "--amount",
            help="Positive Decimal amount (single-account mode only).",
        )
        parser.add_argument(
            "--as-of",
            help="YYYY-MM-DD opening date (single-account mode only).",
        )
        parser.add_argument(
            "--reason",
            default="Opening balance",
            help='AuditLog reason (default: "Opening balance").',
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate inputs; create no JEs.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        user = self._resolve_user()
        dry_run: bool = options["dry_run"]
        reason: str = options["reason"]

        if options["account"] is not None:
            self._handle_single(user, options, reason, dry_run)
        else:
            self._handle_csv(user, options["csv"], reason, dry_run)

    # ------------------------------------------------------------------
    # Single-account mode
    # ------------------------------------------------------------------

    def _handle_single(
        self, user, options: dict, reason: str, dry_run: bool,
    ) -> None:
        if options.get("amount") is None or options.get("as_of") is None:
            raise CommandError(
                "--amount and --as-of are required in single-account mode."
            )

        amount = self._parse_amount(options["amount"], context="--amount")
        as_of = self._parse_date(options["as_of"], context="--as-of")
        account = self._resolve_by_number(options["account"])

        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f"DRY RUN: would set opening balance on "
                    f"{account.account_number} {account.name!r} = {amount} "
                    f"as of {as_of}."
                )
            )
            return

        try:
            result = set_opening_balance(
                account, amount=amount, as_of=as_of, user=user, reason=reason,
            )
        except OpeningBalanceError as exc:
            raise CommandError(f"Refused: {exc}") from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"Posted opening balance JE #{result.journal_entry.pk} "
                f"on {account.account_number} {account.name!r} = "
                f"{amount} as of {as_of}."
            )
        )

    # ------------------------------------------------------------------
    # CSV bulk mode
    # ------------------------------------------------------------------

    def _handle_csv(
        self, user, csv_path: str, reason: str, dry_run: bool,
    ) -> None:
        path = Path(csv_path)
        if not path.exists():
            raise CommandError(f"CSV file not found: {path}")

        rows = self._read_csv(path)

        # Resolve every row to (Account, Decimal, date) BEFORE writing
        # anything. Refuse-not-partial: any single failure aborts the
        # whole batch. The atomic block below runs only if every row
        # parsed and resolved cleanly.
        path_index = self._build_path_index() if any(
            (r.get("account_path") and not r.get("account_number")) for r in rows
        ) else {}

        resolved: list[tuple[int, Account, Decimal, date]] = []
        for row_num, row in enumerate(rows, start=2):  # 1 = header
            try:
                account = self._resolve_row(row, path_index, row_num=row_num)
                amount = self._parse_amount(
                    row["amount"], context=f"row {row_num} 'amount'",
                )
                as_of = self._parse_date(
                    row["as_of"], context=f"row {row_num} 'as_of'",
                )
            except CommandError:
                raise  # already has row context

            resolved.append((row_num, account, amount, as_of))

        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f"DRY RUN: validated {len(resolved)} row(s) from "
                    f"{path.name}. No JEs created."
                )
            )
            return

        # Bulk mode runs inside ONE atomic block: a refused row mid-batch
        # rolls back every prior insert in this batch (refuse-not-partial).
        try:
            with transaction.atomic():
                for row_num, account, amount, as_of in resolved:
                    try:
                        set_opening_balance(
                            account, amount=amount, as_of=as_of,
                            user=user, reason=reason,
                        )
                    except OpeningBalanceError as exc:
                        raise CommandError(
                            f"Row {row_num} refused: {exc}"
                        ) from exc
        except CommandError:
            raise

        self.stdout.write(
            self.style.SUCCESS(
                f"Posted {len(resolved)} opening balance JE(s) from "
                f"{path.name}."
            )
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_user(self):
        User = get_user_model()
        user = User.objects.filter(is_superuser=True).first()
        if user is None:
            raise CommandError(
                "No owner user found. Run `bootstrap_owner` first."
            )
        return user

    def _read_csv(self, path: Path) -> list[dict[str, str]]:
        with open(path, encoding="utf-8", newline="") as fp:
            reader = csv.DictReader(fp)
            if reader.fieldnames is None:
                raise CommandError(f"{path}: missing header row.")
            missing = REQUIRED_CSV_COLUMNS - set(reader.fieldnames)
            if missing:
                raise CommandError(
                    f"{path}: missing CSV column(s) {sorted(missing)}; "
                    f"required: {sorted(REQUIRED_CSV_COLUMNS)}."
                )
            return [{k: (v or "").strip() for k, v in row.items()}
                    for row in reader]

    def _build_path_index(self) -> dict[str, Account]:
        """Compute {full_path: Account} for all accounts by walking
        each row's parent_account chain in memory.

        full_path is JSON-fixture metadata, not a model field. It's
        derived here on demand. For 643 accounts this is a single
        bulk SELECT plus an O(N * depth) walk.
        """
        all_accounts = list(
            Account.objects.select_related().only(
                "id", "name", "parent_account_id"
            )
        )
        by_pk = {a.pk: a for a in all_accounts}

        def full_path_for(account: Account) -> str:
            parts: list[str] = [account.name]
            cur_id = account.parent_account_id
            seen: set[int] = {account.pk}
            while cur_id is not None and cur_id in by_pk:
                if cur_id in seen:
                    return ":".join(parts)  # cycle: bail (shouldn't happen)
                seen.add(cur_id)
                parent = by_pk[cur_id]
                parts.insert(0, parent.name)
                cur_id = parent.parent_account_id
            return ":".join(parts)

        return {full_path_for(a): a for a in all_accounts}

    def _resolve_row(
        self,
        row: dict[str, str],
        path_index: dict[str, Account],
        *,
        row_num: int,
    ) -> Account:
        number = row.get("account_number") or ""
        path_str = row.get("account_path") or ""

        if not number and not path_str:
            raise CommandError(
                f"Row {row_num}: at least one of account_number or "
                "account_path is required."
            )

        # account_number wins (canonical) when present.
        if number:
            try:
                return Account.objects.get(account_number=number)
            except Account.DoesNotExist as exc:
                raise CommandError(
                    f"Row {row_num}: account_number {number!r} not found."
                ) from exc

        if path_str not in path_index:
            raise CommandError(
                f"Row {row_num}: account_path {path_str!r} not found. "
                "Paths are colon-separated full names; verify against "
                "the fixture or `Account.objects.values('name', "
                "'parent_account__name')`."
            )
        return path_index[path_str]

    def _resolve_by_number(self, number: str) -> Account:
        try:
            return Account.objects.get(account_number=number)
        except Account.DoesNotExist as exc:
            raise CommandError(f"--account {number!r} not found.") from exc

    def _parse_amount(self, raw: str, *, context: str) -> Decimal:
        try:
            value = Decimal(raw)
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise CommandError(
                f"{context}: {raw!r} is not a valid Decimal."
            ) from exc
        if value <= 0:
            raise CommandError(
                f"{context}: amount must be positive; got {value}."
            )
        return value

    def _parse_date(self, raw: str, *, context: str) -> date:
        try:
            return date.fromisoformat(raw)
        except (ValueError, TypeError) as exc:
            raise CommandError(
                f"{context}: {raw!r} is not a valid YYYY-MM-DD date."
            ) from exc
