"""ASGI entrypoint (reserved for future async use)."""
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "family_books.settings.prod")

application = get_asgi_application()
