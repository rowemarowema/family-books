"""
Accounting engine core: Account, JournalEntry, JournalLine.

Invariants (defense in depth — see PROGRESS.md Group D design):

  Per-line XOR:    DB CHECK + JournalLine.clean()
  Per-line >= 0:   DB CHECK
  Cross-line sum:  post_entry() service
  Account active:  post_entry() service
  >= 2 lines:      post_entry() service
  Posted immutability:
                   save()/delete() overrides (Python)
                 + Postgres triggers (RunSQL migration)
  4-level max hierarchy:
                   Account.clean() + pre_save signal
  Type vs normal_balance:
                   Account.clean()
  One reversal per original:
                   DB UniqueConstraint (partial index)
                 + reverse_entry() service pre-check
"""
from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import CheckConstraint, Q, UniqueConstraint

from books.accounting.exceptions import PostedEntryImmutable


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class AccountType(models.TextChoices):
    ASSET = "asset", "Asset"
    LIABILITY = "liability", "Liability"
    EQUITY = "equity", "Equity"
    REVENUE = "revenue", "Revenue"
    EXPENSE = "expense", "Expense"


class NormalBalance(models.TextChoices):
    DEBIT = "debit", "Debit"
    CREDIT = "credit", "Credit"


TYPE_TO_NORMAL_BALANCE = {
    AccountType.ASSET: NormalBalance.DEBIT,
    AccountType.EXPENSE: NormalBalance.DEBIT,
    AccountType.LIABILITY: NormalBalance.CREDIT,
    AccountType.EQUITY: NormalBalance.CREDIT,
    AccountType.REVENUE: NormalBalance.CREDIT,
}


class JournalEntryStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    POSTED = "posted", "Posted"
    VOID = "void", "Void"


class JournalEntrySource(models.TextChoices):
    MANUAL = "manual", "Manual"
    IMPORT = "import", "Import"
    RECURRING = "recurring", "Recurring"
    SYSTEM = "system", "System"


# ---------------------------------------------------------------------------
# Account
# ---------------------------------------------------------------------------

MAX_HIERARCHY_DEPTH = 4


class Account(models.Model):
    """Chart-of-accounts node (spec §4.1).

    Hierarchy is capped at 4 levels; enforcement lives in .clean() + a
    pre_save signal (see books.accounting.signals) so Account.objects.create()
    — which skips full_clean() — cannot slip through.

    account_number is a free-form string up to 16 chars. Format is
    convention-only: the default COA uses 1000/2000/..., but callers can
    adopt sub-numbering like 10100 / 10101 / 10200 without the system
    rejecting them.
    """

    account_number = models.CharField(max_length=16, unique=True)
    name = models.CharField(max_length=128)
    type = models.CharField(max_length=16, choices=AccountType.choices)
    subtype = models.CharField(max_length=64, blank=True, default="")
    parent_account = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="children",
    )
    normal_balance = models.CharField(max_length=8, choices=NormalBalance.choices)
    is_active = models.BooleanField(default=True)
    # System accounts are seeded by `seed_default_coa` from the
    # `system_accounts` array of fixtures/default_coa.json. They cannot
    # be deactivated, deleted (where the `is_system=False` filter is used
    # by `reset_coa`), or have `is_system` flipped post-create. Group E
    # (commit 2) layers the protection logic on top of this flag.
    is_system = models.BooleanField(default=False, db_index=True)
    description = models.TextField(blank=True, default="")
    tax_category = models.CharField(max_length=64, blank=True, default="")
    opening_balance = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    opening_balance_date = models.DateField(null=True, blank=True)
    # Sort key for grouped views (type/parent groupings). User accounts default
    # to 0 → alpha-by-name within their group; system accounts use negative
    # values to sort to the top of their grouping. See Batch #4 in
    # project_decisions.md and the COA fixture for the convention.
    display_order = models.IntegerField(default=0, db_index=True)

    class Meta:
        db_table = "account"
        ordering = ["display_order", "name"]
        indexes = [
            models.Index(fields=["type", "is_active"]),
            models.Index(fields=["display_order", "name"]),
        ]

    def __str__(self) -> str:
        return f"{self.account_number} {self.name}"

    def clean(self) -> None:
        super().clean()
        # Normal-balance / type consistency.
        expected = TYPE_TO_NORMAL_BALANCE.get(self.type)
        if expected is not None and self.normal_balance != expected:
            raise ValidationError(
                {
                    "normal_balance": (
                        f"Account type '{self.type}' requires "
                        f"normal_balance='{expected}', got '{self.normal_balance}'."
                    ),
                }
            )
        # Hierarchy depth (walk parent chain; raise if would become > 4).
        depth = 1
        node = self.parent_account
        visited: set[int] = set()
        while node is not None:
            if node.pk in visited:
                raise ValidationError(
                    {"parent_account": "Account hierarchy contains a cycle."}
                )
            visited.add(node.pk)
            depth += 1
            if depth > MAX_HIERARCHY_DEPTH:
                raise ValidationError(
                    {
                        "parent_account": (
                            f"Account hierarchy cannot exceed {MAX_HIERARCHY_DEPTH} levels."
                        )
                    }
                )
            node = node.parent_account


# ---------------------------------------------------------------------------
# JournalEntry
# ---------------------------------------------------------------------------


class JournalEntry(models.Model):
    """A balanced set of JournalLines (spec §4.2).

    Immutability: once status == 'posted', save() and delete() raise
    PostedEntryImmutable. The posting service is the only legitimate path
    from draft to posted, and it works because the in-DB old_status is
    'draft' at the moment of the transition save.

    reversing_entry_id, when set, means "I am the reversal of original
    entry X." The link is one-way — the original is never mutated when a
    reversal is posted.
    """

    entry_date = models.DateField()
    posting_date = models.DateField()
    memo = models.TextField(blank=True, default="")
    source = models.CharField(
        max_length=16,
        choices=JournalEntrySource.choices,
        default=JournalEntrySource.MANUAL,
    )
    reference_number = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(
        max_length=8,
        choices=JournalEntryStatus.choices,
        default=JournalEntryStatus.DRAFT,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_journal_entries",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    posted_at = models.DateTimeField(null=True, blank=True)
    # Spec §4.2 names the DB column `reversing_entry_id`. Django's default
    # for an FK attribute named `reversing_entry` appends `_id` to produce
    # the column name, so `reversing_entry` (the attribute) and
    # `reversing_entry_id` (the raw ID + DB column) match the spec directly.
    reversing_entry = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reversed_by",
    )

    class Meta:
        db_table = "journal_entry"
        ordering = ["-entry_date", "-id"]
        indexes = [
            models.Index(fields=["status", "entry_date"]),
            models.Index(fields=["posting_date"]),
        ]
        constraints = [
            # One reversal per original — partial unique index on the FK.
            UniqueConstraint(
                fields=["reversing_entry"],
                condition=Q(reversing_entry__isnull=False),
                name="one_reversal_per_original",
            ),
        ]

    def __str__(self) -> str:
        return f"JE#{self.pk} {self.entry_date} [{self.status}]"

    def save(self, *args, **kwargs) -> None:
        if self.pk is not None:
            old_status = (
                type(self)
                .objects.filter(pk=self.pk)
                .values_list("status", flat=True)
                .first()
            )
            if old_status == JournalEntryStatus.POSTED:
                raise PostedEntryImmutable(
                    f"JournalEntry #{self.pk} is posted; cannot modify in place."
                )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.pk is not None and self.status == JournalEntryStatus.POSTED:
            raise PostedEntryImmutable(
                f"JournalEntry #{self.pk} is posted; cannot delete."
            )
        return super().delete(*args, **kwargs)


# ---------------------------------------------------------------------------
# JournalLine
# ---------------------------------------------------------------------------


class JournalLine(models.Model):
    """A single debit or credit posting against an Account (spec §4.2).

    DB CHECK constraints guarantee each row has exactly one positive side
    and both sides non-negative. The service layer additionally checks
    cross-line sum equality on post.
    """

    journal_entry = models.ForeignKey(
        JournalEntry,
        on_delete=models.CASCADE,
        related_name="lines",
    )
    account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        related_name="journal_lines",
    )
    debit_amount = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    credit_amount = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    memo = models.TextField(blank=True, default="")

    class Meta:
        db_table = "journal_line"
        ordering = ["id"]
        indexes = [
            models.Index(fields=["account", "journal_entry"]),
        ]
        constraints = [
            CheckConstraint(
                condition=Q(debit_amount__gte=0),
                name="journal_line_debit_non_negative",
            ),
            CheckConstraint(
                condition=Q(credit_amount__gte=0),
                name="journal_line_credit_non_negative",
            ),
            CheckConstraint(
                condition=(
                    Q(debit_amount__gt=0, credit_amount=0)
                    | Q(debit_amount=0, credit_amount__gt=0)
                ),
                name="journal_line_debit_xor_credit",
            ),
        ]

    def __str__(self) -> str:
        side = f"dr {self.debit_amount}" if self.debit_amount else f"cr {self.credit_amount}"
        return f"Line#{self.pk} {self.account.account_number} {side}"

    def clean(self) -> None:
        super().clean()
        if self.debit_amount < 0 or self.credit_amount < 0:
            raise ValidationError("debit_amount and credit_amount must be non-negative.")
        dr_pos = self.debit_amount > 0
        cr_pos = self.credit_amount > 0
        if dr_pos == cr_pos:  # both zero OR both positive
            raise ValidationError(
                "Exactly one of debit_amount or credit_amount must be positive."
            )

    def save(self, *args, **kwargs) -> None:
        if self.journal_entry_id:
            parent_status = (
                JournalEntry.objects.filter(pk=self.journal_entry_id)
                .values_list("status", flat=True)
                .first()
            )
            if parent_status == JournalEntryStatus.POSTED:
                raise PostedEntryImmutable(
                    f"JournalLine belongs to posted JournalEntry "
                    f"#{self.journal_entry_id}; cannot modify."
                )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.journal_entry_id:
            parent_status = (
                JournalEntry.objects.filter(pk=self.journal_entry_id)
                .values_list("status", flat=True)
                .first()
            )
            if parent_status == JournalEntryStatus.POSTED:
                raise PostedEntryImmutable(
                    f"JournalLine belongs to posted JournalEntry "
                    f"#{self.journal_entry_id}; cannot delete."
                )
        return super().delete(*args, **kwargs)
