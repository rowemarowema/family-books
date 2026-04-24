"""SessionAbsoluteTimeoutMiddleware — 12-hour absolute cap."""
from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from books.core.middleware import SessionAbsoluteTimeoutMiddleware


@pytest.mark.django_db
def test_first_request_stamps_session_start(owner):
    client = Client()
    client.force_login(owner)
    client.get("/health")
    session = client.session
    assert SessionAbsoluteTimeoutMiddleware.SESSION_START_KEY in session


@pytest.mark.django_db
def test_request_within_cap_is_allowed(owner):
    client = Client()
    client.force_login(owner)
    # Set the session start to 1 hour ago; cap is 12h, so still valid.
    session = client.session
    session[SessionAbsoluteTimeoutMiddleware.SESSION_START_KEY] = (
        (timezone.now() - timedelta(hours=1)).isoformat()
    )
    session.save()

    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.content == b"ok"


@pytest.mark.django_db
def test_request_after_cap_logs_out_and_redirects(owner, settings):
    client = Client()
    client.force_login(owner)
    session = client.session
    # Pretend the session started 13 hours ago (past the 12h cap).
    session[SessionAbsoluteTimeoutMiddleware.SESSION_START_KEY] = (
        (timezone.now() - timedelta(hours=13)).isoformat()
    )
    session.save()

    resp = client.get("/health", follow=False)
    assert resp.status_code == 302
    assert resp.url.startswith(settings.LOGIN_URL) or "login" in resp.url.lower()
    # User should be anonymous on the next request.
    resp2 = client.get("/health")
    assert "_auth_user_id" not in client.session
