"""Admin registrations for books.accounting on the custom admin site.

The admin is read-heavy for accounts in Stage 1: the COA setup wizard
(Group E) is the primary write path. Deletions are blocked by PROTECT
on JournalLine.account; the admin further disables delete.

JournalEntry and JournalLine admins are intentionally absent from Stage 1:
posted entries are immutable so there's nothing to edit; draft entries
will be shown in a richer form in Stage 2 when the transaction layer
lands. Exposing raw journal CRUD here would invite bypassing the
posting service.
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
        "is_active",
        "opening_balance",
    )
    list_filter = ("type", "is_active")
    search_fields = ("account_number", "name", "description")
    ordering = ("account_number",)
    fields = (
        "account_number",
        "name",
        "type",
        "subtype",
        "parent_account",
        "normal_balance",
        "is_active",
        "description",
        "tax_category",
        "opening_balance",
        "opening_balance_date",
    )

    def has_delete_permission(self, request, obj=None):  # type: ignore[override]
        # Accounts with posted lines are protected by on_delete=PROTECT
        # on JournalLine.account; deactivate (is_active=False) instead.
        return False


admin_site.register(Account, AccountAdmin)
