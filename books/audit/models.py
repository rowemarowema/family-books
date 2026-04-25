"""
AuditLog: append-only record of security- and financial-material events.

Every change to posted accounting data, every auth event worth remembering
(login success/failure, lockout, 2FA state change, owner bootstrap), and every
deliberate ops action (period close/reopen, backup/restore) writes a row here.
Rows are never updated or deleted in code; only the Django admin (owner-only,
2FA-gated) can touch them, and even that is read-only by default.

`action` values are constrained to the AuditAction enum below. New audit
events MUST add a member here before being written; the audit-action drift
test (`tests/integration/test_audit_action_enum.py`) trips if any row in
the DB has an action value not represented in the enum.
"""
from __future__ import annotations

from typing import Any

from django.conf import settings
from django.db import models


class AuditAction(models.TextChoices):
    """Controlled vocabulary for AuditLog.action.

    Members are grouped by origin (auth, accounting, COA). Adding a new
    audit-emitting code path requires adding a member here and updating
    the call site to pass `AuditAction.<NAME>` (string-equivalent via
    StrEnum) instead of a string literal.
    """

    # --- Auth / Group C ---
    BOOTSTRAP_OWNER = "bootstrap_owner", "Bootstrap owner"
    AUTH_LOGIN_FAILED = "auth_login_failed", "Auth login failed"
    AUTH_LOCKOUT = "auth_lockout", "Auth lockout"
    TWO_FACTOR_ENFORCEMENT_ACTIVATED = (
        "two_factor_enforcement_activated",
        "2FA enforcement activated",
    )
    TWO_FACTOR_ENFORCEMENT_DISABLED = (
        "two_factor_enforcement_disabled",
        "2FA enforcement disabled",
    )
    TWO_FACTOR_ENFORCEMENT_AUTO_RE_ENABLED = (
        "two_factor_enforcement_auto_re_enabled",
        "2FA enforcement auto re-enabled",
    )
    RESET_2FA_DEVICES = "reset_2fa_devices", "Reset 2FA devices"

    # --- Accounting / Group D ---
    POST_ENTRY = "post_entry", "Post journal entry"
    REVERSE_ENTRY = "reverse_entry", "Reverse journal entry"

    # --- COA / Group E ---
    COA_SEEDED = "coa_seeded", "COA seeded"
    COA_SEED_REFUSED = "coa_seed_refused", "COA seed refused"
    COA_RESET = "coa_reset", "COA reset"
    COA_RESET_REFUSED = "coa_reset_refused", "COA reset refused"

    # --- Opening balances / Group F ---
    OPENING_BALANCE_SET = "opening_balance_set", "Opening balance set"
    OPENING_BALANCE_REFUSED = (
        "opening_balance_refused",
        "Opening balance refused",
    )


class AuditLog(models.Model):
    entity_type = models.CharField(max_length=64, db_index=True)
    entity_id = models.CharField(max_length=64, db_index=True, blank=True)
    action = models.CharField(
        max_length=64,
        db_index=True,
        choices=AuditAction.choices,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_entries",
    )
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    before_value = models.JSONField(null=True, blank=True)
    after_value = models.JSONField(null=True, blank=True)
    reason = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        db_table = "audit_log"
        ordering = ["-timestamp"]
        # Names pinned to migration-defined identifiers; see Account
        # for the full rationale.
        indexes = [
            models.Index(
                fields=["entity_type", "entity_id"],
                name="audit_log_entity_t_idx",
            ),
            models.Index(
                fields=["action", "-timestamp"],
                name="audit_log_action_ts_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.timestamp:%Y-%m-%d %H:%M} {self.action} {self.entity_type}#{self.entity_id}"

    @classmethod
    def record(
        cls,
        *,
        entity_type: str,
        entity_id: str | int = "",
        action: str | AuditAction,
        user: Any = None,
        before: Any = None,
        after: Any = None,
        reason: str = "",
        ip_address: str | None = None,
    ) -> "AuditLog":
        """
        Append an audit entry. Always use this helper; never instantiate
        AuditLog() directly, because downstream code assumes rows are
        append-only and `record()` is the single audit funnel.

        `action` accepts either an `AuditAction` member (preferred) or its
        raw string value (legacy / migration period). The drift test will
        flag any value not in the enum, so string literals stay limited to
        the same vocabulary.
        """
        # AuditAction is a StrEnum; str() coerces members to their value.
        action_value = str(action) if isinstance(action, AuditAction) else action
        return cls.objects.create(
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id != "" else "",
            action=action_value,
            user=user if (user is not None and getattr(user, "pk", None) is not None) else None,
            before_value=before,
            after_value=after,
            reason=reason,
            ip_address=ip_address,
        )
