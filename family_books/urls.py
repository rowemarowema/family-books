"""Root URL configuration.

Most routes are added by their owning apps as Stage 1 progresses:
    Group C -> auth / 2FA / admin
    Group E -> COA setup wizard + CRUD
    Group F -> trial balance
"""
from __future__ import annotations

from django.contrib import admin
from django.http import HttpRequest, HttpResponse
from django.urls import path


def healthcheck(_request: HttpRequest) -> HttpResponse:
    return HttpResponse("ok", content_type="text/plain")


urlpatterns = [
    path("admin/", admin.site.urls),
    path("health", healthcheck, name="healthcheck"),
]
