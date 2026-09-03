"""add storyboard canvas, panel geometry and dialogue bubble columns

Revision ID: 20260902_26
Revises: 20260902_25
Create Date: 2026-09-02

Pure nullable JSON additions for the V02-30 storyboard layout contract
(docs/v02-storyboard-layout-contract.md). No data backfill: legacy
``bounds``/``region`` values are never rewritten and stay the compatibility
fields; the new columns stay NULL until a canvas save writes them.

``manga_pages.geometry_save_command`` holds the last PUT storyboard-geometry
command tuple ``(request_id, payload_hash, storyboard_version)`` (§10.2) so
idempotent replay survives process restarts without a command-history table.

Plain ADD/DROP COLUMN DDL on purpose (contract §13 allows either style):
``batch_alter_table`` recreates the table on SQLite, and dropping a parent
table with foreign keys enabled cascades child-row wipes on downgrade.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260902_26"
down_revision: str | None = "20260902_25"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    page_columns = {column["name"] for column in inspector.get_columns("manga_pages")}
    panel_columns = {column["name"] for column in inspector.get_columns("panels")}
    dialogue_columns = {column["name"] for column in inspector.get_columns("dialogues")}
    if "canvas" not in page_columns:
        op.add_column("manga_pages", sa.Column("canvas", sa.JSON(), nullable=True))
    if "geometry_save_command" not in page_columns:
        op.add_column(
            "manga_pages",
            sa.Column("geometry_save_command", sa.JSON(), nullable=True),
        )
    if "geometry" not in panel_columns:
        op.add_column("panels", sa.Column("geometry", sa.JSON(), nullable=True))
    if "bubble" not in dialogue_columns:
        op.add_column("dialogues", sa.Column("bubble", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("manga_pages", "geometry_save_command")
    op.drop_column("dialogues", "bubble")
    op.drop_column("panels", "geometry")
    op.drop_column("manga_pages", "canvas")
