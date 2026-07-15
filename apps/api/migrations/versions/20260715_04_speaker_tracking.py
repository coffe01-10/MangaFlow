"""track canonical dialogue speakers

Revision ID: 20260715_04
Revises: 20260715_03
Create Date: 2026-07-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260715_04"
down_revision: str | None = "20260715_03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "beats",
        sa.Column("speaker_name", sa.String(length=120), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("beats", "speaker_name")
