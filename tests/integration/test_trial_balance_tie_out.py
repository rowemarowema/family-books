"""Trial-balance tie-out — precursor to Group F's formal trial balance report.

After N random balanced journal entries post, the sum of all posted
debit_amount values equals the sum of all posted credit_amount values.
This is the core invariant the real trial balance will rely on; the test
lives here in Group D so the engine's contract is proven before any
report code is written in Group F.

Property: for a seed of random amounts and random account pairings,
post_entry(balanced) always leaves the books in balance.
"""
from __future__ import annotations

import random
from decimal import Decimal

import pytest
from django.db.models import Sum

from books.accounting.factories import (
    AccountFactory,
    RevenueAccountFactory,
    make_balanced_entry,
)
from books.accounting.models import JournalEntryStatus, JournalLine
from books.accounting.posting import post_entry


@pytest.mark.django_db
def test_trial_balance_ties_out_across_many_random_entries(owner):
    """Generate N balanced entries with random amounts and random (debit,
    credit) account pairings from an asset vs revenue pool; post them all;
    assert sum-of-debits == sum-of-credits.
    """
    rng = random.Random(42)

    asset_accounts = [
        AccountFactory(account_number=f"10{i:02d}", name=f"Asset {i}")
        for i in range(5)
    ]
    revenue_accounts = [
        RevenueAccountFactory(account_number=f"40{i:02d}", name=f"Revenue {i}")
        for i in range(5)
    ]

    N = 75
    for _ in range(N):
        amount = Decimal(f"{rng.randint(1, 100000)}.{rng.randint(0, 99):02d}")
        debit_acct = rng.choice(asset_accounts)
        credit_acct = rng.choice(revenue_accounts)
        entry = make_balanced_entry(
            debit_account=debit_acct,
            credit_account=credit_acct,
            amount=amount,
        )
        post_entry(entry, user=owner, reason=f"random entry amount={amount}")

    # Sum across all posted lines — must tie out to the cent.
    totals = JournalLine.objects.filter(
        journal_entry__status=JournalEntryStatus.POSTED,
    ).aggregate(
        total_debits=Sum("debit_amount"),
        total_credits=Sum("credit_amount"),
    )
    assert totals["total_debits"] == totals["total_credits"]
    assert totals["total_debits"] > 0  # sanity: we actually posted something
