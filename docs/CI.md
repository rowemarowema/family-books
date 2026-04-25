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
              →  SSH to droplet → ./scripts/deploy.sh → manual deploy
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

### `make check-deploy` posture

Step 2.5 (`make check-deploy`) runs Django's `check --deploy
--fail-level WARNING` against the **production** settings module.
On a real Render deploy, `SECRET_KEY` and `ALLOWED_HOSTS` come from
Render dashboard secrets. CI doesn't have those secrets and shouldn't
need them — the goal of the CI step is to verify **config structure**
(prod settings load without error, all deploy-time checks pass when
given shaped-correctly inputs), NOT to verify **production-secret
strength** (which is Render's concern at deploy time).

The CI workflow injects placeholder values at the step level:

```yaml
env:
  SECRET_KEY: "ci-placeholder-key-long-enough-to-pass-django-w009-check-and-have-50plus-chars"
  ALLOWED_HOSTS: "ci.example.com"
```

These satisfy:
- **W009** — SECRET_KEY length ≥ 50 chars and entropy thresholds.
- **W020** — ALLOWED_HOSTS not empty.

The values are deliberately obvious-placeholder so a future reader
can't mistake them for real secrets. Production values are scoped to
Render's env-var store and never appear in the repo.

**The W001 (WeasyPrint) check still fires as a build-failing error
in this step** if GTK isn't apt-installed on the runner. That's the
load-bearing assertion of the step; the placeholder env vars just
let W009/W020 pass so W001 isn't masked by them.

**Standing rule.** When adding a CI stage that targets prod settings
(`check --deploy`, `migrate` against prod settings, etc.), wire the
inline placeholder env vars **in the same commit**. Otherwise the
gate becomes "does CI know how to fail" rather than "does the config
work." See `memory/feedback_ci_stage_posture.md` for the broader
posture pattern.

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

## Production deploy — DigitalOcean droplet (Docker)

Production runs on a DigitalOcean droplet with the Docker pattern:
postgres + Django (gunicorn) + nginx, all via `docker-compose.prod.yml`.
Deploys are **manual** — operator SSHes to the droplet and runs
`./scripts/deploy.sh`. Branch protection still gates merge to main;
the operator picks the deploy moment.

The full first-time setup (DNS, Docker install, certbot, env vars,
container start) lives in `docs/DEPLOY.md`. This section is the CI/CD
chain reference; DEPLOY.md is the operator runbook.

**Why manual, not auto-deploy.** v1 is a single-user app; "deploy when
ready" beats "deploy on every merge." Operational discipline of running
`scripts/deploy.sh` by hand has real value (operator decides timing,
sees output live, knows what's running). GitHub Actions auto-deploy
is later polish if cadence demands it.

The deploy script (per `scripts/deploy.sh`) does:

```
git pull --ff-only
docker compose -f docker-compose.prod.yml --env-file .env.production build web
docker compose ... up -d postgres   # wait for healthy
docker compose ... run --rm --no-deps web python manage.py migrate
docker compose ... up -d --no-deps --build web
docker compose ... exec -T nginx nginx -s reload
```

Escape hatches: `SKIP_PULL=1` skips git pull (manual rsync workflow);
`SKIP_MIGRATE=1` skips Django migrations (rare; e.g., redeploying
the same code after a restart).

**Manual rollback.** If a deploy ships bad code:
1. SSH to droplet, `cd /home/app/family-books`.
2. `git checkout <previous-good-sha>`.
3. `SKIP_PULL=1 ./scripts/deploy.sh`.

For irreversible-migration cases, see `docs/ROLLBACK.md` § 3 — the
pre-migration backup + `restore_db --confirm-prod-restore` path.

Historical note: a previous version of this doc described Render-based
auto-deploy. That approach was abandoned before the first deploy in
favor of the Docker/DO pattern (which mirrors the rowe-contest
project on the same operator's hand). The artifact is preserved at
`docs/historical/render.yaml.unused` for archaeology.

## Deploy gating chain

The chain walks like this:

1. I push to a feature branch. CI runs.
2. I open a PR into main. CI runs again on the PR ref.
3. GitHub branch protection blocks the merge button until:
   - the `build` status check is green
   - the branch is up to date with main
   - any PR conversations are resolved
4. I merge (rebase) to main.
5. **Manual step**: when ready to ship, SSH to the droplet and run
   `./scripts/deploy.sh`. The script's first action is `git pull
   --ff-only`, so the droplet sees only what's on main.
6. If the deploy fails mid-script, the previous container is still
   running (deploy.sh restarts via `up -d --no-deps web`, which is
   atomic at the container level). Rolling back is `git checkout
   <previous-sha> && SKIP_PULL=1 ./scripts/deploy.sh`.

## When the chain breaks

If a CI failure surfaces a real bug, the failure mode is documented in
the relevant memory file (`feedback_assertion_strength.md` for
test/contract bugs; group-specific PROGRESS.md sections for design
issues). This doc is the chain reference, not a runbook for failures
within the chain — failures get handled in their own paper trail.
