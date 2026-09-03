"""add candidate lineage table

Revision ID: 20260903_28
Revises: 20260903_27
Create Date: 2026-09-03

Additive V02-42B lineage. Creates ``candidate_lineage`` and backfills the
implicit ``request_parameters.original_candidate_id`` convention of existing
REPAIR/UPSCALE candidates into first-class rows. Historical rows are only
read, never rewritten; the backfill leaves missing parent candidates as NULL
parents. PostgreSQL upgrade/downgrade is NOT RUN (issue boundary).
"""

import json
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260903_28"
down_revision: str | None = "20260903_27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BACKFILL_KINDS = {"REPAIR": "REPAIRED", "UPSCALE": "UPSCALED"}


def upgrade() -> None:
    op.create_table(
        "candidate_lineage",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("child_candidate_id", sa.String(length=36), nullable=False),
        sa.Column("parent_candidate_id", sa.String(length=36), nullable=True),
        sa.Column("lineage_kind", sa.String(length=32), nullable=False),
        sa.Column("source_command_id", sa.String(length=36), nullable=True),
        sa.Column("mask_asset_id", sa.String(length=36), nullable=True),
        sa.Column("model_alias", sa.String(length=64), nullable=True),
        sa.Column("catalog_model_id", sa.String(length=36), nullable=True),
        sa.Column("resolution", sa.String(length=16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["child_candidate_id"],
            ["page_candidates.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["parent_candidate_id"],
            ["page_candidates.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["mask_asset_id"], ["assets.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("child_candidate_id", name="uq_candidate_lineage_child"),
    )
    op.create_index(
        "ix_candidate_lineage_child_candidate_id",
        "candidate_lineage",
        ["child_candidate_id"],
    )
    op.create_index(
        "ix_candidate_lineage_parent_candidate_id",
        "candidate_lineage",
        ["parent_candidate_id"],
    )
    op.create_index(
        "ix_candidate_lineage_source_command_id",
        "candidate_lineage",
        ["source_command_id"],
    )
    _backfill_from_request_parameters()


def _backfill_from_request_parameters() -> None:
    """L4: lift request_parameters.original_candidate_id into lineage rows.

    Portable on purpose: job JSON values are parsed in Python so the same code
    runs on SQLite and PostgreSQL. Missing parent candidates stay NULL; rows
    already carrying lineage (re-runs) are skipped.
    """

    bind = op.get_bind()
    derived = bind.execute(
        sa.text(
            """
            SELECT pc.id AS child_id, pc.model_alias AS model_alias,
                   pc.catalog_model_id AS catalog_model_id,
                   pc.resolution AS resolution, pc.created_at AS created_at,
                   b.generation_kind AS generation_kind,
                   j.request_parameters AS request_parameters
            FROM page_candidates pc
            JOIN generation_batches b ON b.id = pc.batch_id
            JOIN generation_jobs j ON j.id = pc.job_id
            WHERE b.generation_kind IN ('REPAIR', 'UPSCALE')
            """
        )
    ).mappings()
    for row in derived:
        params = row["request_parameters"]
        if isinstance(params, str):
            params = json.loads(params or "{}")
        parent_id = (params or {}).get("original_candidate_id")
        if parent_id is not None:
            parent_exists = bind.execute(
                sa.text("SELECT 1 FROM page_candidates WHERE id = :parent_id"),
                {"parent_id": parent_id},
            ).first()
            if parent_exists is None:
                parent_id = None
        already = bind.execute(
            sa.text(
                "SELECT 1 FROM candidate_lineage WHERE child_candidate_id = :child_id"
            ),
            {"child_id": row["child_id"]},
        ).first()
        if already is not None:
            continue
        bind.execute(
            sa.text(
                """
                INSERT INTO candidate_lineage (
                    id, child_candidate_id, parent_candidate_id, lineage_kind,
                    source_command_id, mask_asset_id, model_alias,
                    catalog_model_id, resolution, created_at
                ) VALUES (
                    :id, :child_id, :parent_id, :lineage_kind,
                    NULL, NULL, :model_alias,
                    :catalog_model_id, :resolution, :created_at
                )
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "child_id": row["child_id"],
                "parent_id": parent_id,
                "lineage_kind": _BACKFILL_KINDS[row["generation_kind"]],
                "model_alias": row["model_alias"],
                "catalog_model_id": row["catalog_model_id"],
                "resolution": row["resolution"],
                "created_at": row["created_at"],
            },
        )


def downgrade() -> None:
    op.drop_index(
        "ix_candidate_lineage_source_command_id", table_name="candidate_lineage"
    )
    op.drop_index(
        "ix_candidate_lineage_parent_candidate_id", table_name="candidate_lineage"
    )
    op.drop_index(
        "ix_candidate_lineage_child_candidate_id", table_name="candidate_lineage"
    )
    op.drop_table("candidate_lineage")
