"""Audit log admin: read-only viewer."""
from __future__ import annotations

from django.contrib import admin as django_admin

from books.audit.models import AuditLog
from books.core.admin_site import admin_site


class AuditLogAdmin(django_admin.ModelAdmin):
    list_display = ("timestamp", "action", "entity_type", "entity_id", "user", "ip_address")
    list_filter = ("action", "entity_type")
    search_fields = ("entity_id", "reason")
    date_hierarchy = "timestamp"
    readonly_fields = (
        "entity_type",
        "entity_id",
        "action",
        "user",
        "timestamp",
        "before_value",
        "after_value",
        "reason",
        "ip_address",
    )

    def has_add_permission(self, request):  # type: ignore[override]
        return False

    def has_change_permission(self, request, obj=None):  # type: ignore[override]
        return False

    def has_delete_permission(self, request, obj=None):  # type: ignore[override]
        return False


admin_site.register(AuditLog, AuditLogAdmin)
