"""
Signal handlers that enforce Account invariants even on code paths that
skip full_clean() (e.g., Account.objects.create(), QuerySet.bulk_create
with objs that were never .clean()-ed).
"""
from __future__ import annotations

from typing import Any

from django.db.models.signals import pre_save
from django.dispatch import receiver

from books.accounting.models import Account


@receiver(pre_save, sender=Account)
def enforce_account_invariants(sender: Any, instance: Account, **kwargs: Any) -> None:
    """Runs Account.clean() before every save.

    Account.clean() enforces normal_balance/type consistency and 4-level
    hierarchy depth. Without this signal, a raw
    `Account.objects.create(...)` would skip those checks because Django's
    ModelManager does NOT call full_clean() on create.
    """
    instance.clean()
