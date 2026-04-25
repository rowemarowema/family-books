"""
Reusable view-level auth helpers for owner-only HTTP views.

Mirrors the gate logic baked into FamilyBooksAdminSite.has_permission()
so the trial-balance view (and future report / grid views) can apply
the same access policy without duplicating the SystemFlag check.

Intentional choice: 403, not 302-to-login. Anonymous or non-owner
access to a report URL is a misuse, not a redirect-worthy event. The
login flow lives at /account/login/ via two_factor's LoginView; users
who reach a protected URL unauthenticated should hit a hard wall, not
a redirect that masks the problem.
"""
from __future__ import annotations

from collections.abc import Callable
from functools import wraps

from django.http import HttpRequest, HttpResponse, HttpResponseForbidden


def owner_only_with_2fa(view_func: Callable) -> Callable:
    """View decorator: passes through to view_func only if:

      1. request.user is authenticated AND is_active AND is_superuser, AND
      2. when SystemFlag.two_factor_enforcement_active is True,
         request.user has a verified django-otp device on the request.

    Otherwise returns HttpResponseForbidden(403).

    The 2FA gate is intentionally permissive when enforcement is off
    (post-bootstrap, pre-2FA-setup window): the owner must be able to
    reach the admin / reports with password alone before they enroll
    a TOTP device. Once SystemFlag.two_factor_enforcement_active flips
    to True (sticky-on; see books.core.middleware), the gate becomes
    strict.
    """

    @wraps(view_func)
    def wrapper(request: HttpRequest, *args, **kwargs) -> HttpResponse:
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return HttpResponseForbidden("Authentication required.")
        if not user.is_active or not user.is_superuser:
            return HttpResponseForbidden("Owner-only resource.")

        # Late import to avoid app-loading-order issues during startup.
        from books.core.models import SystemFlag

        flag = SystemFlag.get()
        if flag.two_factor_enforcement_active:
            is_verified = getattr(user, "is_verified", None)
            if not (callable(is_verified) and is_verified()):
                return HttpResponseForbidden(
                    "Owner-only resource. 2FA verification required."
                )
        return view_func(request, *args, **kwargs)

    return wrapper
