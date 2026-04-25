"""Settings for pytest runs."""
from __future__ import annotations

from .base import *  # noqa: F403

DEBUG = False

# Fast, deterministic password hashing for tests.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

# Silence migrations and use an in-memory SQLite when the test DB is not set.
# Default to Postgres (docker-compose) because the accounting engine relies on
# NUMERIC semantics and DB-level CHECK constraints that SQLite does not fully
# emulate.
import os  # noqa: E402

if "TEST_DATABASE_URL" in os.environ:
    import environ as _environ
    DATABASES = {"default": _environ.Env().db("TEST_DATABASE_URL")}

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# 2FA enforcement off during tests unless a specific test flips the SystemFlag.
REQUIRE_2FA = False

# Keep logging quiet during tests.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"null": {"class": "logging.NullHandler"}},
    "root": {"handlers": ["null"], "level": "WARNING"},
}
