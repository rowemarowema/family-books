"""SystemFlag singleton + grace window behavior."""
from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from books.core.models import SystemFlag


@pytest.mark.django_db
def test_system_flag_is_singleton():
    flag_a = SystemFlag.get()
    flag_b = SystemFlag.get()
    assert flag_a.pk == flag_b.pk == SystemFlag.SINGLETON_PK
    assert SystemFlag.objects.count() == 1


@pytest.mark.django_db
def test_grace_window_tracked_by_in_enforcement_grace_window_property():
    flag = SystemFlag.get()
    flag.activate_enforcement()
    flag.grant_enforcement_grace(hours=1)
    assert flag.two_factor_enforcement_active is False
    assert flag.in_enforcement_grace_window is True


@pytest.mark.django_db
def test_grace_window_expires_cleanly():
    flag = SystemFlag.get()
    flag.activate_enforcement()
    flag.grant_enforcement_grace(hours=1)
    # Expire by rewinding the window.
    flag.enforcement_locked_until = timezone.now() - timedelta(minutes=5)
    flag.save()
    flag.refresh_from_db()
    assert flag.in_enforcement_grace_window is False
    assert flag.two_factor_enforcement_active is False  # middleware hasn't re-enabled yet
