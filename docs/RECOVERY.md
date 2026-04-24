# Recovery runbook

Every realistic way you can lock yourself out of Family Books, and the
management-command path out of each one. This is a single-user system —
there is no "email support" to fall back on. The root of trust is your
Render account (or equivalent host access). If you control the host,
you can recover from anything; if you lose that, nothing below helps.

**Keep a paper copy of this file.** A locked-out Mark cannot read a
Markdown file hosted inside the system he's locked out of.

---

## 0. Prerequisite — how to run a management command on Render

1. Sign in to the Render dashboard with your Render-account credentials.
2. Open the Family Books web service.
3. Click **Shell** (top-right). Render drops you into a bash prompt
   inside a fresh instance of the web service container with the code
   and `.env` already loaded.
4. Run commands with `python manage.py <command>`.

All recoveries below assume you are in that shell.

---

## 1. Forgot password (TOTP device still works)

Use Django's built-in password change:

```bash
python manage.py changepassword owner@markrowecontest.com
```

You'll be prompted for the new password twice. It's validated against
`AUTH_PASSWORD_VALIDATORS` — min 12 chars, not a common password, etc.

On next login you still need your OTP code. No audit log entry is
written automatically by `changepassword` (it's upstream Django), but
the Render shell session itself is logged by Render for ≥ 30 days.

---

## 2. Lost TOTP device (password still works)

If you still have your **printed recovery tokens** from enrollment, use
one of them at the login prompt (the login form has a "I don't have my
device" link that accepts a static token). Each token is single-use.

If the recovery tokens are also gone, wipe the 2FA devices:

```bash
python manage.py reset_owner_2fa --confirm-reset "<why>"
```

On next successful password login you'll be redirected to
`/account/two_factor/setup/` to enroll a fresh TOTP device and get a
fresh set of recovery codes. The reset is written to the audit log
with your supplied reason.

---

## 3. Lost both password and TOTP device

Run both recoveries, password first:

```bash
python manage.py changepassword owner@markrowecontest.com
python manage.py reset_owner_2fa --confirm-reset "<why>"
```

Log in with the new password; you'll be redirected to the 2FA setup
page because no devices remain. Enroll a new device and save the
recovery codes somewhere durable this time.

---

## 4. Locked out by django-axes (too many failed attempts)

Either wait 15 minutes (`AXES_COOLOFF_TIME = timedelta(minutes=15)`)
or clear the lockout manually:

```bash
python manage.py axes_reset_username owner@markrowecontest.com
```

To clear lockouts by IP instead:

```bash
python manage.py axes_reset_ip <ip-address>
```

To nuke all axes state (rare, usually only during testing):

```bash
python manage.py axes_reset
```

---

## 5. 2FA enforcement is on, but something is wrong with the 2FA flow

Examples: upstream bug in `django-two-factor-auth`, a setup page that
won't render, or a middleware regression that redirect-loops the OTP
check. You need to bypass enforcement temporarily.

```bash
python manage.py disable_2fa_enforcement \
    --confirm-disable "two_factor:setup is broken; see issue #N"
```

This grants a 24-hour grace window (non-extensible past 24h — clamped
in code). The middleware auto-re-enables enforcement when the window
expires. Use the window to fix the code path, test, and deploy.

If you need more than 24h you can re-run the command; each run writes
an audit entry with the reason.

**You cannot disable enforcement by editing the `REQUIRE_2FA` env var
in Render.** That was a deliberate design choice (decision #22): the
env var is additive — it can turn enforcement *on* but cannot turn it
*off* once the SystemFlag row is sticky. This command is the only
path.

---

## 6. The single owner user has been corrupted / deleted somehow

Very unlikely (the single-user signal and admin's disabled-delete
permission guard against most ways this could happen), but if it does:

```bash
python manage.py bootstrap_owner --email owner@markrowecontest.com
# prompts for a new password
```

The command refuses if an owner already exists. If the existing row is
corrupted beyond use, open a Django shell
(`python manage.py shell`) and delete it directly first:

```python
from django.contrib.auth import get_user_model
User = get_user_model()
User.objects.filter(is_superuser=True).delete()
```

Then re-run `bootstrap_owner`. Write an audit log entry explaining the
action via the shell:

```python
from books.audit.models import AuditLog
AuditLog.record(
    entity_type="User", entity_id="", action="owner_replaced",
    reason="<why>",
)
```

---

## 7. Lost the backup encryption key (`age` private key)

This isn't a lockout from the live system, but it **destroys your
off-site backups**. The `age` public key is used for encrypting
backups before they ship to Backblaze B2; the matching private key is
the only thing that can decrypt them.

- **Prevention:** Keep two copies of the private key in separate
  locations (safe + encrypted USB stick). Do not rely on a single
  password manager if that password manager is also protected by the
  same email that gates everything else.
- **Mitigation if lost:** Rotate. Generate a new `age` keypair, update
  `BACKUP_AGE_RECIPIENT` in Render env vars, and discard the
  unreadable backups. Going forward, new backups use the new key.
  Old backups are permanently unreadable — you're relying on the live
  database from that point until enough new daily backups accumulate.
- Document the rotation in the audit log:
  ```bash
  python manage.py shell -c "from books.audit.models import AuditLog; \
      AuditLog.record(entity_type='backup', action='age_key_rotated', \
      reason='<why>')"
  ```

---

## 8. Render account compromise

Out of scope for this runbook — this is a provider-account-recovery
problem, not a Family Books problem. Render's own account recovery
(2FA on your Render account, device verification) is your control
plane. The encrypted off-site backup is your data escape hatch: with
the `age` private key you can restore to any Postgres you can provision
elsewhere.

---

## Audit-log discoverability

Every management command in this runbook writes an `AuditLog` row
(see `books.audit.models.AuditLog`). After any recovery action, verify
the row appeared:

```bash
python manage.py shell -c "from books.audit.models import AuditLog; \
    print(AuditLog.objects.order_by('-timestamp').values('timestamp', 'action', 'reason')[:5])"
```

If you performed recovery under duress (suspected breach), the audit
log is also your forensic record of what you did and when.
