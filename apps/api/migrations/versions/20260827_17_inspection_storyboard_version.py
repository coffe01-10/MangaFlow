"""Scope quality inspections to the storyboard they actually checked.

Revision ID: 20260827_17
Revises: 20260801_16
Create Date: 2026-08-27
"""

from alembic import op
from sqlalchemy import Column, Integer, inspect

revision = "20260827_17"
down_revision = "20260801_16"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {
        column["name"] for column in inspect(op.get_bind()).get_columns("inspection_results")
    }
    if "storyboard_version" not in columns:
        op.add_column("inspection_results", Column("storyboard_version", Integer(), nullable=True))
    # Legacy results have unknown provenance: retain them, but require a new quality check.


def downgrade() -> None:
    op.drop_column("inspection_results", "storyboard_version")
