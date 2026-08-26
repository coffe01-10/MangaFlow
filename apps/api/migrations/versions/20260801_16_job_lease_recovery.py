"""index generation job leases for recovery scans

Revision ID: 20260801_16
Revises: 20260718_15
Create Date: 2026-08-01
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260801_16"
down_revision: str | Sequence[str] | None = "20260718_15"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_generation_jobs_status_lease",
        "generation_jobs",
        ["status", "lease_expires_at"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_generation_jobs_status_lease", table_name="generation_jobs")
