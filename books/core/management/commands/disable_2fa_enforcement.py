"""
Deliberately disable 2FA enforcement for a 24-hour grace window.

Implementation of decision #22: once SystemFlag.two_factor_enforcement_active
is True in prod, flipping the REQUIRE_2FA env var back to False does NOT
re-disable 2FA. The only path to disable enforcement is this command, and
even it only buys 24 hours before the middleware auto-re-enables.

Usage:
    ./manage.py disable_2fa_enforcement --confirm-disable "<reason>"

The --confirm-disable flag is mandatory. The supplied reason is written
verbatim to AuditLog as evidence that this was a deliberate act.
"""
from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = (
        "Temporarily disable 2FA enforcement for 24 hours. "
        "Middleware auto-re-enables after the grace window."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--confirm-disable",
            required=True,
            help="Explicit acknowledgment + reason; written to the audit log verbatim.",
        )
        parser.add_argument(
            "--hours",
            type=int,
            default=24,
            help="Grace window in hours (default: 24; clamped 1..24).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from books.audit.models import AuditLog
        from books.core.models import SystemFlag

        reason = options["confirm_disable"].strip()
        if not reason:
            raise CommandError("--confirm-disable must be a non-empty reason.")
        hours = max(1, min(24, int(options["hours"])))

        flag = SystemFlag.get()

        if not flag.two_factor_enforcement_active and flag.in_enforcement_grace_window:
            self.stdout.write(
                self.style.WARNING(
                    f"Enforcement is already in a grace window until "
                    f"{flag.enforcement_locked_until:%Y-%m-%d %H:%M %Z}. Extending."
                )
            )
        elif not flag.two_factor_enforcement_active:
            self.stdout.write(
                self.style.WARNING("Enforcement is already off. Nothing to disable.")
            )
            return

        before = {
            "two_factor_enforcement_active": flag.two_factor_enforcement_active,
            "enforcement_locked_until": (
                flag.enforcement_locked_until.isoformat() if flag.enforcement_locked_until else None
            ),
        }
        flag.grant_enforcement_grace(hours=hours)
        after = {
            "two_factor_enforcement_active": flag.two_factor_enforcement_active,
            "enforcement_locked_until": flag.enforcement_locked_until.isoformat(),
        }

        AuditLog.record(
            entity_type="SystemFlag",
            entity_id=flag.pk,
            action="two_factor_enforcement_disabled",
            before=before,
            after=after,
            reason=reason,
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"2FA enforcement disabled until {flag.enforcement_locked_until:%Y-%m-%d %H:%M %Z}. "
                f"Middleware will auto-re-enable after that time."
            )
        )
