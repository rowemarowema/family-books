"""FamilyBooksAdminSite gate: owner-only, 2FA-gated when enforcement is on."""
from __future__ import annotations

import pytest
from django.test import Client, RequestFactory

from books.core.admin_site import admin_site
from books.core.models import SystemFlag


@pytest.mark.django_db
def test_anonymous_user_has_no_admin_permission():
    factory = RequestFactory()
    request = factory.get("/admin/")
    from django.contrib.auth.models import AnonymousUser
    request.user = AnonymousUser()
    assert admin_site.has_permission(request) is False


@pytest.mark.django_db
def test_owner_has_admin_permission_with_2fa_off(owner):
    factory = RequestFactory()
    request = factory.get("/admin/")
    request.user = owner
    assert admin_site.has_permission(request) is True


@pytest.mark.django_db
def test_owner_blocked_from_admin_when_2fa_on_and_unverified(owner):
    flag = SystemFlag.get()
    flag.activate_enforcement()

    factory = RequestFactory()
    request = factory.get("/admin/")
    request.user = owner
    # Default user object has no is_verified() method until OTPMiddleware adds it.
    # Without it, the admin gate treats them as unverified.
    assert admin_site.has_permission(request) is False


@pytest.mark.django_db
def test_owner_reaches_admin_when_2fa_on_and_verified(owner):
    flag = SystemFlag.get()
    flag.activate_enforcement()

    factory = RequestFactory()
    request = factory.get("/admin/")
    request.user = owner
    # Simulate OTPMiddleware having attached a verified device.
    request.user.is_verified = lambda: True  # type: ignore[method-assign]
    assert admin_site.has_permission(request) is True


@pytest.mark.django_db
def test_admin_url_requires_login(owner):
    client = Client()
    resp = client.get("/admin/", follow=False)
    assert resp.status_code in (302, 403)
