"""
Delete all 2FA devices (TOTP + static recovery) for the owner.

Use case: Mark has lost both his authenticator app and his printed recovery
codes. After running this command, the next successful password login will
redirect him to the 2FA setup page (the /account/ allowlist in
TwoFactorEnforcementMiddleware ensures the redirect doesn't loop).

This is recovery code, not routine maintenance. It writes an audit log row
containing the reason and the count of devices that were purged.

Usage:
    ./manage.py reset_owner_2fa --confirm-reset "<reason>"

See docs/RECOVERY.md for the full playbook.
"""
from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Delete all 2FA devices for the owner so they can re-enroll."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--confirm-reset",
            required=True,
            help="Explicit acknowledgment + reason; written verbatim to the audit log.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from django_otp.plugins.otp_static.models import StaticDevice
        from django_otp.plugins.otp_totp.models import TOTPDevice

        from books.audit.models import AuditLog

        reason = options["confirm_reset"].strip()
        if not reason:
            raise CommandError("--confirm-reset must be a non-empty reason.")

        User = get_user_model()
        owner = User.objects.filter(is_superuser=True).first()
        if owner is None:
            raise CommandError("No owner user found. Run bootstrap_owner first.")

        totp_count = TOTPDevice.objects.filter(user=owner).count()
        static_count = StaticDevice.objects.filter(user=owner).count()

        TOTPDevice.objects.filter(user=owner).delete()
        StaticDevice.objects.filter(user=owner).delete()

        AuditLog.record(
            entity_type="User",
            entity_id=owner.pk,
            action="reset_2fa_devices",
            user=owner,
            before={"totp_devices": totp_count, "static_devices": static_count},
            after={"totp_devices": 0, "static_devices": 0},
            reason=reason,
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Cleared {totp_count} TOTP + {static_count} static devices for "
                f"{owner.email}. Next password login will redirect to "
                f"/account/two_factor/setup/ for re-enrollment."
            )
        )
