"""ensure each image belongs to only one character

Revision ID: 20260717_13
Revises: 20260717_12
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260717_13"
down_revision: str | None = "20260717_12"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            """
            DELETE FROM character_references
            WHERE id IN (
                SELECT id FROM (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY asset_id
                               ORDER BY is_canonical DESC, created_at DESC, id ASC
                           ) AS duplicate_rank
                    FROM character_references
                ) ranked
                WHERE duplicate_rank > 1
            )
            """
        )
    )
    connection.execute(
        sa.text(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "uq_character_reference_asset ON character_references (asset_id)"
        )
    )


def downgrade() -> None:
    op.get_bind().execute(sa.text("DROP INDEX IF EXISTS uq_character_reference_asset"))
