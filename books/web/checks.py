"""
Django system checks for books.web.

W001 — WeasyPrint can be loaded. Posture: Warning (not Error) so
local Windows-without-GTK dev isn't blocked from `make check`.
Production gates via `make check-deploy` which runs
`manage.py check --deploy --fail-level WARNING`, converting this
Warning into a build-failing Error in render.yaml's build command
+ in the CI workflow stage between `make migrate` and
`make makemigrations --dry-run`.

The chain (refinement #1 from the H.4 breakdown):
    Local pre-commit (`make check`)             — Warning, doesn't block
    CI (`make check-deploy`, --fail-level WARNING) — Error, blocks build
    Render build (`make check-deploy`)           — Error, blocks deploy

The runtime 503 fallback in books/accounting/reports/exporters/
pdf_export.py (PDFRendererUnavailable) becomes "should never fire"
in production rather than just "shouldn't fire."
"""
from __future__ import annotations

from typing import Any

from django.core.checks import Warning, register

W001_HINT = (
    "WeasyPrint requires Pango / Cairo system libraries. On Render, "
    "the build command must apt-install libpango-1.0-0 "
    "libpangoft2-1.0-0 libgdk-pixbuf2.0-0 (see render.yaml). On "
    "Windows local dev, install the GTK3 runtime per docs/SETUP.md. "
    "PDF endpoints will return 503 until WeasyPrint loads."
)


@register()
def weasyprint_can_load(app_configs: Any, **kwargs: Any) -> list[Warning]:
    """W-level check: weasyprint imports cleanly on this host.

    Treats both ImportError (package missing) and OSError (system
    libs missing) as "unavailable" — matches the F.5
    PDFRendererUnavailable contract.
    """
    try:
        import weasyprint  # noqa: F401
    except (ImportError, OSError) as exc:
        return [
            Warning(
                f"WeasyPrint failed to load: {exc}",
                hint=W001_HINT,
                id="books.web.W001",
            ),
        ]
    return []
