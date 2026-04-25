"""URL conf for books.web — Stage 1 reports.

The `name="trial-balance"` is part of the public URL contract:
- `reverse("trial-balance")` is used by exports (F.5) to build
  download links and by the URL-drift test to lock the path.
- Renaming requires a search-and-replace of every `reverse()` call site.
"""
from __future__ import annotations

from django.urls import path

from books.web.views.reports import trial_balance_view

urlpatterns = [
    path(
        "reports/trial-balance/",
        trial_balance_view,
        name="trial-balance",
    ),
]
