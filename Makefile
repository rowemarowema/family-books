# Family Books — developer convenience targets.
#
# Every target is idempotent and safe to run repeatedly. On Windows, run from
# git-bash or WSL (the project scripts use Unix shell syntax).

PYTHON ?= python
MANAGE := $(PYTHON) manage.py
COMPOSE ?= docker compose

.PHONY: help
help: ## Show this help.
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

.PHONY: install
install: ## Install runtime + dev dependencies in editable mode.
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -e ".[dev]"

.PHONY: db-up
db-up: ## Start the local Postgres container.
	$(COMPOSE) up -d postgres

.PHONY: db-down
db-down: ## Stop the local Postgres container.
	$(COMPOSE) down

.PHONY: migrate
migrate: ## Apply all Django migrations.
	$(MANAGE) migrate

.PHONY: run
run: ## Run the dev server (requires db-up).
	$(MANAGE) runserver

.PHONY: shell
shell: ## Django shell.
	$(MANAGE) shell

.PHONY: lint
lint: ## Run ruff.
	ruff check .

.PHONY: format
format: ## Auto-fix lint issues.
	ruff check --fix .
	ruff format .

.PHONY: typecheck
typecheck: ## Run mypy (Group G: scoped to books.accounting; strict mode).
	mypy books/accounting

.PHONY: test
test: ## Run the full pytest suite with coverage gates.
	pytest --cov=books --cov-report=term-missing --cov-report=html

.PHONY: test-fast
test-fast: ## Run the test suite without coverage (faster feedback loop).
	pytest -x --ff

.PHONY: dataset
dataset: ## Load the seed test dataset (populated in later stages).
	$(MANAGE) load_test_dataset

.PHONY: backup
backup: ## Run a one-off encrypted backup (wired in Group H).
	$(MANAGE) backup_db

.PHONY: restore
restore: ## Restore from a backup object key (wired in Group H).
	@[ -n "$(BACKUP_ID)" ] || (echo "Usage: make restore BACKUP_ID=<id> TARGET=<db_url>" && exit 1)
	$(MANAGE) restore_db $(BACKUP_ID) --into "$(TARGET)"

.PHONY: drill-rollback
drill-rollback: ## Take a backup, restore into a scratch DB, verify tie-out (Group H5).
	$(MANAGE) drill_rollback

.PHONY: check
check: ## Run Django's system checks (does not connect to the database).
	$(MANAGE) check

.PHONY: check-deploy
check-deploy: ## Run deploy-mode security checks against prod settings (CI only).
	DJANGO_SETTINGS_MODULE=family_books.settings.prod $(MANAGE) check --deploy --fail-level WARNING

.PHONY: clean
clean: ## Remove caches and coverage artifacts.
	rm -rf .pytest_cache htmlcov .coverage .mypy_cache .ruff_cache
	find . -type d -name "__pycache__" -prune -exec rm -rf {} +
