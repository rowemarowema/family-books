"""
Delete all non-system Account rows.

Behavior table (Group E commit 4 — refinement #1):

    Refuses if posted JournalEntry exists  -> CommandError
    Refuses if any JournalLine exists      -> CommandError
    Deletes user accounts (is_system=False) -> reverse-depth ORM deletes
    Preserves system accounts              -> system rows untouched
    AuditLog records deleted_user_account_count

Usage:
    ./manage.py reset_coa --confirm-destroy "<reason>"

The --confirm-destroy flag is mandatory; the supplied reason is written
verbatim to AuditLog as evidence that this was a deliberate act.

System accounts are preserved by design (Q1 in the Group E breakdown):
they are infrastructure for the accounting engine, not user data.
Preserving them avoids a transient inconsistent state between reset and
re-seed, where opening-balance journals would have nowhere to point.

Why reverse-depth, not a single bulk DELETE?
    Account.parent_account has on_delete=PROTECT (Group D, intentional —
    blocks ad-hoc parent deletes that would orphan children). A bulk
    `Account.objects.filter(is_system=False).delete()` triggers PROTECT
    against the FIRST parent encountered that has children, even when
    those children are also in the queryset. PROTECT doesn't reason
    about what's also being deleted.

    The fix is structural: walk the user-account tree in-memory, group
    by depth, and delete deepest-first inside one transaction. After
    each depth level is deleted, the level above it has no inbound
    parent_account references, so its delete passes the PROTECT check.

    Direct ad-hoc deletes (a single Account.delete() call elsewhere)
    keep the PROTECT semantics — only this orchestrated batch is
    permitted to walk the tree.

After running this command, `seed_default_coa` is safe to re-run; it
detects existing system rows by account_number and skips them.
"""
from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from books.accounting.models import (
    Account,
    JournalEntry,
    JournalEntryStatus,
    JournalLine,
)


def _user_account_depth_buckets() -> dict[int, list[int]]:
    """Compute depth (root=1, leaf=N) for every user account by walking
    parent_account_id in-memory; return {depth: [pk, pk, ...]} for use
    by the reverse-depth delete loop.

    Walks only within is_system=False rows. A user row whose parent is a
    system row (or None) is treated as depth 1 — from the user-tree's
    point of view it IS a root, and the depth loop never tries to delete
    its system parent.
    """
    rows = list(
        Account.objects.filter(is_system=False).values_list(
            "id", "parent_account_id"
        )
    )
    # Only links into other user rows count for depth — system parents
    # act as roots from the user-tree perspective.
    parent_of_user = dict(rows)
    user_pks = {row_id for row_id, _ in rows}

    def depth_of(node_id: int) -> int:
        d = 1
        cur = parent_of_user.get(node_id)
        seen: set[int] = {node_id}
        while cur is not None and cur in user_pks:
            if cur in seen:
                # Cycle — shouldn't happen for valid data, but stay
                # bounded rather than spinning forever.
                break
            seen.add(cur)
            d += 1
            cur = parent_of_user.get(cur)
        return d

    by_depth: dict[int, list[int]] = {}
    for row_id, _ in rows:
        by_depth.setdefault(depth_of(row_id), []).append(row_id)
    return by_depth


class Command(BaseCommand):
    help = (
        "Delete all non-system Account rows. Refuses if any "
        "JournalEntry/JournalLine exists. Preserves system accounts."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--confirm-destroy",
            required=True,
            help="Free-text reason; written verbatim to AuditLog.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        # Late import keeps this module importable before migrations run.
        from books.audit.models import AuditAction, AuditLog

        reason: str = options["confirm_destroy"].strip()
        if not reason:
            raise CommandError("--confirm-destroy must be a non-empty reason.")

        posted_count = JournalEntry.objects.filter(
            status=JournalEntryStatus.POSTED
        ).count()
        if posted_count:
            AuditLog.record(
                entity_type="Account",
                action=AuditAction.COA_RESET_REFUSED,
                reason=f"{posted_count} posted journal entries exist",
                after={"posted_journal_entries": posted_count},
            )
            raise CommandError(
                f"Refusing to reset COA: {posted_count} posted JournalEntry "
                "row(s) exist. Posted entries are immutable; reverse them "
                "(via reverse_entry) before resetting the COA."
            )

        line_count = JournalLine.objects.count()
        if line_count:
            AuditLog.record(
                entity_type="Account",
                action=AuditAction.COA_RESET_REFUSED,
                reason=f"{line_count} journal lines exist",
                after={"journal_lines": line_count},
            )
            raise CommandError(
                f"Refusing to reset COA: {line_count} JournalLine row(s) "
                "exist (likely on draft entries). Delete the drafts first."
            )

        with transaction.atomic():
            # Reverse-depth delete: leaves first, root last. See module
            # docstring for the on_delete=PROTECT interaction.
            buckets = _user_account_depth_buckets()
            deleted_count = 0
            for depth in sorted(buckets.keys(), reverse=True):
                ids = buckets[depth]
                count, _ = Account.objects.filter(id__in=ids).delete()
                deleted_count += count

            AuditLog.record(
                entity_type="Account",
                action=AuditAction.COA_RESET,
                reason=reason,
                after={"deleted_user_account_count": deleted_count},
            )

        preserved = Account.objects.filter(is_system=True).count()
        self.stdout.write(
            self.style.SUCCESS(
                f"COA reset: deleted {deleted_count} user account(s); "
                f"preserved {preserved} system account(s). "
                "Run `seed_default_coa` to re-seed."
            )
        )
