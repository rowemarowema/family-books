"""Admin registrations for books.accounting on the custom admin site.

The admin is read-heavy for accounts in Stage 1: the COA setup wizard
(Group F+) is the primary write path. Deletions are blocked by PROTECT
on JournalLine.account; the admin further disables delete.

JournalEntry and JournalLine admins are intentionally absent from Stage 1:
posted entries are immutable so there's nothing to edit; draft entries
will be shown in a richer form in Stage 2 when the transaction layer
lands. Exposing raw journal CRUD here would invite bypassing the
posting service.

`display_order` is exposed as a plain editable IntegerField in Stage 1.
The polished UI for reordering (drag-and-drop, bulk reorder) is deferred
to Group I; this is the basic path so Mark can adjust ordering through
the admin without dropping to a shell. `is_system` is rendered read-only
because the only legitimate path to set it is via the `seed_default_coa`
fixture loader.
"""
from __future__ import annotations

from django.contrib import admin as django_admin

from books.accounting.models import Account
from books.core.admin_site import admin_site


class AccountAdmin(django_admin.ModelAdmin):
    list_display = (
        "account_number",
        "name",
        "type",
        "subtype",
        "normal_balance",
        "parent_account",
        "display_order",
        "is_active",
        "is_system",
        "opening_balance",
    )
    list_filter = ("type", "is_active", "is_system")
    search_fields = ("account_number", "name", "description")
    ordering = ("display_order", "name")
    fields = (
        "account_number",
        "name",
        "type",
        "subtype",
        "parent_account",
        "normal_balance",
        "display_order",
        "is_active",
        "is_system",
        "description",
        "tax_category",
        "opening_balance",
        "opening_balance_date",
    )
    readonly_fields = ("is_system",)

    def get_readonly_fields(self, request, obj=None):  # type: ignore[override]
        """System accounts: is_active is readonly too.

        Account.clean() rejects deactivating a system account, so the
        write would fail at the model layer regardless. Removing the
        field from the form is the friendly version of that protection
        — the user can't even attempt the bad write.
        """
        readonly = list(super().get_readonly_fields(request, obj))
        if obj is not None and obj.is_system and "is_active" not in readonly:
            readonly.append("is_active")
        return readonly

    def has_delete_permission(self, request, obj=None):  # type: ignore[override]
        # Accounts with posted lines are protected by on_delete=PROTECT
        # on JournalLine.account; deactivate (is_active=False) instead.
        return False


admin_site.register(Account, AccountAdmin)
