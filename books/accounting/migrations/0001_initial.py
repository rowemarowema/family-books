import django.db.models.deletion
from decimal import Decimal
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Account",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("account_number", models.CharField(max_length=16, unique=True)),
                ("name", models.CharField(max_length=128)),
                (
                    "type",
                    models.CharField(
                        choices=[
                            ("asset", "Asset"),
                            ("liability", "Liability"),
                            ("equity", "Equity"),
                            ("revenue", "Revenue"),
                            ("expense", "Expense"),
                        ],
                        max_length=16,
                    ),
                ),
                ("subtype", models.CharField(blank=True, default="", max_length=64)),
                (
                    "normal_balance",
                    models.CharField(
                        choices=[("debit", "Debit"), ("credit", "Credit")],
                        max_length=8,
                    ),
                ),
                ("is_active", models.BooleanField(default=True)),
                ("description", models.TextField(blank=True, default="")),
                ("tax_category", models.CharField(blank=True, default="", max_length=64)),
                (
                    "opening_balance",
                    models.DecimalField(
                        decimal_places=2,
                        default=Decimal("0.00"),
                        max_digits=18,
                    ),
                ),
                ("opening_balance_date", models.DateField(blank=True, null=True)),
                (
                    "parent_account",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="children",
                        to="accounting.account",
                    ),
                ),
            ],
            options={
                "db_table": "account",
                "ordering": ["account_number"],
            },
        ),
        migrations.AddIndex(
            model_name="account",
            index=models.Index(
                fields=["type", "is_active"],
                name="account_type_active_idx",
            ),
        ),
        migrations.CreateModel(
            name="JournalEntry",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("entry_date", models.DateField()),
                ("posting_date", models.DateField()),
                ("memo", models.TextField(blank=True, default="")),
                (
                    "source",
                    models.CharField(
                        choices=[
                            ("manual", "Manual"),
                            ("import", "Import"),
                            ("recurring", "Recurring"),
                            ("system", "System"),
                        ],
                        default="manual",
                        max_length=16,
                    ),
                ),
                ("reference_number", models.CharField(blank=True, default="", max_length=64)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("draft", "Draft"),
                            ("posted", "Posted"),
                            ("void", "Void"),
                        ],
                        default="draft",
                        max_length=8,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("posted_at", models.DateTimeField(blank=True, null=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_journal_entries",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "reversing_entry",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="reversed_by",
                        to="accounting.journalentry",
                    ),
                ),
            ],
            options={
                "db_table": "journal_entry",
                "ordering": ["-entry_date", "-id"],
            },
        ),
        migrations.AddIndex(
            model_name="journalentry",
            index=models.Index(
                fields=["status", "entry_date"],
                name="je_status_entrydate_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="journalentry",
            index=models.Index(
                fields=["posting_date"],
                name="je_posting_date_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="journalentry",
            constraint=models.UniqueConstraint(
                condition=models.Q(("reversing_entry__isnull", False)),
                fields=("reversing_entry",),
                name="one_reversal_per_original",
            ),
        ),
        migrations.CreateModel(
            name="JournalLine",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "debit_amount",
                    models.DecimalField(
                        decimal_places=2,
                        default=Decimal("0.00"),
                        max_digits=18,
                    ),
                ),
                (
                    "credit_amount",
                    models.DecimalField(
                        decimal_places=2,
                        default=Decimal("0.00"),
                        max_digits=18,
                    ),
                ),
                ("memo", models.TextField(blank=True, default="")),
                (
                    "account",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="journal_lines",
                        to="accounting.account",
                    ),
                ),
                (
                    "journal_entry",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="lines",
                        to="accounting.journalentry",
                    ),
                ),
            ],
            options={
                "db_table": "journal_line",
                "ordering": ["id"],
            },
        ),
        migrations.AddIndex(
            model_name="journalline",
            index=models.Index(
                fields=["account", "journal_entry"],
                name="jl_account_entry_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="journalline",
            constraint=models.CheckConstraint(
                condition=models.Q(("debit_amount__gte", 0)),
                name="journal_line_debit_non_negative",
            ),
        ),
        migrations.AddConstraint(
            model_name="journalline",
            constraint=models.CheckConstraint(
                condition=models.Q(("credit_amount__gte", 0)),
                name="journal_line_credit_non_negative",
            ),
        ),
        migrations.AddConstraint(
            model_name="journalline",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(("debit_amount__gt", 0), ("credit_amount", 0))
                    | models.Q(("debit_amount", 0), ("credit_amount__gt", 0))
                ),
                name="journal_line_debit_xor_credit",
            ),
        ),
    ]
