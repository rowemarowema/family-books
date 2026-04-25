"""Group H commit 1 — extend AuditAction with backup/restore/drill actions."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("audit", "0003_auditlog_action_choices_2"),
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
                    ("backup_created", "Backup created"),
                    ("backup_restored", "Backup restored"),
                    ("backup_drill_passed", "Backup drill passed"),
                    ("backup_drill_failed", "Backup drill failed"),
                ],
                db_index=True,
                max_length=64,
            ),
        ),
    ]
