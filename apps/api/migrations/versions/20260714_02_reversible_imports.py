"""reversible imports and explicit asset purposes

Revision ID: 20260714_02
Revises: 20260714_01
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260714_02"
down_revision: str | Sequence[str] | None = "20260714_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("chapters", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE assets SET kind = 'CHARACTER_REFERENCE' WHERE kind = 'character'")
    op.execute("UPDATE assets SET kind = 'OUTFIT_REFERENCE' WHERE kind = 'outfit'")
    op.execute("UPDATE assets SET kind = 'STYLE_REFERENCE' WHERE kind = 'style'")


def downgrade() -> None:
    op.execute("UPDATE assets SET kind = 'character' WHERE kind = 'CHARACTER_REFERENCE'")
    op.execute("UPDATE assets SET kind = 'outfit' WHERE kind = 'OUTFIT_REFERENCE'")
    op.execute("UPDATE assets SET kind = 'style' WHERE kind = 'STYLE_REFERENCE'")
    op.drop_column("chapters", "deleted_at")
