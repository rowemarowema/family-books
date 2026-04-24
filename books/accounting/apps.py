from django.apps import AppConfig


class AccountingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "books.accounting"
    verbose_name = "Accounting Engine"

    def ready(self) -> None:
        # Importing registers the @receiver decorators.
        from . import signals  # noqa: F401
