"""add editable asset display names

Revision ID: 20260717_14
Revises: 20260717_13
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260717_14"
down_revision: str | None = "20260717_13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("assets")}
    if "display_name" not in columns:
        with op.batch_alter_table("assets") as batch:
            batch.add_column(sa.Column("display_name", sa.String(length=120), nullable=True))


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("assets")}
    if "display_name" in columns:
        with op.batch_alter_table("assets") as batch:
            batch.drop_column("display_name")
