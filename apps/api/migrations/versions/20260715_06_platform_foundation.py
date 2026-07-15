"""add workflow, provider health, and runtime settings foundation

Revision ID: 20260715_06
Revises: 20260715_05
Create Date: 2026-07-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260715_06"
down_revision: str | None = "20260715_05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    managed_tables = {
        "workflow_definitions",
        "workflow_versions",
        "workflow_runs",
        "workflow_node_runs",
        "provider_health",
        "app_settings",
    }
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    existing_managed_tables = existing_tables & managed_tables
    if existing_managed_tables == managed_tables:
        # Early local builds created model metadata at startup before Alembic ran.
        # Treat that complete schema as adopted so existing projects can upgrade.
        return
    if existing_managed_tables:
        missing = ", ".join(sorted(managed_tables - existing_managed_tables))
        raise RuntimeError(f"工作流基础表处于不完整状态，缺少: {missing}")

    op.create_table(
        "workflow_definitions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("draft_graph", sa.JSON(), nullable=False),
        sa.Column("draft_version", sa.Integer(), nullable=False),
        sa.Column("published_version_id", sa.String(length=36), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "name"),
    )
    op.create_index(
        "ix_workflow_definitions_project_active",
        "workflow_definitions",
        ["project_id", "is_active"],
    )
    op.create_index(
        op.f("ix_workflow_definitions_project_id"),
        "workflow_definitions",
        ["project_id"],
    )

    op.create_table(
        "workflow_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("graph", sa.JSON(), nullable=False),
        sa.Column("graph_checksum", sa.String(length=64), nullable=False),
        sa.Column("validation_report", sa.JSON(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflow_definitions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workflow_id", "revision"),
    )
    op.create_index(
        "ix_workflow_versions_workflow_published",
        "workflow_versions",
        ["workflow_id", "published_at"],
    )
    op.create_index(
        op.f("ix_workflow_versions_workflow_id"),
        "workflow_versions",
        ["workflow_id"],
    )

    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("workflow_version_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("scope_type", sa.String(length=32), nullable=False),
        sa.Column("scope_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("start_node_ids", sa.JSON(), nullable=False),
        sa.Column("stop_node_ids", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflow_definitions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workflow_version_id"], ["workflow_versions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_workflow_runs_project_status_created",
        "workflow_runs",
        ["project_id", "status", "created_at"],
    )
    op.create_index(op.f("ix_workflow_runs_project_id"), "workflow_runs", ["project_id"])
    op.create_index(op.f("ix_workflow_runs_scope_id"), "workflow_runs", ["scope_id"])
    op.create_index(op.f("ix_workflow_runs_status"), "workflow_runs", ["status"])
    op.create_index(op.f("ix_workflow_runs_workflow_id"), "workflow_runs", ["workflow_id"])
    op.create_index(
        op.f("ix_workflow_runs_workflow_version_id"),
        "workflow_runs",
        ["workflow_version_id"],
    )

    op.create_table(
        "workflow_node_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_run_id", sa.String(length=36), nullable=False),
        sa.Column("node_id", sa.String(length=120), nullable=False),
        sa.Column("node_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=True),
        sa.Column("input_snapshot", sa.JSON(), nullable=False),
        sa.Column("output_refs", sa.JSON(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["generation_jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workflow_run_id", "node_id", "attempt_count"),
    )
    op.create_index(
        "ix_workflow_node_runs_run_status",
        "workflow_node_runs",
        ["workflow_run_id", "status"],
    )
    op.create_index(op.f("ix_workflow_node_runs_job_id"), "workflow_node_runs", ["job_id"])
    op.create_index(op.f("ix_workflow_node_runs_status"), "workflow_node_runs", ["status"])
    op.create_index(
        op.f("ix_workflow_node_runs_workflow_run_id"),
        "workflow_node_runs",
        ["workflow_run_id"],
    )

    op.create_table(
        "provider_health",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=48), nullable=False),
        sa.Column("configured", sa.Boolean(), nullable=False),
        sa.Column("credential_file_present", sa.Boolean(), nullable=False),
        sa.Column("health_state", sa.String(length=32), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("text_model_access", sa.String(length=32), nullable=False),
        sa.Column("image_model_access", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_provider_health_provider"),
        "provider_health",
        ["provider"],
        unique=True,
    )

    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
    op.drop_index(op.f("ix_provider_health_provider"), table_name="provider_health")
    op.drop_table("provider_health")
    op.drop_index(op.f("ix_workflow_node_runs_workflow_run_id"), table_name="workflow_node_runs")
    op.drop_index(op.f("ix_workflow_node_runs_status"), table_name="workflow_node_runs")
    op.drop_index(op.f("ix_workflow_node_runs_job_id"), table_name="workflow_node_runs")
    op.drop_index("ix_workflow_node_runs_run_status", table_name="workflow_node_runs")
    op.drop_table("workflow_node_runs")
    op.drop_index(op.f("ix_workflow_runs_workflow_version_id"), table_name="workflow_runs")
    op.drop_index(op.f("ix_workflow_runs_workflow_id"), table_name="workflow_runs")
    op.drop_index(op.f("ix_workflow_runs_status"), table_name="workflow_runs")
    op.drop_index(op.f("ix_workflow_runs_scope_id"), table_name="workflow_runs")
    op.drop_index(op.f("ix_workflow_runs_project_id"), table_name="workflow_runs")
    op.drop_index("ix_workflow_runs_project_status_created", table_name="workflow_runs")
    op.drop_table("workflow_runs")
    op.drop_index(op.f("ix_workflow_versions_workflow_id"), table_name="workflow_versions")
    op.drop_index("ix_workflow_versions_workflow_published", table_name="workflow_versions")
    op.drop_table("workflow_versions")
    op.drop_index(op.f("ix_workflow_definitions_project_id"), table_name="workflow_definitions")
    op.drop_index("ix_workflow_definitions_project_active", table_name="workflow_definitions")
    op.drop_table("workflow_definitions")
