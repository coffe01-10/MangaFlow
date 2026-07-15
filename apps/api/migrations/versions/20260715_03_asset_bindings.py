"""bind outfit references and scene wardrobe

Revision ID: 20260715_03
Revises: 20260714_02
Create Date: 2026-07-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260715_03"
down_revision: str | Sequence[str] | None = "20260714_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "outfits",
        sa.Column("reference_asset_ids", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("outfits", "reference_asset_ids")
