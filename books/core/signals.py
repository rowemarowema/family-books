"""
Signal handlers for single-user enforcement and axes audit integration.

Connected in books.core.apps.CoreConfig.ready().
"""
from __future__ import annotations

import logging
from typing import Any

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models.signals import pre_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)


@receiver(pre_save)
def refuse_second_user(sender: Any, instance: Any, **kwargs: Any) -> None:
    """
    Single-user system: the User table holds exactly one row (the owner).

    Dispatched on pre_save for any model because Django's custom-user pattern
    means the User model is only discoverable via get_user_model(). We check
    sender identity inside the handler instead of at decorator time.
    """
    User = get_user_model()
    if sender is not User:
        return
    if instance.pk is not None:
        return  # existing user being updated — fine
    if User.objects.exists():
        raise ValidationError(
            "This is a single-user system. An owner already exists; "
            "refusing to create a second user."
        )


def _on_axes_lockout(*, request: Any, credentials: dict[str, Any] | None = None, **_: Any) -> None:
    """Log django-axes lockouts to the audit trail."""
    from books.audit.models import AuditAction, AuditLog

    username = (credentials or {}).get("username", "")
    AuditLog.record(
        entity_type="User",
        entity_id=username,
        action=AuditAction.AUTH_LOCKOUT,
        reason="django-axes failure threshold hit; account locked.",
        ip_address=_req_ip(request),
    )


def _on_axes_user_locked_out(*, request: Any, username: str | None = None, **_: Any) -> None:
    """Alternate axes signal name used in newer releases."""
    from books.audit.models import AuditAction, AuditLog

    AuditLog.record(
        entity_type="User",
        entity_id=username or "",
        action=AuditAction.AUTH_LOCKOUT,
        reason="django-axes failure threshold hit; account locked.",
        ip_address=_req_ip(request),
    )


def _on_login_failed(*, credentials: dict[str, Any] | None = None, request: Any = None, **_: Any) -> None:
    """Log every failed login attempt (axes handles lockout; we handle the audit row)."""
    from books.audit.models import AuditAction, AuditLog

    username = (credentials or {}).get("username", "")
    AuditLog.record(
        entity_type="User",
        entity_id=username,
        action=AuditAction.AUTH_LOGIN_FAILED,
        ip_address=_req_ip(request) if request is not None else None,
    )


def _req_ip(request: Any) -> str | None:
    if request is None:
        return None
    meta = getattr(request, "META", {}) or {}
    xff = meta.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return meta.get("REMOTE_ADDR")


def connect_auth_signals() -> None:
    """Wire axes and auth signals. Called from apps.ready()."""
    from django.contrib.auth.signals import user_login_failed

    user_login_failed.connect(_on_login_failed, dispatch_uid="books.core.login_failed")

    # django-axes exposes different signals across versions; connect whichever are present.
    try:
        from axes.signals import user_locked_out
        user_locked_out.connect(_on_axes_user_locked_out, dispatch_uid="books.core.axes_user_locked_out")
    except ImportError:
        pass
    try:
        from axes.signals import user_locked
        user_locked.connect(_on_axes_lockout, dispatch_uid="books.core.axes_user_locked")
    except ImportError:
        pass
