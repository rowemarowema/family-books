"""django-axes lockout -> AuditLog wiring."""
from __future__ import annotations

import pytest
from django.test import RequestFactory

from books.audit.models import AuditLog
from books.core.signals import _on_axes_user_locked_out, _on_login_failed


@pytest.mark.django_db
def test_login_failed_signal_writes_audit_row():
    factory = RequestFactory()
    request = factory.post("/account/login/", REMOTE_ADDR="198.51.100.10")
    _on_login_failed(
        credentials={"username": "attacker@example.com"},
        request=request,
    )
    audit = AuditLog.objects.get(action="auth_login_failed")
    assert audit.entity_id == "attacker@example.com"
    assert audit.ip_address == "198.51.100.10"


@pytest.mark.django_db
def test_axes_lockout_signal_writes_audit_row():
    factory = RequestFactory()
    request = factory.post("/account/login/", REMOTE_ADDR="198.51.100.10")
    _on_axes_user_locked_out(request=request, username="attacker@example.com")
    audit = AuditLog.objects.get(action="auth_lockout")
    assert audit.entity_id == "attacker@example.com"
    assert audit.ip_address == "198.51.100.10"


@pytest.mark.django_db
def test_login_failed_handles_missing_request_gracefully():
    # axes sometimes calls without a request object.
    _on_login_failed(credentials={"username": "x@example.com"})
    assert AuditLog.objects.filter(action="auth_login_failed").exists()
