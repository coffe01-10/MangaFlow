"""Hot-path indexes for the usage ledger and candidate tables.

Revision ID: 20260913_31
Revises: 20260906_30
Create Date: 2026-09-13

Closes issue #663 (index half): ``model_call_attempts`` had no index covering
the ``(provider, model_id, started_at)`` prefix that ``usage_attempt_query``
filters and orders by (settings usage dashboard), and the two candidate tables
had no ``job_id``/``asset_id`` indexes even though every job finalization and
the asset-deletion guard in ``uploads`` scan those columns. Candidates are the
highest-volume table (one row per generated candidate) and ``model_call_attempts``
grows by one row per paid call, so both degrade as full scans over time.

This migration only creates (and on downgrade drops) the five plain
non-unique B-tree indexes declared in ``models.py`` ``__table_args__``; no
column, constraint, or data change is involved. ``op.create_index`` emits the
same ``CREATE INDEX`` on SQLite and PostgreSQL, so no dialect branch is
needed. Creation is idempotent (skip when the index name already exists),
following migration 20260901_23: offline suites and some local databases were
built with ``Base.metadata.create_all`` before being stamped, so those
databases legitimately arrive at this revision already owning the indexes.
PostgreSQL upgrade/downgrade is NOT RUN in this environment; SQLite round-trip
and create_all parity are covered by tests/test_migrations.py,
tests/test_migration_parity.py, and tests/test_hot_path_indexes.py.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260913_31"
down_revision: str | None = "20260906_30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, index name, columns) — kept in exact sync with models.py
# ``__table_args__`` declarations; tests/test_migration_parity.py compares the
# migrated schema against ``Base.metadata.create_all`` and fails on any drift.
HOT_PATH_INDEXES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "model_call_attempts",
        "ix_model_call_attempts_provider_model_started",
        ("provider", "model_id", "started_at"),
    ),
    ("page_candidates", "ix_page_candidates_job_id", ("job_id",)),
    ("page_candidates", "ix_page_candidates_asset_id", ("asset_id",)),
    ("asset_candidates", "ix_asset_candidates_job_id", ("job_id",)),
    ("asset_candidates", "ix_asset_candidates_asset_id", ("asset_id",)),
)


def _existing_index_names(table: str) -> set[str]:
    bind = op.get_bind()
    return {
        index["name"]
        for index in sa.inspect(bind).get_indexes(table)
        if index["name"]
    }


def upgrade() -> None:
    for table, name, columns in HOT_PATH_INDEXES:
        if name not in _existing_index_names(table):
            op.create_index(name, table, list(columns), unique=False)


def downgrade() -> None:
    for table, name, _columns in reversed(HOT_PATH_INDEXES):
        if name in _existing_index_names(table):
            op.drop_index(name, table_name=table)
