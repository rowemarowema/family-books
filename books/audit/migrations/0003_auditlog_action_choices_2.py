"""Group F commit 1 — extend AuditAction TextChoices.

Adds OPENING_BALANCE_SET and OPENING_BALANCE_REFUSED to the audit-action
vocabulary. Pure model-state AlterField (same shape as Group E's
0002_auditlog_action_choices); no DB column change.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("audit", "0002_auditlog_action_choices"),
    ]

    operations = [
        migrations.AlterField(
            model_name="auditlog",
            name="action",
            field=models.CharField(
                choices=[
                    ("bootstrap_owner", "Bootstrap owner"),
                    ("auth_login_failed", "Auth login failed"),
                    ("auth_lockout", "Auth lockout"),
                    (
                        "two_factor_enforcement_activated",
                        "2FA enforcement activated",
                    ),
                    (
                        "two_factor_enforcement_disabled",
                        "2FA enforcement disabled",
                    ),
                    (
                        "two_factor_enforcement_auto_re_enabled",
                        "2FA enforcement auto re-enabled",
                    ),
                    ("reset_2fa_devices", "Reset 2FA devices"),
                    ("post_entry", "Post journal entry"),
                    ("reverse_entry", "Reverse journal entry"),
                    ("coa_seeded", "COA seeded"),
                    ("coa_seed_refused", "COA seed refused"),
                    ("coa_reset", "COA reset"),
                    ("coa_reset_refused", "COA reset refused"),
                    ("opening_balance_set", "Opening balance set"),
                    ("opening_balance_refused", "Opening balance refused"),
                ],
                db_index=True,
                max_length=64,
            ),
        ),
    ]
