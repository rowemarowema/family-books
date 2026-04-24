"""Local development settings. Never used in prod."""
from __future__ import annotations

from .base import *  # noqa: F401, F403
from .base import LOGGING

DEBUG = True

ALLOWED_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0"]

# Readable console logs in dev; JSON is for prod log aggregation.
LOGGING["handlers"]["stdout"]["formatter"] = "console"

# 2FA enforcement off by default in dev (decision #10). Override via REQUIRE_2FA=1.
# The sticky SystemFlag set by enabling 2FA in prod does NOT apply in dev — each
# environment has its own DB.

# Email: console backend — alerts print to runserver stdout.
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
