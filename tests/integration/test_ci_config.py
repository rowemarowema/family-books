"""Sanity tests for .github/workflows/ci.yml.

Locks the workflow's structure so a future PR can't accidentally drop
a verification stage, reorder them, or unpin the Postgres / Python
versions. This test parses the YAML file and asserts the load-bearing
shape; the actual workflow runs on every push and is the real
verification of the CI itself.

The four-step verification + lint + typecheck order is non-negotiable:
  check → migrate → dry-run → lint → typecheck → test

Each gates the next; failure aborts the pipeline.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml


WORKFLOW_PATH = (
    Path(__file__).resolve().parents[2]
    / ".github" / "workflows" / "ci.yml"
)


@pytest.fixture(scope="module")
def workflow() -> dict:
    assert WORKFLOW_PATH.exists(), f"Workflow file missing at {WORKFLOW_PATH}"
    with open(WORKFLOW_PATH) as fp:
        return yaml.safe_load(fp)


@pytest.fixture(scope="module")
def steps(workflow) -> list[dict]:
    return workflow["jobs"]["build"]["steps"]


@pytest.fixture(scope="module")
def step_names(steps) -> list[str]:
    return [s.get("name", "") for s in steps]


# ---------------------------------------------------------------------------
# Required steps + ordering
# ---------------------------------------------------------------------------


def _index_of(step_names: list[str], token: str) -> int:
    """Find the first step whose name contains `token` (case-insensitive)."""
    for i, name in enumerate(step_names):
        if token.lower() in name.lower():
            return i
    return -1


def test_all_six_required_steps_present(step_names):
    """The four-step verification + lint + typecheck must all be
    represented as named steps. Bare commands without step names
    don't count — `name:` is what the GitHub UI surfaces and is the
    contract this test pins.
    """
    required = ["make check", "make migrate", "dry-run",
                "make lint", "make typecheck", "make test"]
    missing = [t for t in required if _index_of(step_names, t) < 0]
    assert not missing, f"Required CI steps missing: {missing}"


def test_steps_run_in_required_order(step_names):
    """check → migrate → dry-run → lint → typecheck → test.

    Reordering changes the failure mode (e.g., running lint before
    migrate would mean lint failures mask migration drift). Lock it.
    """
    check_idx = _index_of(step_names, "make check")
    migrate_idx = _index_of(step_names, "make migrate")
    dryrun_idx = _index_of(step_names, "dry-run")
    lint_idx = _index_of(step_names, "make lint")
    typecheck_idx = _index_of(step_names, "make typecheck")
    test_idx = _index_of(step_names, "make test")

    assert check_idx < migrate_idx, "check must precede migrate"
    assert migrate_idx < dryrun_idx, "migrate must precede dry-run"
    assert dryrun_idx < lint_idx, "dry-run must precede lint"
    assert lint_idx < typecheck_idx, "lint must precede typecheck"
    assert typecheck_idx < test_idx, "typecheck must precede test"


# ---------------------------------------------------------------------------
# Pinned versions
# ---------------------------------------------------------------------------


def test_python_version_pinned_to_312(steps):
    """Python pinned to 3.12 to match the project's requires-python.
    A floating Python version is a 'works on my machine' bug waiting
    to happen."""
    setup_python = next(
        (s for s in steps if s.get("uses", "").startswith("actions/setup-python")),
        None,
    )
    assert setup_python is not None, "actions/setup-python step missing"
    assert setup_python["with"]["python-version"] == "3.12"


def test_postgres_pinned_to_16(workflow):
    """Postgres major pinned to 16 to match production (Render).
    Floating tags would let CI drift from prod and produce 'green
    in CI, red in prod' bugs."""
    services = workflow["jobs"]["build"]["services"]
    assert services["postgres"]["image"] == "postgres:16"


# ---------------------------------------------------------------------------
# Concurrency cancel-in-progress
# ---------------------------------------------------------------------------


def test_concurrency_cancels_in_progress(workflow):
    """Rapid iteration on a feature branch shouldn't pile up CI runs;
    the latest commit is the one we care about."""
    concurrency = workflow.get("concurrency", {})
    assert concurrency.get("cancel-in-progress") is True
    # Group on ref so different branches stay independent (a feature
    # branch run doesn't cancel a main run).
    assert "github.ref" in concurrency.get("group", "")


# ---------------------------------------------------------------------------
# GTK system libs (required for F.5 PDF export tests)
# ---------------------------------------------------------------------------


def test_gtk_system_libs_apt_installed(steps):
    """WeasyPrint needs Pango + GDK-PixBuf to render PDFs. Without
    these, the PDF tests fail outright on Linux (the SKIP_PDF_ON_
    WINDOWS_NO_GTK gate only applies to Windows). Mirrors the
    apt-install line in render.yaml so CI matches production."""
    apt_steps = [
        s for s in steps
        if "apt-get install" in s.get("run", "")
    ]
    assert apt_steps, "GTK apt-install step missing"
    apt_run = apt_steps[0]["run"]
    for lib in ("libpango-1.0-0", "libpangoft2-1.0-0", "libgdk-pixbuf2.0-0"):
        assert lib in apt_run, f"Missing GTK lib in apt-install: {lib}"
