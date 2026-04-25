"""Root URL configuration.

URL layout:
  /health                    -> unauthenticated health probe
  /account/...               -> django-two-factor-auth login + OTP flows
  /account/two_factor/disable/ -> shadowed; self-service 2FA disable is forbidden
                                  once enforcement is active. Disable via
                                  manage.py disable_2fa_enforcement instead.
  /admin/                    -> FamilyBooksAdminSite (owner-only, 2FA-gated)

Per-module routes (COA, trial balance, etc.) are added by their owning apps
as Stage 1 progresses.
"""
from __future__ import annotations

from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.urls import include, path

# two_factor.urls.urlpatterns is pre-wrapped as a 2-tuple
# (pattern_list, 'two_factor'). Import it explicitly and pass to include() so
# Django's include() destructures (urlconf_module=pattern_list, app_name=
# 'two_factor'). Verified against
# site-packages/two_factor/urls.py line 71 and
# site-packages/django/urls/conf.py lines 17-59.
from two_factor.urls import urlpatterns as two_factor_urls

from books.core.admin_site import admin_site


def healthcheck(_request: HttpRequest) -> HttpResponse:
    return HttpResponse("ok", content_type="text/plain")


def disabled_self_service_disable(_request: HttpRequest) -> HttpResponse:
    return HttpResponseForbidden(
        "Self-service 2FA disable is not available. "
        "Run: python manage.py disable_2fa_enforcement --confirm-disable '<reason>'"
    )


urlpatterns = [
    path("health", healthcheck, name="healthcheck"),
    # Shadow the two_factor DisableView so the "turn off 2FA" button cannot
    # be reached from the UI. This pattern must come BEFORE the include() of
    # two_factor.urls so URL resolution picks ours first.
    path(
        "account/two_factor/disable/",
        disabled_self_service_disable,
        name="two_factor_disable_shadow",
    ),
    path("", include(two_factor_urls)),
    path("admin/", admin_site.urls),
    # Owner-only reports (trial balance, etc.) — see books/web/urls.py.
    path("", include("books.web.urls")),
]
