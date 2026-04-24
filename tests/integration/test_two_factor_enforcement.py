"""
End-to-end tests for the 2FA enforcement hardening (decision #22).

Covers:
  - REQUIRE_2FA=True trips SystemFlag sticky on first request.
  - REQUIRE_2FA=False alone cannot un-trip the sticky flag.
  - disable_2fa_enforcement command flips the flag but only for 24h.
  - After 24h, middleware auto re-enables and writes an audit row.
  - Command requires --confirm-disable with a non-empty reason.
"""
from __future__ import annotations

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client
from django.test.utils import override_settings
from django.utils import timezone

from books.audit.models import AuditLog
from books.core.models import SystemFlag


@pytest.mark.django_db
def test_require_2fa_env_var_flips_system_flag_on(owner):
    client = Client()
    client.force_login(owner)
    flag = SystemFlag.get()
    assert flag.two_factor_enforcement_active is False

    with override_settings(REQUIRE_2FA=True):
        client.get("/health")

    flag.refresh_from_db()
    assert flag.two_factor_enforcement_active is True
    assert AuditLog.objects.filter(action="two_factor_enforcement_activated").exists()


@pytest.mark.django_db
def test_env_var_false_cannot_un_trip_sticky_flag(owner):
    client = Client()
    client.force_login(owner)

    with override_settings(REQUIRE_2FA=True):
        client.get("/health")
    SystemFlag.get().refresh_from_db()

    # Now env var flips back off. Flag should stay sticky-on.
    with override_settings(REQUIRE_2FA=False):
        client.get("/health")

    flag = SystemFlag.get()
    assert flag.two_factor_enforcement_active is True, (
        "Env var alone must not re-disable enforcement; only the "
        "disable_2fa_enforcement command can."
    )


@pytest.mark.django_db
def test_disable_command_requires_confirm_disable_flag():
    with pytest.raises(CommandError):
        call_command("disable_2fa_enforcement")


@pytest.mark.django_db
def test_disable_command_with_empty_reason_is_rejected():
    flag = SystemFlag.get()
    flag.activate_enforcement()
    with pytest.raises(CommandError, match="non-empty reason"):
        call_command("disable_2fa_enforcement", "--confirm-disable", "   ")


@pytest.mark.django_db
def test_disable_command_flips_flag_and_starts_grace_window():
    flag = SystemFlag.get()
    flag.activate_enforcement()

    out = StringIO()
    call_command(
        "disable_2fa_enforcement",
        "--confirm-disable",
        "hardware token lost; rotating",
        stdout=out,
    )

    flag.refresh_from_db()
    assert flag.two_factor_enforcement_active is False
    assert flag.enforcement_locked_until is not None
    assert flag.enforcement_locked_until > timezone.now()
    assert flag.enforcement_locked_until <= timezone.now() + timedelta(hours=24, seconds=5)
    assert "auto-re-enable" in out.getvalue()

    audit = AuditLog.objects.get(action="two_factor_enforcement_disabled")
    assert "hardware token lost" in audit.reason


@pytest.mark.django_db
def test_middleware_auto_re_enables_after_grace_window_expires(owner):
    flag = SystemFlag.get()
    flag.activate_enforcement()
    flag.grant_enforcement_grace(hours=1)
    # Rewind the window so it's already expired.
    flag.enforcement_locked_until = timezone.now() - timedelta(minutes=1)
    flag.save()

    client = Client()
    client.force_login(owner)
    client.get("/health")

    flag.refresh_from_db()
    assert flag.two_factor_enforcement_active is True
    assert flag.enforcement_locked_until is None
    assert AuditLog.objects.filter(action="two_factor_enforcement_auto_re_enabled").exists()


@pytest.mark.django_db
def test_grace_window_blocks_auto_re_enable_until_it_expires(owner):
    flag = SystemFlag.get()
    flag.activate_enforcement()
    flag.grant_enforcement_grace(hours=1)

    client = Client()
    client.force_login(owner)
    client.get("/health")

    flag.refresh_from_db()
    assert flag.two_factor_enforcement_active is False, (
        "Middleware must not re-enable while the grace window is still open."
    )
    assert AuditLog.objects.filter(action="two_factor_enforcement_auto_re_enabled").count() == 0


@pytest.mark.django_db
def test_disable_command_clamps_hours_to_24():
    flag = SystemFlag.get()
    flag.activate_enforcement()
    call_command(
        "disable_2fa_enforcement",
        "--confirm-disable", "test clamp",
        "--hours", "1000",
    )
    flag.refresh_from_db()
    assert flag.enforcement_locked_until is not None
    assert flag.enforcement_locked_until <= timezone.now() + timedelta(hours=24, seconds=5)
