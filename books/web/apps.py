from django.apps import AppConfig


class WebConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "books.web"
    verbose_name = "Web UI"

    def ready(self) -> None:
        # Importing registers the @register decorators in checks.py.
        from . import checks  # noqa: F401
