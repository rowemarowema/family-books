"""Shared fixtures for integration tests."""
from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

from books.core.models import SystemFlag


@pytest.fixture
def owner(db):
    """The sole owner user, created via the ORM (not the bootstrap command)."""
    User = get_user_model()
    return User.objects.create_superuser(
        username="owner@example.com",
        email="owner@example.com",
        password="owner-strong-pass-88!",
    )


@pytest.fixture
def system_flag(db) -> SystemFlag:
    """The singleton SystemFlag row, freshly created (default: off)."""
    return SystemFlag.get()
