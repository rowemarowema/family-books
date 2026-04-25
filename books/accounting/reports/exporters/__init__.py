"""
Trial-balance export renderers.

Each module exposes a single render function that takes a
TrialBalance dataclass and returns bytes. The view layer
(books.web.views.reports) wires Content-Type + Content-Disposition
around the bytes.

Same engine source as the HTML view. Cell-level round-trip tests
prove HTML/CSV/XLSX agree on every cell value vs. the engine; PDF
gets a separate render + text-extract smoke (full PDF cell parsing
is fragile and disproportionate for v1).
"""
from books.accounting.reports.exporters.csv_export import render_csv
from books.accounting.reports.exporters.xlsx_export import render_xlsx
from books.accounting.reports.exporters.pdf_export import (
    PDFRendererUnavailable,
    render_pdf,
)

__all__ = [
    "render_csv",
    "render_xlsx",
    "render_pdf",
    "PDFRendererUnavailable",
]
