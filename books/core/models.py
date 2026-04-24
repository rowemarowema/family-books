"""
Core models: custom User and the singleton SystemFlag.

User is a pass-through of AbstractUser — the spec is single-user, and we don't
need extra fields today, but starting with a custom User model lets us add
fields later without a disruptive migration.

SystemFlag is a singleton row that gates 2FA enforcement. See decision #22:
the env var REQUIRE_2FA trips enforcement on (sticky); only the
disable_2fa_enforcement management command can turn it back off, and only for
a 24-hour grace window that auto-re-enables if not renewed.
"""
from __future__ import annotations

from datetime import timedelta

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import CheckConstraint, Q
from django.utils import timezone


class User(AbstractUser):
    """Single-user system; AbstractUser pass-through for future-proofing."""

    class Meta(AbstractUser.Meta):
        db_table = "core_user"

    def __str__(self) -> str:
        return self.email or self.username


class SystemFlag(models.Model):
    """
    Singleton row for system-wide flags that must survive env-var flips.

    Enforcement lifecycle (decision #22):
      1. REQUIRE_2FA=1 in env -> on first authenticated request, middleware
         sets two_factor_enforcement_active=True. Sticky from this point.
      2. disable_2fa_enforcement --confirm-disable "<reason>" command sets
         two_factor_enforcement_active=False and enforcement_locked_until=
         now+24h, writes an AuditLog row.
      3. While enforcement_locked_until is in the future, middleware does NOT
         enforce. When it passes, middleware flips active back to True and
         clears locked_until, writing a second AuditLog row.
      4. REQUIRE_2FA=0 alone cannot un-set active. The env var is additive,
         not authoritative.

    setup_coa_mode (decision #14): records which path the COA setup wizard
    took: "default" / "import" / "empty" / None (not yet run).
    """

    SINGLETON_PK = 1

    id = models.PositiveSmallIntegerField(primary_key=True, default=SINGLETON_PK)
    two_factor_enforcement_active = models.BooleanField(default=False)
    enforcement_locked_until = models.DateTimeField(null=True, blank=True)
    setup_coa_mode = models.CharField(
        max_length=16,
        null=True,
        blank=True,
        choices=[
            ("default", "Loaded default COA"),
            ("import", "Imported user COA"),
            ("empty", "Started empty"),
        ],
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "core_system_flag"
        constraints = [
            # `condition=` is the Django 5.1+ name; `check=` is deprecated
            # (RemovedInDjango60Warning per
            # site-packages/django/db/models/constraints.py line 169).
            CheckConstraint(
                condition=Q(id=1),
                name="core_system_flag_singleton",
            ),
        ]

    def __str__(self) -> str:
        return f"SystemFlag(2fa={self.two_factor_enforcement_active})"

    @classmethod
    def get(cls) -> "SystemFlag":
        """Fetch or lazily create the singleton row."""
        obj, _ = cls.objects.get_or_create(id=cls.SINGLETON_PK)
        return obj

    @property
    def in_enforcement_grace_window(self) -> bool:
        """True when enforcement is temporarily disabled but will auto-re-enable."""
        return (
            not self.two_factor_enforcement_active
            and self.enforcement_locked_until is not None
            and timezone.now() < self.enforcement_locked_until
        )

    def grant_enforcement_grace(self, hours: int = 24) -> None:
        """Temporarily disable 2FA enforcement; auto re-enable after `hours`."""
        self.two_factor_enforcement_active = False
        self.enforcement_locked_until = timezone.now() + timedelta(hours=hours)
        self.save(update_fields=["two_factor_enforcement_active", "enforcement_locked_until", "updated_at"])

    def activate_enforcement(self) -> None:
        """Flip enforcement on; clears any grace window."""
        self.two_factor_enforcement_active = True
        self.enforcement_locked_until = None
        self.save(update_fields=["two_factor_enforcement_active", "enforcement_locked_until", "updated_at"])
