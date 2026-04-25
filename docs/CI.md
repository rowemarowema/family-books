# Family Books — CI / CD chain

This is the reference for how changes get from a working branch to
production. The chain has four enforcement points; if any is bypassed,
the discipline of the standing rules breaks down. Re-establishing the
rules from this doc is the rollback plan if a future change inadvertently
weakens them.

## The chain

```
local commit  →  push (triggers CI)  →  PR opened (triggers CI again)
              →  branch protection waits for CI green
              →  merge to main (only allowed when CI green + up-to-date)
              →  Render auto-deploys main
```

Each arrow is an enforcement boundary. The "branch protection waits"
arrow is the load-bearing one — it's what makes everything upstream
mandatory rather than optional.

## CI pipeline (`.github/workflows/ci.yml`)

GitHub Actions, ubuntu-latest runner, Postgres 16 service container.
Triggered by every push (any branch) AND by every PR targeting main.
Concurrency group keyed on `github.ref` with `cancel-in-progress: true`
so rapid iteration doesn't pile up runs.

Five gating steps + one informational step:

| # | Step | Make target | Posture | Fails build when |
|---|---|---|---|---|
| 1 | Django system checks | `make check` | gating | `manage.py check` returns non-zero |
| 2 | Apply migrations | `make migrate` | gating | Any migration errors on a fresh DB |
| 3 | Migration drift check | `python manage.py makemigrations --dry-run` | gating | Output doesn't contain "No changes detected" |
| 4 | Lint | `make lint` | gating | `ruff check .` finds violations |
| 5 | Type check | `make typecheck` | **informational (Group G)** | Never — `continue-on-error: true`. Re-promoted to gating in Group I after the annotation backlog is cleared |
| 6 | Tests + coverage | `make test` | gating | Any pytest failure OR coverage below 88% (`fail_under` in `pyproject.toml`) |

**Why typecheck is informational.** Adding lint and typecheck as CI
stages in Group G surfaced ~71 mypy findings accumulated across
Groups D–F — missing annotations on auto-generated migrations,
factory_boy class attributes, Django stub gaps. None are bugs (the
test suite proves correctness) but they're real annotations that
need to land. Promoting typecheck to gating mid-Group-G would have
either blocked Group G or forced a side-quest cleanup that distracted
from production-readiness (the actual goal). The honest gate posture
is "we run it, we don't yet enforce it"; Group I is where the
backlog gets cleared and the gate flips to mandatory.

`make lint`, in contrast, came in clean after auto-fixing 53
mechanical issues + targeted suppressions in `pyproject.toml` for
the 5 Django-convention false-positives (N806, N818, RUF012, DJ001,
DJ012, plus per-file ignores for tests + dev settings + migrations).
See pyproject.toml comments for the rationale on each.

**Step ordering rationale.** Lint and typecheck run before tests so
trivial breaks fail fast — cheaper to diagnose than test-suite
output, and saves test cycles when the diff has a one-line ruff
violation. Migration drift before lint because schema correctness is
load-bearing for everything downstream.

**Why mypy is scoped to `books/accounting`.** The accounting engine
is the load-bearing correctness surface; strict mode there catches
the most expensive bugs. Broader mypy coverage is a Stage 2 / Group I
effort. Don't expand the scope without a deliberate decision.

## Makefile targets

Local equivalents of the CI pipeline. Running these in order is the
pre-push contract.

| Target | Purpose | When to run |
|---|---|---|
| `make check` | Django system checks; no DB | Always, fast |
| `make migrate` | Apply migrations to local DB | When models or migrations changed |
| `make lint` | `ruff check .` | Before push; pre-commit hook in Group I |
| `make typecheck` | `mypy books/accounting` (strict) | Before push when touching the engine |
| `make test` | Full pytest with coverage gate (88%) | Before push |
| `make test-fast` | `pytest --reuse-db -x --ff` | During iteration; drop back to `make test` after migration changes (`--reuse-db` doesn't pick up schema changes) |

`make format` (ruff auto-fix) is available as a developer convenience
but isn't part of the verification chain — fixing then committing the
fix is the workflow.

## Branch protection rules (main)

Configured via GitHub Settings → Branches → Branch protection rules.
The settings below are the contract; if any is later relaxed, the
chain weakens. Reproduce these exactly if the GitHub-side state ever
needs to be reset.

| Rule | Setting |
|---|---|
| Require a pull request before merging | **On** |
| Require approvals | **Off** (single-user repo) |
| Dismiss stale approvals on new commits | N/A |
| Require status checks to pass before merging | **On**; required check: `build` (the workflow's job name) |
| Require branches to be up to date before merging | **On** |
| Require conversation resolution before merging | **On** |
| Require linear history | **On** (rebase-only; no merge commits on main) |
| Allow force pushes | **Off** |
| Allow deletions | **Off** |

The "Require status checks to pass" rule is the load-bearing one. The
`build` job name is what gets watched; if the job is renamed in the
workflow, this rule must be updated to match (or the gate becomes a
no-op). The CI sanity test (`tests/integration/test_ci_config.py`) pins
the job name implicitly via the workflow's structure assertions.

## Render auto-deploy (`render.yaml`)

Render watches the `main` branch and auto-deploys on any new commit.
Because branch protection requires CI green before merge, anything
Render sees has already passed the six-step pipeline.

Build steps (per `render.yaml`):

```
apt-get update && \
apt-get install -y libpango-1.0-0 libpangoft2-1.0-0 libgdk-pixbuf2.0-0 && \
pip install --upgrade pip && \
pip install . && \
python manage.py collectstatic --noinput && \
python manage.py migrate --noinput
```

The apt-install line is the only Family-Books-specific build step
(WeasyPrint needs Pango / GDK-PixBuf for PDF rendering — see ADR-006
and `docs/SETUP.md`). Group H adds a startup check that exercises
`import weasyprint` at boot and fails fast if the apt-install was
silently skipped.

**Manual deploy fallback.** Render's dashboard has a "Manual Deploy"
button that lets me roll back to a prior commit on `main` without
reverting in git. Use this for "production is broken, need to roll
back NOW" — then commit the actual revert afterward so the git state
matches what's deployed.

## Deploy gating chain

The implicit gating walks like this:

1. I push to a feature branch. CI runs.
2. I open a PR into main. CI runs again on the PR ref.
3. GitHub branch protection blocks the merge button until:
   - the `build` status check is green
   - the branch is up to date with main
   - any PR conversations are resolved
4. I merge (rebase) to main.
5. Render's webhook fires. Render runs its build and starts the new
   service.
6. If the Render build fails, the previous deploy stays live; no
   automatic rollback, but no broken state shipped either.

The chain has no explicit "wait for CI before deploying" step in
`render.yaml`. It doesn't need one: branch protection is the gate.
A Render-side gate would be redundant and add a second failure mode.

## When the chain breaks

If a CI failure surfaces a real bug, the failure mode is documented in
the relevant memory file (`feedback_assertion_strength.md` for
test/contract bugs; group-specific PROGRESS.md sections for design
issues). This doc is the chain reference, not a runbook for failures
within the chain — failures get handled in their own paper trail.
