"""Tests for the W001 WeasyPrint Django check.

Posture: Warning, not Error. Local `make check` doesn't block; CI's
`make check-deploy` (--fail-level WARNING) does.
"""
from __future__ import annotations

import sys
from unittest.mock import patch

import pytest


@pytest.fixture
def fresh_check_import():
    """The check function caches nothing; fresh import each call."""
    from books.web.checks import weasyprint_can_load
    return weasyprint_can_load


def test_check_returns_empty_when_weasyprint_loads(fresh_check_import):
    """If weasyprint imports cleanly, the check is silent."""
    # In CI Linux with GTK installed, this is the normal case.
    # In Windows local dev without GTK, weasyprint may not import —
    # in which case we'd see a Warning, also acceptable. This test
    # asserts the success-path behavior; the next test asserts the
    # failure-path behavior with explicit mocking.
    try:
        import weasyprint  # noqa: F401
        weasyprint_loadable = True
    except (ImportError, OSError):
        weasyprint_loadable = False

    if not weasyprint_loadable:
        pytest.skip(
            "weasyprint not loadable in this env; success path "
            "covered when GTK is present (CI Linux + Render)."
        )

    result = fresh_check_import(app_configs=None)
    assert result == []


def test_check_emits_warning_when_weasyprint_import_fails(fresh_check_import):
    """ImportError on weasyprint produces a single Warning with id
    books.web.W001 + a hint pointing at docs/SETUP.md and render.yaml."""
    # Patch sys.modules to force ImportError on weasyprint (re-import).
    # The check uses `import weasyprint` inside the function, so we
    # need the module lookup to fail.
    with patch.dict(sys.modules, {"weasyprint": None}):
        result = fresh_check_import(app_configs=None)

    assert len(result) == 1
    warn = result[0]
    assert warn.id == "books.web.W001"
    assert "WeasyPrint failed to load" in warn.msg
    assert "render.yaml" in warn.hint
    assert "docs/SETUP.md" in warn.hint


def test_check_emits_warning_when_weasyprint_raises_oserror(fresh_check_import):
    """On Windows without GTK, weasyprint imports but raises OSError
    when loading the underlying Pango/Cairo shared libs. The check
    treats OSError the same as ImportError — both mean "unavailable".
    """
    def _raise_oserror(name, *args, **kwargs):
        if name == "weasyprint":
            raise OSError("cannot find Pango library")
        return original_import(name, *args, **kwargs)

    original_import = __builtins__["__import__"] if isinstance(
        __builtins__, dict,
    ) else __builtins__.__import__

    with patch("builtins.__import__", side_effect=_raise_oserror):
        result = fresh_check_import(app_configs=None)

    assert len(result) == 1
    assert result[0].id == "books.web.W001"
