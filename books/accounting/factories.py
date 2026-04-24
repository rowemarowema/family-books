"""factory_boy factories for accounting tests.

Importable from `books.accounting.factories` so tests under tests/ and
books/ can reuse them. These produce realistic but deterministic seed
data — the `journal_line_*` factories always produce valid CHECK-
constraint-passing lines by construction.
"""
from __future__ import annotations

from decimal import Decimal

import factory
from factory.django import DjangoModelFactory

from books.accounting.models import (
    Account,
    AccountType,
    JournalEntry,
    JournalEntrySource,
    JournalEntryStatus,
    JournalLine,
    NormalBalance,
)


class AccountFactory(DjangoModelFactory):
    class Meta:
        model = Account

    account_number = factory.Sequence(lambda n: f"{1000 + n}")
    name = factory.Sequence(lambda n: f"Account {n}")
    type = AccountType.ASSET
    subtype = ""
    parent_account = None
    normal_balance = NormalBalance.DEBIT
    is_active = True
    description = ""
    tax_category = ""
    opening_balance = Decimal("0.00")
    opening_balance_date = None


class LiabilityAccountFactory(AccountFactory):
    type = AccountType.LIABILITY
    normal_balance = NormalBalance.CREDIT


class RevenueAccountFactory(AccountFactory):
    type = AccountType.REVENUE
    normal_balance = NormalBalance.CREDIT


class ExpenseAccountFactory(AccountFactory):
    type = AccountType.EXPENSE
    normal_balance = NormalBalance.DEBIT


class EquityAccountFactory(AccountFactory):
    type = AccountType.EQUITY
    normal_balance = NormalBalance.CREDIT


class JournalEntryFactory(DjangoModelFactory):
    class Meta:
        model = JournalEntry

    entry_date = factory.Faker("date_this_decade")
    posting_date = factory.LazyAttribute(lambda o: o.entry_date)
    memo = factory.Faker("sentence", nb_words=6)
    source = JournalEntrySource.MANUAL
    reference_number = factory.Sequence(lambda n: f"REF-{n:06d}")
    status = JournalEntryStatus.DRAFT
    posted_at = None


class DebitLineFactory(DjangoModelFactory):
    class Meta:
        model = JournalLine

    journal_entry = factory.SubFactory(JournalEntryFactory)
    account = factory.SubFactory(AccountFactory)
    debit_amount = Decimal("10.00")
    credit_amount = Decimal("0.00")
    memo = ""


class CreditLineFactory(DjangoModelFactory):
    class Meta:
        model = JournalLine

    journal_entry = factory.SubFactory(JournalEntryFactory)
    account = factory.SubFactory(AccountFactory)
    debit_amount = Decimal("0.00")
    credit_amount = Decimal("10.00")
    memo = ""


def make_balanced_entry(
    *,
    debit_account: Account,
    credit_account: Account,
    amount: Decimal = Decimal("100.00"),
    **entry_kwargs,
) -> JournalEntry:
    """Convenience: two-line balanced draft entry ready for post_entry."""
    entry = JournalEntryFactory(**entry_kwargs)
    DebitLineFactory(
        journal_entry=entry,
        account=debit_account,
        debit_amount=amount,
        credit_amount=Decimal("0.00"),
    )
    CreditLineFactory(
        journal_entry=entry,
        account=credit_account,
        debit_amount=Decimal("0.00"),
        credit_amount=amount,
    )
    return entry
