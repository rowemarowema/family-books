"""Admin registrations owned by books.core (User, SystemFlag)."""
from __future__ import annotations

from django.contrib import admin as django_admin
from django.contrib.auth.admin import UserAdmin

from books.core.admin_site import admin_site
from books.core.models import SystemFlag, User


class FamilyBooksUserAdmin(UserAdmin):
    list_display = ("email", "username", "is_active", "is_superuser", "last_login")
    search_fields = ("email", "username")

    def has_add_permission(self, request):  # type: ignore[override]
        # Single-user system: no adding users through admin. Use bootstrap_owner.
        return False

    def has_delete_permission(self, request, obj=None):  # type: ignore[override]
        # Disallow deleting the owner from admin.
        return False


class SystemFlagAdmin(django_admin.ModelAdmin):
    """Read-only view of SystemFlag. Mutations go through management commands."""

    list_display = (
        "id",
        "two_factor_enforcement_active",
        "enforcement_locked_until",
        "setup_coa_mode",
        "updated_at",
    )
    readonly_fields = (
        "id",
        "two_factor_enforcement_active",
        "enforcement_locked_until",
        "setup_coa_mode",
        "updated_at",
    )

    def has_add_permission(self, request):  # type: ignore[override]
        return False

    def has_delete_permission(self, request, obj=None):  # type: ignore[override]
        return False


admin_site.register(User, FamilyBooksUserAdmin)
admin_site.register(SystemFlag, SystemFlagAdmin)
