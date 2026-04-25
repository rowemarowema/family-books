from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "books.core"
    verbose_name = "Core"

    def ready(self) -> None:
        # Importing the module registers the pre_save receiver via @receiver.
        from . import signals
        signals.connect_auth_signals()
