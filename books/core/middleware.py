"""
Custom middleware:

1. SessionAbsoluteTimeoutMiddleware — hard 12-hour session cap regardless of
   activity. Django's SESSION_COOKIE_AGE + SESSION_SAVE_EVERY_REQUEST gives
   idle timeout; this middleware adds the absolute cap (decision #4).

2. TwoFactorEnforcementMiddleware — implements the sticky 2FA gate
   (decision #22). Once enforcement is on in SystemFlag, it stays on until
   the disable_2fa_enforcement management command is run. Even then, the
   grace window is time-boxed and auto-re-enables.
"""
from __future__ import annotations

import logging
from typing import Callable

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone

logger = logging.getLogger(__name__)

# URL prefixes that must remain reachable even when 2FA is required but the
# user hasn't yet enrolled or verified. Without this, the middleware would
# redirect-loop the setup page into itself.
_TWO_FACTOR_ALLOWLIST_PREFIXES = (
    "/account/",
    "/static/",
    "/health",
)


class SessionAbsoluteTimeoutMiddleware:
    """Enforce SESSION_ABSOLUTE_TIMEOUT_SECONDS (12h cap) on top of idle timeout."""

    SESSION_START_KEY = "_session_started_at"

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response
        self.absolute_timeout = getattr(settings, "SESSION_ABSOLUTE_TIMEOUT_SECONDS", 12 * 60 * 60)

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if request.user.is_authenticated:
            started_iso = request.session.get(self.SESSION_START_KEY)
            if started_iso is None:
                request.session[self.SESSION_START_KEY] = timezone.now().isoformat()
            else:
                try:
                    started = timezone.datetime.fromisoformat(started_iso)
                except (TypeError, ValueError):
                    started = timezone.now()
                age = (timezone.now() - started).total_seconds()
                if age >= self.absolute_timeout:
                    logger.info("Session absolute timeout hit; logging user out.", extra={
                        "user_id": request.user.pk,
                        "session_age_seconds": age,
                    })
                    logout(request)
                    messages.info(
                        request,
                        "Your session has ended after the 12-hour absolute cap. Please sign in again.",
                    )
                    return redirect(settings.LOGIN_URL)
        return self.get_response(request)


class TwoFactorEnforcementMiddleware:
    """
    Enforce 2FA per decision #10 and #22.

    Logic on every request:
      1. If REQUIRE_2FA=True, ensure SystemFlag.two_factor_enforcement_active is
         True. This is the sticky-on latch.
      2. If SystemFlag is in a grace window (locked_until set, in the past),
         auto-re-enable: flip active=True, clear locked_until, audit-log.
      3. If enforcement is active and the user is authenticated but not OTP-
         verified, redirect to 2FA setup/verify (allowlisted routes pass).
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        # Local imports keep this module importable before migrations run.
        from books.audit.models import AuditAction, AuditLog
        from books.core.models import SystemFlag

        flag = SystemFlag.get()

        # (1) Sticky-on latch.
        if settings.REQUIRE_2FA and not flag.two_factor_enforcement_active and not flag.in_enforcement_grace_window:
            flag.activate_enforcement()
            AuditLog.record(
                entity_type="SystemFlag",
                entity_id=flag.pk,
                action=AuditAction.TWO_FACTOR_ENFORCEMENT_ACTIVATED,
                user=request.user if request.user.is_authenticated else None,
                reason="REQUIRE_2FA=True observed; activating sticky enforcement.",
                ip_address=_client_ip(request),
            )

        # (2) Grace window expired -> auto re-enable.
        if (
            not flag.two_factor_enforcement_active
            and flag.enforcement_locked_until is not None
            and timezone.now() >= flag.enforcement_locked_until
        ):
            flag.activate_enforcement()
            AuditLog.record(
                entity_type="SystemFlag",
                entity_id=flag.pk,
                action=AuditAction.TWO_FACTOR_ENFORCEMENT_AUTO_RE_ENABLED,
                reason="24-hour grace window expired.",
                ip_address=_client_ip(request),
            )

        # (3) Enforcement.
        if flag.two_factor_enforcement_active and request.user.is_authenticated:
            if not _request_path_is_allowlisted(request.path) and not _user_is_otp_verified(request.user):
                return redirect(reverse("two_factor:setup"))

        return self.get_response(request)


def _user_is_otp_verified(user: object) -> bool:
    """True if django-otp has attached a verified device to this user."""
    is_verified = getattr(user, "is_verified", None)
    return bool(is_verified()) if callable(is_verified) else False


def _request_path_is_allowlisted(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in _TWO_FACTOR_ALLOWLIST_PREFIXES)


def _client_ip(request: HttpRequest) -> str | None:
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")
