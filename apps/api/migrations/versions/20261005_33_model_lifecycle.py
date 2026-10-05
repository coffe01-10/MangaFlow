"""P0-2 model catalog lifecycle columns on ``ai_models``.

Revision ID: 20261005_33
Revises: 20261005_32
Create Date: 2026-10-05

Adds the two lifecycle columns the P0-2 模型目录生命周期管理 feature needs
(docs/market-research/功能计划建议.md, approach section "模型目录生命周期管理"):

- ``lifecycle`` — String(16) NOT NULL default ``'ACTIVE'``, constrained by
  ``ck_ai_models_lifecycle`` to ACTIVE / DEPRECATED / EOL. DEPRECATED stays
  history-readable but refuses new-artifact task kinds; EOL mirrors a
  provider shutdown (the authoritative source is the hand-maintained
  ``services/model_sunsets.py`` table, not a DB table — no data migration).
- ``sunset_at`` — nullable DateTime(timezone=True) recording the announced
  retirement date when known.
- ``ix_ai_models_lifecycle`` index for the catalog/dispatch filters.

``batch_alter_table`` is mandatory: SQLite cannot ``ALTER TABLE ADD
CONSTRAINT``, so the first CheckConstraint on this table requires a table
rebuild, and ``ALTER TABLE ... ADD COLUMN ... NOT NULL`` without a server
default would fail on populated databases — the batch reflect copy carries
the model-side default into the rebuild. Downgrade drops the index, the
constraint and both columns in one rebuild.

PostgreSQL upgrade/downgrade is NOT RUN in this environment; SQLite
round-trip and create_all parity are covered by tests/test_migrations.py
and tests/test_migration_parity.py.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261005_33"
down_revision: str | None = "20261005_32"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LIFECYCLE_CHECK = "lifecycle IN ('ACTIVE', 'DEPRECATED', 'EOL')"


def _existing_columns(table: str) -> set[str]:
    return {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)
    }


def _existing_index_names(table: str) -> set[str]:
    return {
        index["name"]
        for index in sa.inspect(op.get_bind()).get_indexes(table)
        if index["name"]
    }


def upgrade() -> None:
    columns = _existing_columns("ai_models")
    add_lifecycle = "lifecycle" not in columns
    add_sunset = "sunset_at" not in columns
    if add_lifecycle or add_sunset:
        with op.batch_alter_table("ai_models") as batch:
            if add_lifecycle:
                batch.add_column(
                    sa.Column(
                        "lifecycle",
                        sa.String(length=16),
                        nullable=False,
                        server_default="ACTIVE",
                    )
                )
            if add_sunset:
                batch.add_column(
                    sa.Column("sunset_at", sa.DateTime(timezone=True), nullable=True)
                )
            batch.create_check_constraint("ck_ai_models_lifecycle", _LIFECYCLE_CHECK)
    else:
        # Columns already exist (e.g. a create_all-built dev database stamped
        # to head): the constraint may still be missing, so ensure it via a
        # no-op batch when absent.
        checks = {
            constraint["name"]
            for constraint in sa.inspect(op.get_bind()).get_check_constraints(
                "ai_models"
            )
        }
        if "ck_ai_models_lifecycle" not in checks:
            with op.batch_alter_table("ai_models") as batch:
                batch.create_check_constraint(
                    "ck_ai_models_lifecycle", _LIFECYCLE_CHECK
                )
    if "ix_ai_models_lifecycle" not in _existing_index_names("ai_models"):
        op.create_index("ix_ai_models_lifecycle", "ai_models", ["lifecycle"])


def downgrade() -> None:
    if "ix_ai_models_lifecycle" in _existing_index_names("ai_models"):
        op.drop_index("ix_ai_models_lifecycle", table_name="ai_models")
    columns = _existing_columns("ai_models")
    if "lifecycle" in columns or "sunset_at" in columns:
        with op.batch_alter_table("ai_models") as batch:
            batch.drop_constraint("ck_ai_models_lifecycle", type_="check")
            if "sunset_at" in columns:
                batch.drop_column("sunset_at")
            if "lifecycle" in columns:
                batch.drop_column("lifecycle")
