"""Apply AuditAction TextChoices to AuditLog.action.

Adding `choices=...` to a CharField is a model-state change (Django records
it in ProjectState even though it does not alter the column type or add a
DB CHECK). We emit an AlterField so future `makemigrations` runs come up
clean. Form-layer validation gains the choice list automatically; the
drift test in `tests/integration/test_audit_action_enum.py` is the
in-DB enforcement.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("audit", "0001_initial"),
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
                ],
                db_index=True,
                max_length=64,
            ),
        ),
    ]
