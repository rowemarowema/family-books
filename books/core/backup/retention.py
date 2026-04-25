"""
Retention policy for B2 backup objects (ADR-001 / Batch #1 #3):

    30 daily   — every backup within the last 30 days, all kept.
    12 monthly — top 12 most-recent (year, month) buckets OUTSIDE the
                 daily window; the latest backup in each is kept.
    7 annual   — among years that don't already have a monthly
                 representative kept, the top 7 most-recent; the
                 latest backup in each is kept.

The "top N" framing matters: a calendar-window framing (e.g., "all
objects in the last 365 days") overcounts at boundaries. With 10 years
of daily backups, top-N gives exactly 30 + 12 + 7 = 49 keepers.

Pure functions over (date, key) tuples; no I/O. Tested via property
tests in tests/unit/test_retention.py.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta

DAILY_RETENTION_DAYS = 30
MONTHLY_RETENTION_MONTHS = 12
ANNUAL_RETENTION_YEARS = 7


@dataclass(frozen=True)
class BackupObject:
    key: str
    taken_on: date


def select_keepers(
    objects: Iterable[BackupObject], *, today: date,
) -> set[str]:
    """Apply retention policy. Returns the set of object keys to KEEP."""
    daily_cutoff = today - timedelta(days=DAILY_RETENTION_DAYS)

    keepers: set[str] = set()

    # Step 1: partition by daily-window vs. pre-daily.
    daily_objects: list[BackupObject] = []
    pre_daily_by_ym: dict[tuple[int, int], BackupObject] = {}

    for obj in objects:
        if daily_cutoff < obj.taken_on <= today:
            daily_objects.append(obj)
        elif obj.taken_on <= daily_cutoff:
            ym = (obj.taken_on.year, obj.taken_on.month)
            existing = pre_daily_by_ym.get(ym)
            if existing is None or obj.taken_on > existing.taken_on:
                pre_daily_by_ym[ym] = obj

    # Step 2: daily — keep all.
    keepers.update(o.key for o in daily_objects)

    # Step 3: monthly. Keep one backup per (year, month) for the
    # MONTHLY_RETENTION_MONTHS most-recent buckets within the monthly
    # horizon (12 calendar months back from today). Buckets older than
    # the horizon are dropped here — they fall through to annual.
    monthly_horizon_ym = _ym_n_months_ago(today, MONTHLY_RETENTION_MONTHS)
    monthly_in_horizon = {
        ym: obj for ym, obj in pre_daily_by_ym.items()
        if ym >= monthly_horizon_ym
    }
    top_months = sorted(monthly_in_horizon.keys(), reverse=True)[
        :MONTHLY_RETENTION_MONTHS
    ]
    keepers.update(monthly_in_horizon[ym].key for ym in top_months)

    # Step 4: annual. Among years within the annual horizon
    # (ANNUAL_RETENTION_YEARS years back from today) AND not already
    # covered by daily/monthly, keep the latest object per year — top
    # ANNUAL_RETENTION_YEARS most-recent.
    #
    # The horizon cap drops ancient one-off backups: anything older
    # than 7 years from today goes regardless of how few objects exist.
    annual_horizon_year = today.year - ANNUAL_RETENTION_YEARS
    covered_years = {today.year} | {ym[0] for ym in top_months}

    annual_candidates: dict[int, BackupObject] = {}
    for obj in pre_daily_by_ym.values():
        y = obj.taken_on.year
        if y < annual_horizon_year:
            continue  # outside annual horizon
        if y in covered_years:
            continue  # already covered by daily/monthly
        existing = annual_candidates.get(y)
        if existing is None or obj.taken_on > existing.taken_on:
            annual_candidates[y] = obj

    top_years = sorted(annual_candidates.keys(), reverse=True)[
        :ANNUAL_RETENTION_YEARS
    ]
    keepers.update(annual_candidates[y].key for y in top_years)

    return keepers


def _ym_n_months_ago(d: date, n: int) -> tuple[int, int]:
    """(year, month) tuple n months before d's (year, month)."""
    total = d.year * 12 + (d.month - 1) - n
    year, month_idx = divmod(total, 12)
    return year, month_idx + 1


def select_for_deletion(
    objects: Iterable[BackupObject], *, today: date,
) -> list[str]:
    """Inverse of select_keepers — keys eligible for deletion."""
    objs = list(objects)
    keepers = select_keepers(objs, today=today)
    return [o.key for o in objs if o.key not in keepers]
