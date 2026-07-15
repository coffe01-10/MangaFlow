"""add media thumbnails and high-volume query indexes

Revision ID: 20260715_07
Revises: 20260715_06
Create Date: 2026-07-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260715_07"
down_revision: str | None = "20260715_06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    asset_columns = {column["name"] for column in inspector.get_columns("assets")}
    asset_indexes = {index["name"] for index in inspector.get_indexes("assets")}
    with op.batch_alter_table("assets") as batch:
        if "thumbnail_320_key" not in asset_columns:
            batch.add_column(
                sa.Column("thumbnail_320_key", sa.String(length=500), nullable=True)
            )
        if "thumbnail_640_key" not in asset_columns:
            batch.add_column(
                sa.Column("thumbnail_640_key", sa.String(length=500), nullable=True)
            )
        if "ix_assets_project_deleted_created" not in asset_indexes:
            batch.create_index(
                "ix_assets_project_deleted_created",
                ["project_id", "deleted_at", "created_at"],
                unique=False,
            )

    index_specs = [
        (
            "generation_jobs",
            "ix_generation_jobs_project_status_created",
            ["project_id", "status", "created_at"],
        ),
        (
            "generation_batches",
            "ix_generation_batches_project_created_id",
            ["project_id", "created_at", "id"],
        ),
        (
            "page_candidates",
            "ix_page_candidates_batch_deleted_ordinal",
            ["batch_id", "deleted_at", "ordinal"],
        ),
    ]
    for table_name, index_name, columns in index_specs:
        indexes = {index["name"] for index in inspector.get_indexes(table_name)}
        if index_name not in indexes:
            op.create_index(index_name, table_name, columns, unique=False)


def downgrade() -> None:
    op.drop_index(
        "ix_page_candidates_batch_deleted_ordinal", table_name="page_candidates"
    )
    op.drop_index(
        "ix_generation_batches_project_created_id", table_name="generation_batches"
    )
    op.drop_index(
        "ix_generation_jobs_project_status_created", table_name="generation_jobs"
    )
    with op.batch_alter_table("assets") as batch:
        batch.drop_index("ix_assets_project_deleted_created")
        batch.drop_column("thumbnail_640_key")
        batch.drop_column("thumbnail_320_key")
