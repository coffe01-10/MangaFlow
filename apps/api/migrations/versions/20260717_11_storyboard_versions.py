"""add explicit storyboard and candidate generation versions

Revision ID: 20260717_11
Revises: 20260717_10
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260717_11"
down_revision: str | None = "20260717_10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    page_columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("manga_pages")
    }
    candidate_columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("page_candidates")
    }
    with op.batch_alter_table("manga_pages") as batch:
        if "storyboard_version" not in page_columns:
            batch.add_column(
                sa.Column("storyboard_version", sa.Integer(), nullable=False, server_default="1")
            )
        if "selected_candidate_ack_version" not in page_columns:
            batch.add_column(
                sa.Column("selected_candidate_ack_version", sa.Integer(), nullable=True)
            )
    with op.batch_alter_table("page_candidates") as batch:
        if "based_on_storyboard_version" not in candidate_columns:
            batch.add_column(sa.Column("based_on_storyboard_version", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("page_candidates") as batch:
        batch.drop_column("based_on_storyboard_version")
    with op.batch_alter_table("manga_pages") as batch:
        batch.drop_column("selected_candidate_ack_version")
        batch.drop_column("storyboard_version")
