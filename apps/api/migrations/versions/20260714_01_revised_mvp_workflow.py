"""revised MVP workflow

Revision ID: 20260714_01
Revises: 949d8856e6a4
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260714_01"
down_revision: str | Sequence[str] | None = "949d8856e6a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "last_image_model_alias",
            sa.String(length=64),
            nullable=False,
            server_default="image.nano_banana_2",
        ),
    )
    op.execute(
        "UPDATE projects SET last_image_model_alias = "
        "CASE WHEN image_model_alias = 'image.quality' THEN 'image.nano_banana_pro' "
        "ELSE 'image.nano_banana_2' END"
    )
    op.execute(
        "UPDATE projects SET image_model_alias = last_image_model_alias"
    )

    with op.batch_alter_table("characters") as batch:
        batch.alter_column("name", new_column_name="primary_name")
        batch.add_column(
            sa.Column("aliases", sa.JSON(), nullable=False, server_default="[]")
        )
        batch.add_column(
            sa.Column("aliases_normalized", sa.JSON(), nullable=False, server_default="[]")
        )
        batch.add_column(
            sa.Column("alias_conflict", sa.Boolean(), nullable=False, server_default=sa.false())
        )

    op.add_column("assets", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))

    for column in (
        sa.Column("revision_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("estimated_text_chars", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estimated_bubbles", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_coverage", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("selected_candidate_id", sa.String(length=36), nullable=True),
        sa.Column(
            "continuity_status",
            sa.String(length=32),
            nullable=False,
            server_default="NOT_CHECKED",
        ),
    ):
        op.add_column("manga_pages", column)
    op.create_index(
        "uq_manga_pages_revision",
        "manga_pages",
        ["chapter_id", "page_number", "revision_no"],
        unique=True,
    )

    for column in (
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("idempotency_key", sa.String(length=160), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(length=120), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    ):
        op.add_column("generation_jobs", column)
    op.create_index(
        "uq_generation_jobs_idempotency_key",
        "generation_jobs",
        ["idempotency_key"],
        unique=True,
    )

    op.create_table(
        "source_segments",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("source_revision_id", sa.String(length=36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_revision_id"], ["source_revisions.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("source_revision_id", "ordinal"),
    )
    op.create_index(
        "ix_source_segments_source_revision_id",
        "source_segments",
        ["source_revision_id"],
    )

    op.create_table(
        "script_revisions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("chapter_id", sa.String(length=36), nullable=False),
        sa.Column("source_revision_id", sa.String(length=36), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("coverage", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_revision_id"], ["source_revisions.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("chapter_id", "revision_no"),
    )
    op.create_index("ix_script_revisions_chapter_id", "script_revisions", ["chapter_id"])
    op.create_index(
        "ix_script_revisions_source_revision_id",
        "script_revisions",
        ["source_revision_id"],
    )

    op.create_table(
        "character_references",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("character_id", sa.String(length=36), nullable=False),
        sa.Column("asset_id", sa.String(length=36), nullable=False),
        sa.Column("angle", sa.String(length=32), nullable=False),
        sa.Column("is_canonical", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["character_id"], ["characters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("character_id", "asset_id"),
    )
    op.create_index(
        "ix_character_references_character_id", "character_references", ["character_id"]
    )
    op.create_index(
        "ix_character_references_asset_id", "character_references", ["asset_id"]
    )

    op.create_table(
        "generation_batches",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("chapter_id", sa.String(length=36), nullable=True),
        sa.Column("page_id", sa.String(length=36), nullable=True),
        sa.Column("target_type", sa.String(length=32), nullable=True),
        sa.Column("target_id", sa.String(length=36), nullable=True),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("generation_kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["page_id"], ["manga_pages.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("project_id", "ordinal"),
    )
    op.create_index("ix_generation_batches_project_id", "generation_batches", ["project_id"])
    op.create_index("ix_generation_batches_chapter_id", "generation_batches", ["chapter_id"])
    op.create_index("ix_generation_batches_page_id", "generation_batches", ["page_id"])
    op.create_index("ix_generation_batches_target_id", "generation_batches", ["target_id"])

    resolution = sa.Enum("DRAFT_1K", "STANDARD_2K", "HIGH_4K", name="resolution")
    op.create_table(
        "page_candidates",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("batch_id", sa.String(length=36), nullable=False),
        sa.Column("page_id", sa.String(length=36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("model_alias", sa.String(length=64), nullable=False),
        sa.Column("resolution", resolution, nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("asset_id", sa.String(length=36), nullable=True),
        sa.Column("job_id", sa.String(length=36), nullable=True),
        sa.Column("generation_record_id", sa.String(length=36), nullable=True),
        sa.Column("is_favorite", sa.Boolean(), nullable=False),
        sa.Column("is_selected", sa.Boolean(), nullable=False),
        sa.Column("prompt_snapshot", sa.JSON(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["batch_id"], ["generation_batches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["page_id"], ["manga_pages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["job_id"], ["generation_jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["generation_record_id"], ["generation_records.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("batch_id", "ordinal"),
    )
    op.create_index("ix_page_candidates_batch_id", "page_candidates", ["batch_id"])
    op.create_index("ix_page_candidates_page_id", "page_candidates", ["page_id"])

    op.create_table(
        "asset_candidates",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("batch_id", sa.String(length=36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("model_alias", sa.String(length=64), nullable=False),
        sa.Column("resolution", resolution, nullable=False),
        sa.Column("variant", sa.String(length=48), nullable=False),
        sa.Column("instruction", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("asset_id", sa.String(length=36), nullable=True),
        sa.Column("job_id", sa.String(length=36), nullable=True),
        sa.Column("generation_record_id", sa.String(length=36), nullable=True),
        sa.Column("is_favorite", sa.Boolean(), nullable=False),
        sa.Column("prompt_snapshot", sa.JSON(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["batch_id"], ["generation_batches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["job_id"], ["generation_jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["generation_record_id"], ["generation_records.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("batch_id", "ordinal"),
    )
    op.create_index("ix_asset_candidates_batch_id", "asset_candidates", ["batch_id"])

    op.create_table(
        "page_source_segments",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("page_id", sa.String(length=36), nullable=False),
        sa.Column("source_segment_id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(["page_id"], ["manga_pages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_segment_id"], ["source_segments.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("page_id", "source_segment_id"),
    )
    op.create_index("ix_page_source_segments_page_id", "page_source_segments", ["page_id"])
    op.create_index(
        "ix_page_source_segments_source_segment_id",
        "page_source_segments",
        ["source_segment_id"],
    )

    op.create_table(
        "export_bundles",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("chapter_id", sa.String(length=36), nullable=True),
        sa.Column("export_type", sa.String(length=24), nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_export_bundles_project_id", "export_bundles", ["project_id"])
    op.create_index("ix_export_bundles_chapter_id", "export_bundles", ["chapter_id"])

    with op.batch_alter_table("inspection_results") as batch:
        batch.alter_column("generation_record_id", nullable=True)
        batch.add_column(sa.Column("candidate_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key(
            "fk_inspection_results_candidate_id",
            "page_candidates",
            ["candidate_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_index("ix_inspection_results_candidate_id", ["candidate_id"])


def downgrade() -> None:
    with op.batch_alter_table("inspection_results") as batch:
        batch.drop_index("ix_inspection_results_candidate_id")
        batch.drop_constraint("fk_inspection_results_candidate_id", type_="foreignkey")
        batch.drop_column("candidate_id")
        batch.alter_column("generation_record_id", nullable=False)

    op.drop_index("ix_export_bundles_chapter_id", table_name="export_bundles")
    op.drop_index("ix_export_bundles_project_id", table_name="export_bundles")
    op.drop_table("export_bundles")
    op.drop_index("ix_page_source_segments_source_segment_id", table_name="page_source_segments")
    op.drop_index("ix_page_source_segments_page_id", table_name="page_source_segments")
    op.drop_table("page_source_segments")
    op.drop_index("ix_page_candidates_page_id", table_name="page_candidates")
    op.drop_index("ix_page_candidates_batch_id", table_name="page_candidates")
    op.drop_index("ix_asset_candidates_batch_id", table_name="asset_candidates")
    op.drop_table("asset_candidates")
    op.drop_table("page_candidates")
    op.drop_index("ix_generation_batches_target_id", table_name="generation_batches")
    op.drop_index("ix_generation_batches_page_id", table_name="generation_batches")
    op.drop_index("ix_generation_batches_chapter_id", table_name="generation_batches")
    op.drop_index("ix_generation_batches_project_id", table_name="generation_batches")
    op.drop_table("generation_batches")
    op.drop_index("ix_character_references_asset_id", table_name="character_references")
    op.drop_index("ix_character_references_character_id", table_name="character_references")
    op.drop_table("character_references")
    op.drop_index("ix_script_revisions_source_revision_id", table_name="script_revisions")
    op.drop_index("ix_script_revisions_chapter_id", table_name="script_revisions")
    op.drop_table("script_revisions")
    op.drop_index("ix_source_segments_source_revision_id", table_name="source_segments")
    op.drop_table("source_segments")
    op.drop_index("uq_generation_jobs_idempotency_key", table_name="generation_jobs")
    for column in (
        "lease_expires_at",
        "lease_owner",
        "finished_at",
        "started_at",
        "scheduled_at",
        "idempotency_key",
        "progress",
    ):
        op.drop_column("generation_jobs", column)
    op.drop_index("uq_manga_pages_revision", table_name="manga_pages")
    for column in (
        "continuity_status",
        "selected_candidate_id",
        "source_coverage",
        "estimated_bubbles",
        "estimated_text_chars",
        "revision_no",
    ):
        op.drop_column("manga_pages", column)
    op.drop_column("assets", "deleted_at")
    with op.batch_alter_table("characters") as batch:
        batch.drop_column("alias_conflict")
        batch.drop_column("aliases_normalized")
        batch.drop_column("aliases")
        batch.alter_column("primary_name", new_column_name="name")
    op.drop_column("projects", "last_image_model_alias")
