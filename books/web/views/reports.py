"""
Report views. Owner-only + 2FA-gated; engine output rendered as HTML.

F.4 ships format=html only. F.5 will add csv|xlsx|pdf to this same view
by branching on the `format` query param after compute_trial_balance()
returns. The engine call is shared across formats — every format
renders the SAME TrialBalance dataclass; only the surface changes.

Why dispatch on a query param instead of separate view functions:
- One URL (`/reports/trial-balance/`), reverse() returns the same path
  regardless of format. Export download links are query-string variants.
- One auth + parameter-validation block, no copy-paste.
- The cell-level tie-out test (HTML) and the export round-trip tests
  (F.5) cover the same engine output, eliminating drift between formats.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
)
from django.shortcuts import render
from django.utils import timezone

from books.accounting.models import AccountType
from books.accounting.reports.trial_balance import (
    TYPE_ORDER,
    TrialBalanceRow,
    compute_trial_balance,
)
from books.core.auth import owner_only_with_2fa


# F.4 ships html only. F.5 will extend this tuple to include
# csv|xlsx|pdf and add format-dispatch branches below.
VALID_FORMATS = ("html",)


@owner_only_with_2fa
def trial_balance_view(request: HttpRequest) -> HttpResponse:
    """GET /reports/trial-balance/?as_of=YYYY-MM-DD
                                  &prior_as_of=YYYY-MM-DD
                                  &include_zero=1
                                  &types=asset,liability
                                  &format=html

    Defaults: as_of=today (server tz), include_zero=False, types=all,
    format=html.

    Returns:
      200: rendered HTML.
      400: with a clear message naming the invalid query param. e.g.,
           "as_of must be YYYY-MM-DD; got 'banana'".
      403: from the @owner_only_with_2fa decorator.
    """
    try:
        params = _parse_params(request)
    except _BadParam as exc:
        return HttpResponseBadRequest(str(exc))

    tb = compute_trial_balance(
        as_of=params["as_of"],
        prior_as_of=params.get("prior_as_of"),
        include_zero=params["include_zero"],
        types=params.get("types"),
    )

    # Only "html" is permitted by VALID_FORMATS today; F.5 extends.
    return _render_html(request, tb)


# ---------------------------------------------------------------------------
# Query parameter parsing
# ---------------------------------------------------------------------------


class _BadParam(ValueError):
    """Internal: raised by _parse_params() so the view returns a 400
    with the exception message verbatim."""


def _parse_params(request: HttpRequest) -> dict[str, Any]:
    raw = request.GET

    as_of = _parse_date(raw.get("as_of"), default=timezone.localdate(),
                        param="as_of")
    prior_as_of = _parse_date(raw.get("prior_as_of"), default=None,
                              param="prior_as_of")

    include_zero_raw = raw.get("include_zero", "")
    include_zero = include_zero_raw.lower() in ("1", "true", "yes", "on")

    types_raw = raw.get("types", "").strip()
    if types_raw:
        requested = [t.strip().lower() for t in types_raw.split(",") if t.strip()]
        valid = {choice.value for choice in AccountType}
        invalid = [t for t in requested if t not in valid]
        if invalid:
            raise _BadParam(
                f"types includes unknown value(s) {invalid}; valid: "
                f"{sorted(valid)}."
            )
        types = tuple(requested)
    else:
        types = None

    fmt = raw.get("format", "html").lower()
    if fmt not in VALID_FORMATS:
        raise _BadParam(
            f"format must be one of {list(VALID_FORMATS)}; got {fmt!r}."
        )

    return {
        "as_of": as_of,
        "prior_as_of": prior_as_of,
        "include_zero": include_zero,
        "types": types,
        "format": fmt,
    }


def _parse_date(raw: str | None, *, default: date | None, param: str) -> date | None:
    if raw is None or raw == "":
        return default
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise _BadParam(
            f"{param} must be YYYY-MM-DD; got {raw!r}."
        ) from exc


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------


def _render_html(request: HttpRequest, tb) -> HttpResponse:
    """Build template context: tb (the dataclass) + groups (one entry
    per AccountType in TYPE_ORDER, with flat_rows already in
    engine-defined order).

    The template iterates groups → flat_rows so a type-header always
    appears before its rows, regardless of whether the type has any
    visible activity. (Empty type sections render the header alone;
    the structural-drift test relies on all 5 headers being present.)
    """
    rows_by_type: dict[str, list[TrialBalanceRow]] = {t: [] for t in TYPE_ORDER}
    for row in _walk(tb.rows):
        rows_by_type.setdefault(row.account.type, []).append(row)

    type_label = {choice.value: choice.label for choice in AccountType}
    groups = [
        {
            "type_value": t,
            "type_label": type_label.get(t, t.title()),
            "flat_rows": rows_by_type.get(t, []),
        }
        for t in TYPE_ORDER
    ]

    return render(
        request,
        "reports/trial_balance.html",
        {"tb": tb, "groups": groups},
    )


def _walk(rows):
    for row in rows:
        yield row
        yield from _walk(row.children)
