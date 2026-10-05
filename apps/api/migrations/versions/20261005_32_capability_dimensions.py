"""Structured capability dimensions on ``ai_models.capabilities`` — no schema delta.

Revision ID: 20261005_32
Revises: 20260913_31
Create Date: 2026-10-05

P0-1 能力位契约细化 (docs/market-research/功能计划建议.md, contract section in
docs/v02-image-edit-capability-matrix.md) adds the normalized capability
dimensions ``edit_modes`` / ``resolution_tiers`` / ``media`` plus the unified
``capability_sources`` provenance map. All of them live inside the existing
``ai_models.capabilities`` JSON column — no column, constraint, index or table
change is required, so SQLite and PostgreSQL are already at parity.

Existing rows are NOT backfilled: every new dimension is fail-closed by
contract (``services/model_capabilities.py``), so an absent dimension reads as
unsupported/UNSPECIFIED and writing UNSPECIFIED placeholders onto every row
would only churn the catalog without changing any decision. Admin writes are
validated by ``provider_schemas.validate_model_capabilities_payload``.

The revision exists so the frozen contract ships a numbered head that later
migrations (e.g. the P0-2 lifecycle columns on ``ai_models``) can chain onto
without renumbering; upgrade and downgrade are intentional no-ops.
"""

from collections.abc import Sequence

revision: str = "20261005_32"
down_revision: str | None = "20260913_31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # capabilities JSON dimensions need no DDL; reads stay fail-closed.
    pass


def downgrade() -> None:
    # Nothing was written, so nothing is reverted.
    pass
