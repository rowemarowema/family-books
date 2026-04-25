# Deploying Family Books to production

Step-by-step runbook for bringing the site up on a fresh DigitalOcean
droplet. Assumes no prior Docker or Linux experience — copy and paste
commands, one block at a time. Mirrors the rowe-contest project's
DEPLOY.md so 3am runbooks stay uniform across both projects.

> **Domain:** `books.markrowecontest.com`
> **App lives at:** `/home/app/family-books`
> **OS:** Ubuntu 22.04 LTS
> **Server IP:** _(filled in at provisioning)_

---

## 1. Point DNS at the server

In the registrar for `markrowecontest.com`, add an A record:

| Host    | Type | Value                |
| ------- | ---- | -------------------- |
| `books` | A    | `<droplet-ip>`       |

DNS propagation can take 1 minute to a few hours. Verify with:

```bash
dig +short books.markrowecontest.com
```

Should return your droplet IP. **Do not continue to step 6 (SSL)
until this is true** — Let's Encrypt will fail otherwise.

---

## 2. Connect to the server via SSH

```bash
ssh root@<droplet-ip>
```

All remaining steps run **on the server**, not on your laptop.

---

## 3. Install Docker

```bash
apt-get update
apt-get install -y ca-certificates curl gnupg
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg

echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  > /etc/apt/sources.list.d/docker.list

apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io \
                   docker-buildx-plugin docker-compose-plugin

docker --version
docker compose version
```

Both should print version numbers. If not, stop and investigate.

---

## 4. Open the firewall

```bash
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
ufw status
```

---

## 5. Configure environment variables

Pull the repo into `/home/app/family-books` (use `git clone` or
deploy a tarball — whatever your provisioning workflow uses).

```bash
cd /home/app/family-books
cp .env.production.example .env.production
chmod 600 .env.production
nano .env.production
```

Fill in every `replace-me-...` placeholder per the template's inline
comments. Generate random secrets with:

```bash
# Django SECRET_KEY (50+ chars, high entropy)
python3 -c "import secrets; print(secrets.token_urlsafe(64))"

# POSTGRES_PASSWORD
openssl rand -base64 32
```

The B2 + age values come from the operator's password manager
(LastPass per Q1) and the Backblaze B2 console.

> **Double-check:** `ls -l .env.production` should show
> `-rw------- 1 root root ...`.

---

## 6. Get an SSL certificate from Let's Encrypt

```bash
apt-get install -y certbot

certbot certonly --standalone \
  -d books.markrowecontest.com \
  --agree-tos -m mark@markrowecontest.com --no-eff-email
```

Successful output ends with:

```
Certificate is saved at: /etc/letsencrypt/live/books.markrowecontest.com/fullchain.pem
Key is saved at:         /etc/letsencrypt/live/books.markrowecontest.com/privkey.pem
```

### Auto-renewal

Certbot installs a systemd timer automatically. Verify:

```bash
systemctl list-timers | grep certbot
```

Add a renewal hook so nginx picks up the new cert without a full
container restart:

```bash
mkdir -p /etc/letsencrypt/renewal-hooks/deploy
cat > /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh <<'EOF'
#!/usr/bin/env bash
cd /home/app/family-books
docker compose -f docker-compose.prod.yml --env-file .env.production \
  exec -T nginx nginx -s reload || true
EOF
chmod +x /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh
```

---

## 7. Start all containers

```bash
cd /home/app/family-books
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build
```

This pulls postgres:16-alpine and nginx:1.27-alpine, builds the
Family Books image from `Dockerfile`, and starts the three services.
First build takes 3–5 minutes.

```bash
docker compose -f docker-compose.prod.yml ps
```

`postgres` should be `Up (healthy)`. `web` and `nginx` should be `Up`.

---

## 8. Run database migrations

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production \
  run --rm --no-deps web python manage.py migrate
```

Output ends with `... no migrations to apply.` only after a
successful first run.

---

## 9. Bootstrap the owner account

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production \
  run --rm --no-deps web python manage.py bootstrap_owner \
    --email mark@markrowecontest.com --password-stdin
```

The command reads the password from stdin. Generate a strong one
beforehand and paste it when prompted (`Ctrl-D` to terminate input).

---

## 10. Seed the default chart of accounts

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production \
  run --rm --no-deps web python manage.py seed_default_coa
```

Output: `Seeded 3 system + 640 user accounts (NNN parent links) from
default_coa.json.`

---

## 11. Verify everything works

- **HTTPS loads:** <https://books.markrowecontest.com> returns the site (no browser warning).
- **Redirect works:** <http://books.markrowecontest.com> redirects to `https://`.
- **Login works:** sign in with the bootstrap-owner credentials; complete 2FA enrollment.
- **Stack health:**
  ```bash
  docker compose -f docker-compose.prod.yml ps
  # all three services: Up / Up (healthy)
  ```
- **Security headers:**
  ```bash
  curl -sI https://books.markrowecontest.com | grep -iE 'strict-transport|x-frame|x-content-type'
  ```
- **Trial balance renders empty:**
  - <https://books.markrowecontest.com/reports/trial-balance/> returns 200 with the empty Bootstrap table (no opening balances posted yet).

---

## 12. Set up nightly database backups

The backup runs `python manage.py backup_db` inside the `web`
container — encrypted with age, uploaded to Backblaze B2, retention
prune per ADR-001. The cron entry calls a wrapper script.

```bash
# Test the wrapper manually first
chmod +x /home/app/family-books/scripts/cron-backup.sh
/home/app/family-books/scripts/cron-backup.sh

# Confirm the BACKUP_CREATED audit row appeared
docker compose -f docker-compose.prod.yml --env-file .env.production \
  exec -T web python manage.py shell -c \
  "from books.audit.models import AuditLog, AuditAction; \
   r = AuditLog.objects.filter(action=AuditAction.BACKUP_CREATED).order_by('-timestamp').first(); \
   print(r.entity_id, r.after_value)"
```

Install the cron job (runs at 03:00 UTC daily):

```bash
crontab -e
```

Add this line:

```
0 3 * * * /home/app/family-books/scripts/cron-backup.sh >> /var/log/family-books-backup.log 2>&1
```

Verify:

```bash
crontab -l
```

> **Note on prefix:** the cron-backup script uses the production B2
> prefix (default: `prod/`). Drill artifacts use `dev-test/`. **Never
> co-mingle.** See `docs/ROLLBACK.md` § drill log for the full
> reasoning.

---

## 13. Deploying future updates

When new code lands on `main`:

```bash
ssh root@<droplet-ip>
cd /home/app/family-books
./scripts/deploy.sh
```

That script:

1. `git pull --ff-only`
2. Rebuilds the `web` image
3. Waits for postgres healthcheck
4. Applies any new Django migrations
5. Recreates **only** the `web` container (postgres + nginx untouched)
6. Reloads nginx

Escape hatches: `SKIP_PULL=1` (manual rsync workflow),
`SKIP_MIGRATE=1` (rare; e.g., redeploying same code after restart).

If a deploy ships bad code:

```bash
git checkout <previous-good-sha>
SKIP_PULL=1 ./scripts/deploy.sh
```

For irreversible-migration cases, see `docs/ROLLBACK.md` § 3.

---

## 14. Common operational commands

| Task                           | Command                                                                                          |
| ------------------------------ | ------------------------------------------------------------------------------------------------ |
| View live web logs             | `docker compose -f docker-compose.prod.yml logs -f web`                                          |
| Restart just web               | `docker compose -f docker-compose.prod.yml restart web`                                          |
| Stop everything                | `docker compose -f docker-compose.prod.yml down`                                                 |
| Start everything               | `docker compose -f docker-compose.prod.yml --env-file .env.production up -d`                     |
| Open a shell inside web        | `docker compose -f docker-compose.prod.yml exec web bash`                                        |
| Open a Django shell            | `docker compose -f docker-compose.prod.yml exec web python manage.py shell`                      |
| Open a psql prompt             | `docker compose -f docker-compose.prod.yml exec postgres psql -U $POSTGRES_USER $POSTGRES_DB`    |
| Trigger an ad-hoc backup       | `/home/app/family-books/scripts/cron-backup.sh`                                                  |
| Tail backup cron log           | `tail -f /var/log/family-books-backup.log`                                                       |
| Disk + Docker disk usage       | `df -h` + `docker system df`                                                                     |
| Free old Docker images         | `docker image prune -f`                                                                          |

---

## Troubleshooting

**Browser certificate warning.** Cert isn't mounted or didn't issue.
Check `/etc/letsencrypt/live/books.markrowecontest.com/fullchain.pem`
exists on the host, and that `docker compose ps` shows nginx as `Up`.

**`502 Bad Gateway`.** The web container isn't healthy:
```bash
docker compose -f docker-compose.prod.yml logs --tail=200 web
```

**Migration fails with "database does not exist".** Postgres hadn't
finished initializing. Wait 20s and re-run step 8.

**WeasyPrint W001 warning at boot.** GTK libs missing. Confirm the
Dockerfile's apt-install line completed. Rebuild:
```bash
docker compose -f docker-compose.prod.yml build --no-cache web
```

**Backup stops running silently.** See `docs/ROLLBACK.md` § 6 — the
manual query that catches missing `BACKUP_CREATED` audit rows.
