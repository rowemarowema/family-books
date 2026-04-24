"""Tests for bootstrap_owner management command + single-user enforcement."""
from __future__ import annotations

from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError

from books.audit.models import AuditLog


@pytest.mark.django_db
def test_bootstrap_creates_owner_and_audits():
    out = StringIO()
    call_command(
        "bootstrap_owner",
        "--email", "mark@example.com",
        "--password-stdin",
        stdin=StringIO("strong-password-77!!"),
        stdout=out,
    )

    User = get_user_model()
    user = User.objects.get(email="mark@example.com")
    assert user.is_superuser is True
    assert user.check_password("strong-password-77!!")
    assert AuditLog.objects.filter(action="bootstrap_owner", entity_id=str(user.pk)).exists()
    assert "Created owner" in out.getvalue()


@pytest.mark.django_db
def test_bootstrap_same_email_is_noop():
    call_command(
        "bootstrap_owner",
        "--email", "mark@example.com",
        "--password-stdin",
        stdin=StringIO("strong-password-77!!"),
    )
    out = StringIO()
    call_command(
        "bootstrap_owner",
        "--email", "mark@example.com",
        "--password-stdin",
        stdin=StringIO("whatever"),
        stdout=out,
    )
    User = get_user_model()
    assert User.objects.filter(is_superuser=True).count() == 1
    assert "already exists" in out.getvalue()


@pytest.mark.django_db
def test_bootstrap_refuses_second_owner_with_different_email():
    call_command(
        "bootstrap_owner",
        "--email", "mark@example.com",
        "--password-stdin",
        stdin=StringIO("strong-password-77!!"),
    )
    with pytest.raises(CommandError, match="single-user system"):
        call_command(
            "bootstrap_owner",
            "--email", "someone-else@example.com",
            "--password-stdin",
            stdin=StringIO("another-password-99!!"),
        )


@pytest.mark.django_db
def test_bootstrap_rejects_weak_password():
    with pytest.raises(CommandError, match="Password rejected"):
        call_command(
            "bootstrap_owner",
            "--email", "mark@example.com",
            "--password-stdin",
            stdin=StringIO("short"),
        )


@pytest.mark.django_db
def test_signal_refuses_second_user_via_orm(owner):
    User = get_user_model()
    with pytest.raises(ValidationError, match="single-user system"):
        User.objects.create_user(
            username="another@example.com",
            email="another@example.com",
            password="another-strong-pass-88!",
        )
