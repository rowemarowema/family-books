"""Group E commit 1 — foundations.

Schema additions for the COA bootstrap path:
- `Account.display_order` IntegerField (default 0, indexed). User accounts
  keep the default; system accounts use negative values to sort to the top
  of their grouping. See Batch #4 in project_decisions.md.
- `Account.is_system` BooleanField (default False, indexed). Marks the 3
  system accounts seeded from `fixtures/default_coa.json`'s
  `system_accounts` array. Protection logic (Account.clean + admin form +
  pre_save signal) lands in commit 2.
- `Account.Meta.ordering` flips from `["account_number"]` to
  `["display_order", "name"]`.
- New composite index on `(display_order, name)` so the default sort is
  index-covered. The single-column `display_order` index produced by
  `db_index=True` on the field stays — it's redundant for the composite
  prefix but Django emits it anyway, and dropping it would require an
  explicit `RemoveIndex` we don't need.

Pure schema migration — no data migration. Existing rows pick up the
defaults (display_order=0, is_system=False).
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounting", "0002_immutability_triggers"),
    ]

    operations = [
        migrations.AddField(
            model_name="account",
            name="display_order",
            field=models.IntegerField(db_index=True, default=0),
        ),
        migrations.AddField(
            model_name="account",
            name="is_system",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AlterModelOptions(
            name="account",
            options={"ordering": ["display_order", "name"]},
        ),
        migrations.AddIndex(
            model_name="account",
            index=models.Index(
                fields=["display_order", "name"],
                name="account_disporder_name_idx",
            ),
        ),
    ]
