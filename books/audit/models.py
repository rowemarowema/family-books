"""
AuditLog: append-only record of security- and financial-material events.

Every change to posted accounting data, every auth event worth remembering
(login success/failure, lockout, 2FA state change, owner bootstrap), and every
deliberate ops action (period close/reopen, backup/restore) writes a row here.
Rows are never updated or deleted in code; only the Django admin (owner-only,
2FA-gated) can touch them, and even that is read-only by default.
"""
from __future__ import annotations

from typing import Any

from django.conf import settings
from django.db import models


class AuditLog(models.Model):
    entity_type = models.CharField(max_length=64, db_index=True)
    entity_id = models.CharField(max_length=64, db_index=True, blank=True)
    action = models.CharField(max_length=64, db_index=True)
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
        indexes = [
            models.Index(fields=["entity_type", "entity_id"]),
            models.Index(fields=["action", "-timestamp"]),
        ]

    def __str__(self) -> str:
        return f"{self.timestamp:%Y-%m-%d %H:%M} {self.action} {self.entity_type}#{self.entity_id}"

    @classmethod
    def record(
        cls,
        *,
        entity_type: str,
        entity_id: str | int = "",
        action: str,
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
        """
        return cls.objects.create(
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id != "" else "",
            action=action,
            user=user if (user is not None and getattr(user, "pk", None) is not None) else None,
            before_value=before,
            after_value=after,
            reason=reason,
            ip_address=ip_address,
        )
