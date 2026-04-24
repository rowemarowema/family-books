"""
Base settings shared across dev, prod, and test.

Environment-specific overrides live in sibling modules:
    dev.py   — local development
    prod.py  — Render deployment
    test.py  — pytest runs

Secrets (SECRET_KEY, DATABASE_URL, API keys) come from environment
variables via django-environ. See .env.example for the full list.
"""
from __future__ import annotations

from pathlib import Path

import environ

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
# BASE_DIR = <repo root> (the directory containing manage.py)
BASE_DIR = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
env = environ.Env(
    DEBUG=(bool, False),
    REQUIRE_2FA=(bool, False),
    ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
)

# Load .env if present (ignored in prod where env vars come from the platform)
env_file = BASE_DIR / ".env"
if env_file.exists():
    environ.Env.read_env(str(env_file))

# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------
SECRET_KEY = env("SECRET_KEY", default="dev-insecure-key-override-in-env")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

# Feature flags
# REQUIRE_2FA is read here but enforcement logic lives in books.core.middleware
# (implemented in Stage 1 / Group C). See docs/DECISIONS.md #22: once enforcement
# is active in the DB (SystemFlag.two_factor_enforcement_active), flipping this
# env var back to False does NOT re-disable 2FA; a management command is required.
REQUIRE_2FA = env("REQUIRE_2FA")

# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS: list[str] = [
    "django_otp",
    "django_otp.plugins.otp_totp",
    "django_otp.plugins.otp_static",
    # formtools is a transitive requirement of django-two-factor-auth (its
    # SessionWizardView). Must be installed for the login/setup flow.
    "formtools",
    "two_factor",
    "axes",
]

LOCAL_APPS = [
    "books.core",
    "books.audit",
    "books.accounting",
    "books.coa",
    "books.web",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

# ---------------------------------------------------------------------------
# Custom admin site (books.core.admin_site.FamilyBooksAdminSite)
# ---------------------------------------------------------------------------
# Auto-discovery is handled by the default admin config; we register on our
# custom site in each app's admin.py.


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # OTPMiddleware annotates request.user with is_verified(); must come
    # after AuthenticationMiddleware.
    "django_otp.middleware.OTPMiddleware",
    # AxesMiddleware catches lockouts on login; must come after auth.
    "axes.middleware.AxesMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Our hardening middleware comes last so it sees the fully authenticated
    # + OTP-annotated request.
    "books.core.middleware.SessionAbsoluteTimeoutMiddleware",
    "books.core.middleware.TwoFactorEnforcementMiddleware",
]

ROOT_URLCONF = "family_books.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "family_books.wsgi.application"
ASGI_APPLICATION = "family_books.asgi.application"

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default="postgres://family_books:family_books@localhost:5433/family_books",
    )
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
AUTH_USER_MODEL = "core.User"

# django-axes must come first so brute-force attempts are caught before the
# standard auth backend validates credentials.
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]

LOGIN_URL = "two_factor:login"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = LOGIN_URL

# ---------------------------------------------------------------------------
# Password hashing and validators
# ---------------------------------------------------------------------------
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ---------------------------------------------------------------------------
# Sessions (hardened in Group C)
# ---------------------------------------------------------------------------
# Decision #4: 2-hour idle, 12-hour absolute cap.
# SESSION_COOKIE_AGE + SESSION_SAVE_EVERY_REQUEST gives idle behavior;
# absolute cap enforced by custom middleware in books.core (Group C).
SESSION_COOKIE_AGE = 2 * 60 * 60  # 2 hours
SESSION_SAVE_EVERY_REQUEST = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
# SESSION_COOKIE_SECURE set in prod.py

SESSION_ABSOLUTE_TIMEOUT_SECONDS = 12 * 60 * 60  # 12 hours; enforced by middleware

# ---------------------------------------------------------------------------
# django-axes (brute-force lockout)
# ---------------------------------------------------------------------------
from datetime import timedelta as _timedelta  # noqa: E402

AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = _timedelta(minutes=15)
# Verified against site-packages/axes/conf.py line 16 — this is the axes 6+
# replacement for the deprecated AXES_ONLY_USER_FAILURES / AXES_LOCK_OUT_* set.
AXES_LOCKOUT_PARAMETERS = ["username", "ip_address"]
AXES_RESET_ON_SUCCESS = True

# ---------------------------------------------------------------------------
# django-two-factor-auth
# ---------------------------------------------------------------------------
# Never offer a "remember this device for N days" option; every login re-prompts
# for the OTP. Aligned with decision #10: 2FA is load-bearing once enabled.
TWO_FACTOR_REMEMBER_COOKIE_AGE = 0
TWO_FACTOR_PATCH_ADMIN = False  # we use our own FamilyBooksAdminSite gate
TWO_FACTOR_CALL_GATEWAY = None
TWO_FACTOR_SMS_GATEWAY = None

# ---------------------------------------------------------------------------
# Internationalization
# ---------------------------------------------------------------------------
# Decision #20 — Timezone policy:
#   Storage: UTC (USE_TZ=True stores all DateTimeField in UTC)
#   Display: America/Chicago (Central)
#   Transaction dates: naive DateField (no time, no TZ)
# See docs/TIMEZONE.md for the rendering contract.
LANGUAGE_CODE = "en-us"
TIME_ZONE = "America/Chicago"
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Static files / media
# ---------------------------------------------------------------------------
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"] if (BASE_DIR / "static").exists() else []
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
# dev.py swaps the JSON formatter for a readable console one.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "()": "family_books.log.JsonFormatter",
        },
        "console": {
            "format": "[{asctime}] {levelname:<8} {name}: {message}",
            "style": "{",
        },
    },
    "handlers": {
        "stdout": {
            "class": "logging.StreamHandler",
            "formatter": "json",
        },
    },
    "root": {
        "handlers": ["stdout"],
        "level": env("LOG_LEVEL", default="INFO"),
    },
    "loggers": {
        "django": {
            "handlers": ["stdout"],
            "level": "INFO",
            "propagate": False,
        },
        "books": {
            "handlers": ["stdout"],
            "level": env("LOG_LEVEL", default="INFO"),
            "propagate": False,
        },
    },
}
