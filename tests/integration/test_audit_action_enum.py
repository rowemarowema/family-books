"""Drift test for AuditLog.action vs AuditAction enum.

Pattern mirrors the Group D `pg_constraint` drift tests: assume someone in
the future writes a string-literal action, this test trips so it gets
caught before merging. Cheap, durable, no special infra required.
"""
from __future__ import annotations

import pytest

from books.audit.models import AuditAction, AuditLog


@pytest.mark.django_db
def test_every_audit_action_value_is_in_the_enum():
    """Every distinct AuditLog.action value in the DB must be a member.

    Run after the rest of the test suite to capture rows written by
    fixtures and other tests; on a fresh DB the queryset is empty, which
    also passes (vacuously) and is fine.
    """
    enum_values = set(AuditAction.values)
    db_values = set(
        AuditLog.objects.values_list("action", flat=True).distinct()
    )
    drift = db_values - enum_values
    assert not drift, (
        f"AuditLog rows contain action values not in AuditAction enum: "
        f"{sorted(drift)}. Add them to AuditAction or remove the call site."
    )


@pytest.mark.django_db
def test_record_helper_accepts_enum_member_and_string_literal():
    """AuditLog.record() coerces both AuditAction members and raw strings."""
    e1 = AuditLog.record(
        entity_type="Test",
        entity_id="1",
        action=AuditAction.POST_ENTRY,
    )
    e2 = AuditLog.record(
        entity_type="Test",
        entity_id="2",
        action="post_entry",
    )
    assert e1.action == "post_entry"
    assert e2.action == "post_entry"


def test_audit_action_covers_all_known_origins():
    """Tripwire: explicit origin coverage so the enum can't silently lose a member.

    If you remove an entry, this test fails and forces you to think
    about whether the call site was actually retired.
    """
    expected = {
        # Auth / Group C
        "bootstrap_owner",
        "auth_login_failed",
        "auth_lockout",
        "two_factor_enforcement_activated",
        "two_factor_enforcement_disabled",
        "two_factor_enforcement_auto_re_enabled",
        "reset_2fa_devices",
        # Accounting / Group D
        "post_entry",
        "reverse_entry",
        # COA / Group E
        "coa_seeded",
        "coa_seed_refused",
        "coa_reset",
        "coa_reset_refused",
    }
    assert set(AuditAction.values) == expected


def test_audit_action_field_has_choices():
    """The model field carries `choices=AuditAction.choices` so admin /
    forms get the dropdown; the migration 0002 also reflects this."""
    field = AuditLog._meta.get_field("action")
    assert field.choices is not None
    field_values = {value for value, _label in field.choices}
    assert field_values == set(AuditAction.values)
