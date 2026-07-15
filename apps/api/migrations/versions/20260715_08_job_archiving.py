"""add task history archiving

Revision ID: 20260715_08
Revises: 20260715_07
Create Date: 2026-07-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260715_08"
down_revision: str | None = "20260715_07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("generation_jobs")}
    indexes = {index["name"] for index in inspector.get_indexes("generation_jobs")}
    with op.batch_alter_table("generation_jobs") as batch:
        if "archived_at" not in columns:
            batch.add_column(sa.Column("archived_at", sa.DateTime(), nullable=True))
        if "ix_generation_jobs_project_archived_created" not in indexes:
            batch.create_index(
                "ix_generation_jobs_project_archived_created",
                ["project_id", "archived_at", "created_at"],
                unique=False,
            )


def downgrade() -> None:
    with op.batch_alter_table("generation_jobs") as batch:
        batch.drop_index("ix_generation_jobs_project_archived_created")
        batch.drop_column("archived_at")
