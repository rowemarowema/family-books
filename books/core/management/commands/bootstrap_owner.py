"""
Create the sole owner user for this single-user system.

Usage:
    ./manage.py bootstrap_owner --email mark@example.com
        (prompts for password)

    echo "hunter2secure123!" | ./manage.py bootstrap_owner \\
        --email mark@example.com --password-stdin

Idempotent when called with the same email: reports "owner already exists"
and exits 0. Refuses to create a second owner with a different email.
"""
from __future__ import annotations

import getpass
import sys
from typing import Any

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Create the sole owner user. Idempotent with the same email."

    # Opt into an injected stdin stream so tests can pass stdin=StringIO(...)
    # to call_command. Without this, call_command raises TypeError: Unknown
    # option(s) because "stdin" isn't in BaseCommand.base_stealth_options
    # (which is only ("stderr", "stdout") per
    # site-packages/django/core/management/base.py line 273).
    stealth_options = ("stdin",)

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--email", required=True, help="Owner email (also used as username).")
        parser.add_argument(
            "--password-stdin",
            action="store_true",
            help="Read the password from stdin instead of prompting.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        User = get_user_model()
        email = options["email"].strip().lower()

        existing_owner = User.objects.filter(is_superuser=True).first()
        if existing_owner is not None:
            if existing_owner.email == email:
                self.stdout.write(self.style.SUCCESS(f"Owner {email} already exists; no-op."))
                return
            raise CommandError(
                f"An owner already exists ({existing_owner.email}). "
                "This is a single-user system; refusing to create a second owner. "
                "If you need to rotate the owner, contact the project runbook."
            )

        if User.objects.exists():
            raise CommandError(
                "Non-owner users exist in the auth table. "
                "Refusing to bootstrap on a non-empty system."
            )

        stdin = options.get("stdin") or sys.stdin
        password = (
            stdin.read().rstrip("\n") if options["password_stdin"] else _prompt_password()
        )

        try:
            validate_password(password)
        except ValidationError as exc:
            raise CommandError("Password rejected: " + "; ".join(exc.messages)) from exc

        user = User.objects.create_superuser(
            username=email,
            email=email,
            password=password,
        )
        user.save()

        # Audit the bootstrap itself. Imported late to avoid touching AuditLog
        # before migrations run during test setup.
        from books.audit.models import AuditLog
        AuditLog.record(
            entity_type="User",
            entity_id=user.pk,
            action="bootstrap_owner",
            user=user,
            after={"email": user.email, "is_superuser": True},
            reason="Initial system bootstrap.",
        )

        self.stdout.write(self.style.SUCCESS(f"Created owner {email} (id={user.pk})."))


def _prompt_password() -> str:
    while True:
        p1 = getpass.getpass("Password: ")
        p2 = getpass.getpass("Confirm: ")
        if p1 == p2:
            return p1
        sys.stderr.write("Passwords do not match, try again.\n")
