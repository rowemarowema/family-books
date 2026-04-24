"""Postgres triggers enforcing "posted entries are immutable" at the DB layer.

Rationale: JournalEntry.save() and JournalLine.save() overrides block
mutations from the Django ORM, but raw SQL / QuerySet.update() /
bulk_update / data migrations can bypass save(). Defense in depth: a
BEFORE INSERT OR UPDATE OR DELETE trigger on each table raises if the
parent JournalEntry is posted.

The "draft -> posted" transition itself is permitted because OLD.status
is still 'draft' at the moment the update fires.
"""
from django.db import migrations


CREATE_JOURNAL_ENTRY_TRIGGER = r"""
CREATE OR REPLACE FUNCTION journal_entry_enforce_immutability()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND OLD.status = 'posted' THEN
        RAISE EXCEPTION 'JournalEntry % is posted; cannot modify in place.', OLD.id
            USING ERRCODE = 'check_violation';
    END IF;
    IF TG_OP = 'DELETE' AND OLD.status = 'posted' THEN
        RAISE EXCEPTION 'JournalEntry % is posted; cannot delete.', OLD.id
            USING ERRCODE = 'check_violation';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS journal_entry_immutability ON journal_entry;
CREATE TRIGGER journal_entry_immutability
    BEFORE UPDATE OR DELETE ON journal_entry
    FOR EACH ROW EXECUTE FUNCTION journal_entry_enforce_immutability();
"""

DROP_JOURNAL_ENTRY_TRIGGER = r"""
DROP TRIGGER IF EXISTS journal_entry_immutability ON journal_entry;
DROP FUNCTION IF EXISTS journal_entry_enforce_immutability();
"""

CREATE_JOURNAL_LINE_TRIGGER = r"""
CREATE OR REPLACE FUNCTION journal_line_enforce_immutability()
RETURNS TRIGGER AS $$
DECLARE
    parent_entry_id BIGINT;
    parent_status TEXT;
BEGIN
    parent_entry_id := COALESCE(NEW.journal_entry_id, OLD.journal_entry_id);
    SELECT status INTO parent_status FROM journal_entry WHERE id = parent_entry_id;
    IF parent_status = 'posted' THEN
        RAISE EXCEPTION 'JournalLine on posted JournalEntry %; cannot insert/modify/delete.',
                        parent_entry_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS journal_line_immutability ON journal_line;
CREATE TRIGGER journal_line_immutability
    BEFORE INSERT OR UPDATE OR DELETE ON journal_line
    FOR EACH ROW EXECUTE FUNCTION journal_line_enforce_immutability();
"""

DROP_JOURNAL_LINE_TRIGGER = r"""
DROP TRIGGER IF EXISTS journal_line_immutability ON journal_line;
DROP FUNCTION IF EXISTS journal_line_enforce_immutability();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("accounting", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(
            sql=CREATE_JOURNAL_ENTRY_TRIGGER,
            reverse_sql=DROP_JOURNAL_ENTRY_TRIGGER,
        ),
        migrations.RunSQL(
            sql=CREATE_JOURNAL_LINE_TRIGGER,
            reverse_sql=DROP_JOURNAL_LINE_TRIGGER,
        ),
    ]
