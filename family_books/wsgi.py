"""WSGI entrypoint for gunicorn in production."""
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "family_books.settings.prod")

application = get_wsgi_application()
