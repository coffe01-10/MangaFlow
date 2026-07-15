"""make the last-used image model optional

Revision ID: 20260715_05
Revises: 20260715_04
Create Date: 2026-07-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260715_05"
down_revision: str | None = "20260715_04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("projects") as batch_op:
        batch_op.alter_column(
            "last_image_model_alias",
            existing_type=sa.String(length=64),
            nullable=True,
            server_default=None,
        )


def downgrade() -> None:
    op.execute(
        "UPDATE projects SET last_image_model_alias = image_model_alias "
        "WHERE last_image_model_alias IS NULL"
    )
    with op.batch_alter_table("projects") as batch_op:
        batch_op.alter_column(
            "last_image_model_alias",
            existing_type=sa.String(length=64),
            nullable=False,
            server_default="image.nano_banana_2",
        )
