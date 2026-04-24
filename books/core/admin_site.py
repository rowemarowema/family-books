"""
Custom AdminSite: owner-only access with a 2FA gate that respects SystemFlag.

Design (decision #19):
  - Admin is enabled but only the single owner (superuser) can enter.
  - When SystemFlag.two_factor_enforcement_active is True, the admin additionally
    requires django-otp to have attached a verified device to the request.
  - In early dev with 2FA enforcement off, the owner can reach admin with
    password alone. The session still times out per SESSION_COOKIE_AGE.
"""
from __future__ import annotations

from django.contrib.admin import AdminSite
from django.http import HttpRequest


class FamilyBooksAdminSite(AdminSite):
    site_title = "Family Books"
    site_header = "Family Books"
    index_title = "Admin"

    def has_permission(self, request: HttpRequest) -> bool:
        # Super's has_permission already checks is_active + is_staff.
        if not super().has_permission(request):
            return False
        if not request.user.is_superuser:
            return False
        # Gate on 2FA enforcement state. Import here to dodge app-loading
        # ordering during startup.
        from books.core.models import SystemFlag

        flag = SystemFlag.get()
        if flag.two_factor_enforcement_active:
            is_verified = getattr(request.user, "is_verified", None)
            return bool(is_verified()) if callable(is_verified) else False
        return True


admin_site = FamilyBooksAdminSite(name="admin")
