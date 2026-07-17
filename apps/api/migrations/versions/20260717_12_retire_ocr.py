"""retire OCR as an active production setting

Revision ID: 20260717_12
Revises: 20260717_11
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260717_12"
down_revision: str | None = "20260717_11"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("UPDATE projects SET ocr_enabled = 0 WHERE ocr_enabled != 0"))
    connection.execute(
        sa.text(
            """
            UPDATE generation_jobs
            SET status = 'CONSISTENCY_CHECKING'
            WHERE status = 'OCR_CHECKING'
              AND finished_at IS NULL
            """
        )
    )


def downgrade() -> None:
    # OCR stays disabled; re-enabling it requires an explicit product decision.
    pass
