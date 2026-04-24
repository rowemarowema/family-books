"""reset_owner_2fa management command."""
from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from books.audit.models import AuditLog


@pytest.mark.django_db
def test_reset_requires_confirm_flag():
    with pytest.raises(CommandError):
        call_command("reset_owner_2fa")


@pytest.mark.django_db
def test_reset_requires_non_empty_reason(owner):
    with pytest.raises(CommandError, match="non-empty reason"):
        call_command("reset_owner_2fa", "--confirm-reset", "   ")


@pytest.mark.django_db
def test_reset_errors_when_no_owner_exists(db):
    with pytest.raises(CommandError, match="No owner user"):
        call_command("reset_owner_2fa", "--confirm-reset", "test")


@pytest.mark.django_db
def test_reset_deletes_totp_and_static_devices_and_audits(owner):
    from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
    from django_otp.plugins.otp_totp.models import TOTPDevice

    TOTPDevice.objects.create(user=owner, name="phone", confirmed=True)
    static = StaticDevice.objects.create(user=owner, name="recovery")
    StaticToken.objects.create(device=static, token="abcdef")

    out = StringIO()
    call_command(
        "reset_owner_2fa",
        "--confirm-reset", "lost authenticator app + recovery sheet",
        stdout=out,
    )

    assert TOTPDevice.objects.filter(user=owner).count() == 0
    assert StaticDevice.objects.filter(user=owner).count() == 0
    assert "Cleared 1 TOTP + 1 static devices" in out.getvalue()

    audit = AuditLog.objects.get(action="reset_2fa_devices")
    assert audit.before_value == {"totp_devices": 1, "static_devices": 1}
    assert audit.after_value == {"totp_devices": 0, "static_devices": 0}
    assert "lost authenticator" in audit.reason


@pytest.mark.django_db
def test_reset_is_idempotent_when_no_devices_exist(owner):
    from django_otp.plugins.otp_totp.models import TOTPDevice

    assert TOTPDevice.objects.filter(user=owner).count() == 0
    call_command("reset_owner_2fa", "--confirm-reset", "test")
    # Second run should not fail even though there's nothing to delete.
    call_command("reset_owner_2fa", "--confirm-reset", "test again")
    assert AuditLog.objects.filter(action="reset_2fa_devices").count() == 2
