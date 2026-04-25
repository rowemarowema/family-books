"""
PDF exporter — WeasyPrint (HTML → PDF).

Reuses the F.4 trial-balance template with a print-specific extension
(`templates/reports/trial_balance_print.html`) that adds @page CSS for
A4 portrait + small body font.

Production posture (Mark's call): WeasyPrint is a runtime dep
(pyproject.toml); Render's build command apt-installs the GTK system
libs (libpango, libgdk-pixbuf). Local Windows dev that hasn't
installed GTK degrades gracefully via lazy import + a 503 from the
view; this module raises PDFRendererUnavailable so the view can
distinguish "GTK missing" from real render errors.

The print template extends `templates/base.html` so styling + Bootstrap
stay shared with the on-screen HTML — only the @page CSS and the
header / footer chrome differ.
"""
from __future__ import annotations

from typing import Any

from django.template.loader import render_to_string

from books.accounting.reports.trial_balance import TrialBalance, TYPE_ORDER


class PDFRendererUnavailable(RuntimeError):
    """Raised when WeasyPrint can't be imported (typically: GTK system
    libraries are not installed). The view layer catches this and
    returns 503 so the user sees a clear "PDF rendering unavailable"
    response rather than a generic 500.

    In production on Render, the build command apt-installs the GTK
    deps and this exception should never fire."""


def render_pdf(tb: TrialBalance, request) -> bytes:
    """Render the trial balance as PDF bytes via WeasyPrint.

    Lazy import of weasyprint so the module imports cleanly on
    Windows-without-GTK. Failure to import is rethrown as a typed
    PDFRendererUnavailable so the view can branch.
    """
    try:
        from weasyprint import HTML
    except (ImportError, OSError) as exc:
        # WeasyPrint raises OSError when the underlying Pango/Cairo
        # shared libraries can't be loaded — e.g., on Windows without
        # the GTK runtime. Treat both as "unavailable" so the view
        # can return 503.
        raise PDFRendererUnavailable(
            "WeasyPrint failed to load. On Render, ensure the build "
            "command installs libpango-1.0-0, libpangoft2-1.0-0, "
            "libgdk-pixbuf2.0-0. On Windows local dev, install the GTK "
            "runtime per docs/SETUP.md."
        ) from exc

    rows_by_type: dict[str, list] = {t: [] for t in TYPE_ORDER}
    for row in _walk(tb.rows):
        rows_by_type.setdefault(row.account.type, []).append(row)

    from books.accounting.models import AccountType
    type_label = {choice.value: choice.label for choice in AccountType}
    groups = [
        {
            "type_value": t,
            "type_label": type_label.get(t, t.title()),
            "flat_rows": rows_by_type.get(t, []),
        }
        for t in TYPE_ORDER
    ]

    html_string = render_to_string(
        "reports/trial_balance_print.html",
        {"tb": tb, "groups": groups},
        request=request,
    )

    # base_url lets WeasyPrint resolve relative URLs (CSS, images) if
    # the template ever references local static files. Today the
    # template only pulls Bootstrap from a CDN; base_url is harmless.
    return HTML(string=html_string, base_url=str(_base_url(request))).write_pdf()


def _base_url(request) -> str:
    return request.build_absolute_uri("/")


def _walk(rows):
    for row in rows:
        yield row
        yield from _walk(row.children)
