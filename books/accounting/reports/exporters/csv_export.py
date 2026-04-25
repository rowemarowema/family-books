"""
CSV exporter for the trial balance.

Output shape (RFC-4180, UTF-8, CRLF):
  Header row:
    account_number,name,type,depth,debits,credits,own_balance,
    rollup_balance[,prior_own_balance]
  Data rows: walked depth-first in engine order, type subheader rows
    DROPPED (Q2 refinement — clean tables for downstream pandas/Excel
    consumers; the `type` column does the grouping).
  Indent: 4 leading spaces per (depth - 1) inside the `name` cell.
  Totals row: account_number="TOTAL", numerics populated, balance
    column carries "Balanced" or "OUT OF BALANCE".

Decimal cells emit canonical "X.XX" strings — no thousands separator,
no parens for negatives, no currency symbol — so round-trip parsing
is unambiguous. The HTML view is responsible for human-friendly
display; the CSV is the machine-friendly mirror.
"""
from __future__ import annotations

import csv
import io

from books.accounting.reports.trial_balance import TrialBalance, TrialBalanceRow


COLUMNS_BASE = [
    "account_number",
    "name",
    "type",
    "depth",
    "debits",
    "credits",
    "own_balance",
    "rollup_balance",
]
PRIOR_COLUMN = "prior_own_balance"
INDENT = "    "  # 4 spaces per depth level


def render_csv(tb: TrialBalance) -> bytes:
    """Render the trial balance as CSV bytes."""
    columns = list(COLUMNS_BASE)
    if tb.prior_as_of is not None:
        columns.append(PRIOR_COLUMN)

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, quoting=csv.QUOTE_MINIMAL)
    writer.writeheader()

    for row in _walk(tb.rows):
        record = {
            "account_number": row.account.account_number,
            "name": INDENT * (row.depth - 1) + row.account.name,
            "type": row.account.type,
            "depth": row.depth,
            "debits": _fmt(row.debits_total),
            "credits": _fmt(row.credits_total),
            "own_balance": _fmt(row.own_balance),
            "rollup_balance": _fmt(row.rollup_balance),
        }
        if tb.prior_as_of is not None:
            record[PRIOR_COLUMN] = (
                _fmt(row.prior_own_balance)
                if row.prior_own_balance is not None
                else ""
            )
        writer.writerow(record)

    # Totals row.
    totals = {
        "account_number": "TOTAL",
        "name": "",
        "type": "",
        "depth": "",
        "debits": _fmt(tb.total_debits),
        "credits": _fmt(tb.total_credits),
        "own_balance": "Balanced" if tb.is_balanced else "OUT OF BALANCE",
        "rollup_balance": "",
    }
    if tb.prior_as_of is not None:
        totals[PRIOR_COLUMN] = ""
    writer.writerow(totals)

    return buf.getvalue().encode("utf-8")


def _fmt(value) -> str:
    """Canonical Decimal-as-string. No thousands separator; raw negative
    sign for negatives (parens are HTML's job). 2 decimal places are
    already the storage shape (DecimalField max_digits=18, decimals=2)."""
    return str(value)


def _walk(rows):
    for row in rows:
        yield row
        yield from _walk(row.children)
