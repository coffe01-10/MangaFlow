"""Add creator-facing model display preference.

Revision ID: 20260830_20
Revises: 20260830_19
Create Date: 2026-08-30
"""

import sqlalchemy as sa
from alembic import op

revision = "20260830_20"
down_revision = "20260830_19"
branch_labels = None
depends_on = None


def _column_names() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns("ai_models")}


def upgrade() -> None:
    if "display_enabled" in _column_names():
        return
    op.add_column(
        "ai_models",
        sa.Column(
            "display_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    if "display_enabled" not in _column_names():
        return
    hidden_count = op.get_bind().execute(
        sa.text(
            "SELECT COUNT(*) FROM ai_models "
            "WHERE display_enabled = :display_enabled"
        ),
        {"display_enabled": False},
    ).scalar_one()
    if hidden_count:
        raise RuntimeError(
            "refusing downgrade: reset hidden model preferences before "
            "removing the column"
        )
    op.drop_column("ai_models", "display_enabled")
