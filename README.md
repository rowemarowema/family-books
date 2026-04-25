# Family Books

Single-user, cloud-hosted personal finance system for the Rowe
household. Double-entry accounting from day one; calendar-year
fiscal year; USD only.

This is a private project. It is not accepting issues, pull
requests, or external contributions.

## Stack

- **Django 5.2** (Python 3.12), gunicorn, WhiteNoise.
- **PostgreSQL 16**.
- **Docker Compose** on a **DigitalOcean droplet** (postgres + web
  + nginx). nginx terminates TLS via certbot.
- **Backblaze B2** for off-site encrypted backups (`age` + nightly
  cron). 30 daily + 12 monthly + 7 annual retention.
- **GitHub Actions** for CI; manual deploy via
  `./scripts/deploy.sh`.
- 2FA enforced (`django-two-factor-auth`); `django-axes` for
  failed-login lockout.

## Documentation

- [`docs/BUILD_SPEC.docx`](docs/BUILD_SPEC.docx) — authoritative
  build specification.
- [`docs/DEPLOY.md`](docs/DEPLOY.md) — first-time droplet
  provisioning runbook (DNS, Docker, ufw, certbot, env vars).
- [`docs/CI.md`](docs/CI.md) — CI / CD chain reference (branch
  protection, deploy gating, Makefile targets).
- [`docs/ROLLBACK.md`](docs/ROLLBACK.md) — rollback playbook
  (bad-deploy, migration reversal, host-down, missed backup).
- [`docs/RECOVERY.md`](docs/RECOVERY.md) — credential-loss
  recovery (lost TOTP, lost password, axes lockout).
- [`docs/SETUP.md`](docs/SETUP.md) — local dev setup, including
  Windows + GTK install path for WeasyPrint.
- [`PROGRESS.md`](PROGRESS.md) — staged build log; current state,
  decisions, deferred items.

## Locked out?

Every realistic lockout — forgotten password, lost TOTP device,
axes lockout, bad 2FA state — is covered in
[`docs/RECOVERY.md`](docs/RECOVERY.md). Print a paper copy.

## Local development

Standard pre-commit verification chain:

```bash
make check
make migrate
python manage.py makemigrations --dry-run
make test
```

See [`docs/CI.md`](docs/CI.md) for the per-step purpose and
[`docs/SETUP.md`](docs/SETUP.md) for first-time setup.

## License

Proprietary. All rights reserved.
