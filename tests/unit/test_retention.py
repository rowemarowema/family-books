"""Retention policy unit tests.

Pure-Python; no DB, no boto3. The policy is the load-bearing constraint
on backup storage cost (B2 per-GB pricing) and recoverability (can I
go back N years?). Tests assert both bounds.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from books.core.backup.retention import (
    ANNUAL_RETENTION_YEARS,
    DAILY_RETENTION_DAYS,
    MONTHLY_RETENTION_MONTHS,
    BackupObject,
    select_for_deletion,
    select_keepers,
)


def _daily_keys(start: date, end: date) -> list[BackupObject]:
    """Generate one BackupObject per day in [start, end] inclusive."""
    objects = []
    cur = start
    while cur <= end:
        objects.append(BackupObject(key=f"backup-{cur.isoformat()}.sql.age",
                                    taken_on=cur))
        cur += timedelta(days=1)
    return objects


# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------


def test_keepers_at_most_49_for_10_years_of_daily_backups():
    """Property: 30 daily + 12 monthly + 7 annual = 49 max keepers.

    Generates 10 years of one-per-day backups (3650 objects). The exact
    count varies by calendar position (some today values produce 49,
    others 48 — depends on whether the daily window's calendar year is
    fully covered by monthly). The bound is the policy max."""
    today = date(2026, 4, 25)
    start = today - timedelta(days=365 * 10)
    objects = _daily_keys(start, today)
    keepers = select_keepers(objects, today=today)
    # Daily always full (30); monthly + annual together ≤ 19.
    assert len(keepers) <= 49
    assert len(keepers) >= 30 + 12  # daily + monthly always at full count


def test_year_end_today_hits_49_keeper_max():
    """Edge case: when today is at year-end, monthly window covers
    only one calendar year (current), leaving the full
    ANNUAL_RETENTION_YEARS slots available for prior years."""
    today = date(2026, 12, 31)
    start = today - timedelta(days=365 * 10)
    objects = _daily_keys(start, today)
    keepers = select_keepers(objects, today=today)
    assert len(keepers) == 49


def test_empty_input_yields_empty_keepers():
    assert select_keepers([], today=date(2026, 4, 25)) == set()


def test_single_object_in_daily_window_is_kept():
    today = date(2026, 4, 25)
    obj = BackupObject(key="recent", taken_on=today)
    keepers = select_keepers([obj], today=today)
    assert keepers == {"recent"}


def test_object_older_than_annual_window_is_dropped():
    today = date(2026, 4, 25)
    obj = BackupObject(key="ancient",
                       taken_on=today - timedelta(days=365 * 10))
    keepers = select_keepers([obj], today=today)
    assert keepers == set()


# ---------------------------------------------------------------------------
# Window boundaries
# ---------------------------------------------------------------------------


def test_all_30_days_in_daily_window_kept():
    """Every object dated within DAILY_RETENTION_DAYS of today is kept,
    even if there are multiple per day."""
    today = date(2026, 4, 25)
    objects = []
    for d_offset in range(DAILY_RETENTION_DAYS):
        d = today - timedelta(days=d_offset)
        # Two backups per day to test "all daily kept, not just one"
        objects.append(BackupObject(key=f"a-{d.isoformat()}", taken_on=d))
        objects.append(BackupObject(key=f"b-{d.isoformat()}", taken_on=d))

    keepers = select_keepers(objects, today=today)
    # All 60 (30 days x 2 backups/day) within the daily window are kept.
    assert len(keepers) == 60


def test_monthly_window_keeps_latest_per_calendar_month():
    """For dates outside the daily window but within the monthly
    window, only the LATEST per (year, month) survives."""
    today = date(2026, 4, 25)

    # Generate 5 backups in 2026-02 (monthly window). Only the latest
    # February 2026 should be kept.
    feb_dates = [date(2026, 2, day) for day in (3, 7, 15, 22, 28)]
    objects = [
        BackupObject(key=f"feb-{d.isoformat()}", taken_on=d)
        for d in feb_dates
    ]
    keepers = select_keepers(objects, today=today)
    assert keepers == {"feb-2026-02-28"}


def test_annual_window_keeps_latest_per_year():
    today = date(2026, 4, 25)
    # Pick dates well within the annual window: 2022, 2021.
    objects = [
        BackupObject(key="2022-jan", taken_on=date(2022, 1, 15)),
        BackupObject(key="2022-jun", taken_on=date(2022, 6, 15)),
        BackupObject(key="2022-dec", taken_on=date(2022, 12, 31)),
        BackupObject(key="2021-mar", taken_on=date(2021, 3, 5)),
        BackupObject(key="2021-nov", taken_on=date(2021, 11, 28)),
    ]
    keepers = select_keepers(objects, today=today)
    assert keepers == {"2022-dec", "2021-nov"}


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seed", [42, 1234, 9999, 0, 7777])
def test_property_keepers_subset_of_all(seed):
    """Property: keepers ⊆ all_objects. Trivial but locks against any
    bug that fabricates keys."""
    import random
    rng = random.Random(seed)
    today = date(2026, 4, 25)
    objects = []
    for _ in range(rng.randint(50, 500)):
        days_ago = rng.randint(0, 365 * 10)
        d = today - timedelta(days=days_ago)
        objects.append(BackupObject(
            key=f"obj-{rng.randint(0, 10**9)}-{d.isoformat()}",
            taken_on=d,
        ))
    all_keys = {o.key for o in objects}
    keepers = select_keepers(objects, today=today)
    assert keepers <= all_keys


@pytest.mark.parametrize("seed", [42, 1234, 9999, 0, 7777])
def test_property_select_for_deletion_complements_keepers(seed):
    """Property: keepers union deletions == all keys; intersection empty."""
    import random
    rng = random.Random(seed)
    today = date(2026, 4, 25)
    objects = [
        BackupObject(
            key=f"obj-{i}",
            taken_on=today - timedelta(days=rng.randint(0, 365 * 10)),
        )
        for i in range(100)
    ]
    keepers = select_keepers(objects, today=today)
    deletions = set(select_for_deletion(objects, today=today))
    all_keys = {o.key for o in objects}
    assert keepers | deletions == all_keys
    assert keepers & deletions == set()


def test_policy_constants_match_spec():
    """Pin against accidental drift from ADR-001 / Batch #1 #3."""
    assert DAILY_RETENTION_DAYS == 30
    assert MONTHLY_RETENTION_MONTHS == 12
    assert ANNUAL_RETENTION_YEARS == 7
