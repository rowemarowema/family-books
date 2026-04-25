"""Tests for `@owner_only_with_2fa` view decorator.

Mirrors test_admin_owner_only.py for the AdminSite gate, but on a plain
function-based view. Five cases:

  1. Anonymous user                                              -> 403
  2. Authenticated non-owner                                     -> 403
  3. Owner + 2FA enforcement OFF, no verified OTP device         -> 200
     (post-bootstrap, pre-2FA-setup window — refinement #1)
  4. Owner + 2FA enforcement ON, no verified OTP                 -> 403
  5. Owner + 2FA enforcement ON, verified OTP                    -> 200
"""
from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse
from django.test import RequestFactory

from books.core.auth import owner_only_with_2fa
from books.core.models import SystemFlag


@owner_only_with_2fa
def _stub_view(request):
    return HttpResponse("ok")


@pytest.fixture
def rf():
    return RequestFactory()


@pytest.mark.django_db
def test_anonymous_user_gets_403(rf):
    request = rf.get("/anywhere/")
    request.user = AnonymousUser()
    response = _stub_view(request)
    assert response.status_code == 403


@pytest.mark.django_db
def test_non_owner_authenticated_user_gets_403(rf):
    """A standard user (not superuser) cannot reach owner-only views.

    The single-user enforcement (refuse_second_user signal) means
    User #2 can't normally exist, but factory_boy can still build a
    pre-existing-row scenario. Either way, is_superuser=False is the
    gate."""
    from django.contrib.auth import get_user_model

    User = get_user_model()
    user = User.objects.create_user(
        username="not-owner@example.com",
        email="not-owner@example.com",
        password="x" * 20,
    )
    request = rf.get("/anywhere/")
    request.user = user
    response = _stub_view(request)
    assert response.status_code == 403


@pytest.mark.django_db
def test_owner_with_2fa_enforcement_off_and_no_otp_gets_200(owner, rf):
    """Refinement #1 (post-bootstrap, pre-2FA-setup window).

    After bootstrap_owner runs, the owner exists but has no TOTP device
    yet. SystemFlag.two_factor_enforcement_active is still False at
    this stage. The decorator MUST allow the owner through so they can
    reach the admin / reports and enroll their first device. Once
    REQUIRE_2FA flips to True, the middleware activates enforcement
    (sticky-on); after that, this same owner without a verified device
    would get 403.
    """
    flag = SystemFlag.get()
    assert flag.two_factor_enforcement_active is False

    request = rf.get("/anywhere/")
    request.user = owner
    # No is_verified attribute — django-otp's middleware hasn't run.
    assert not hasattr(request.user, "is_verified") or not callable(
        getattr(request.user, "is_verified", None)
    )
    response = _stub_view(request)
    assert response.status_code == 200
    assert response.content == b"ok"


@pytest.mark.django_db
def test_owner_with_2fa_enforcement_on_and_unverified_gets_403(owner, rf):
    flag = SystemFlag.get()
    flag.activate_enforcement()

    request = rf.get("/anywhere/")
    request.user = owner
    # No is_verified() / returns False
    response = _stub_view(request)
    assert response.status_code == 403


@pytest.mark.django_db
def test_owner_with_2fa_enforcement_on_and_verified_gets_200(owner, rf):
    flag = SystemFlag.get()
    flag.activate_enforcement()

    request = rf.get("/anywhere/")
    request.user = owner
    request.user.is_verified = lambda: True  # type: ignore[method-assign]
    response = _stub_view(request)
    assert response.status_code == 200
