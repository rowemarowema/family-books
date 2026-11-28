"""settings/prod.py honours the SMTP TLS/SSL env flags.

Until Nov 2026 prod.py read EMAIL_USE_SSL and EMAIL_PORT but never
EMAIL_USE_TLS, so EMAIL_USE_TLS=true in the env file was silently
ignored. Django then fell back to its own default (False) and SMTP on
587 ran in plaintext. DigitalOcean blocks outbound 465 and 587 from
the droplet, so the working setup is STARTTLS on 2525 (EMAIL_PORT=2525,
EMAIL_USE_TLS=true, EMAIL_USE_SSL=false). That needs this setting.

prod.py is imported fresh in each test, with the env set by
monkeypatch. Only module-level assignments are read; no Django
reconfiguration happens.
"""
from __future__ import annotations

import importlib
import sys
from types import ModuleType

import pytest

PROD = "family_books.settings.prod"


def _load_prod() -> ModuleType:
    sys.modules.pop(PROD, None)
    try:
        return importlib.import_module(PROD)
    finally:
        sys.modules.pop(PROD, None)


@pytest.fixture
def mail_env(monkeypatch):
    for name in ("EMAIL_PORT", "EMAIL_USE_TLS", "EMAIL_USE_SSL"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_email_use_tls_true_is_honoured(mail_env):
    mail_env.setenv("EMAIL_PORT", "2525")
    mail_env.setenv("EMAIL_USE_TLS", "true")
    mail_env.setenv("EMAIL_USE_SSL", "false")

    prod = _load_prod()

    assert prod.EMAIL_PORT == 2525
    assert prod.EMAIL_USE_TLS is True
    assert prod.EMAIL_USE_SSL is False


def test_email_use_tls_false_is_honoured(mail_env):
    mail_env.setenv("EMAIL_USE_TLS", "false")

    prod = _load_prod()

    assert prod.EMAIL_USE_TLS is False


def test_defaults_unchanged_when_flags_unset(mail_env):
    """No env flags: implicit TLS on 465 as before, STARTTLS off.
    Django refuses USE_TLS and USE_SSL both True, so the TLS default
    must stay False."""
    prod = _load_prod()

    assert prod.EMAIL_PORT == 465
    assert prod.EMAIL_USE_SSL is True
    assert prod.EMAIL_USE_TLS is False
