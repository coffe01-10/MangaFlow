"""Add the per-attempt model call audit ledger.

Purely additive table: one durable, redacted row per actual provider dispatch
attempt, independent of the successful GenerationRecord.

Revision ID: 20260829_18
Revises: 20260827_17
Create Date: 2026-08-29
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260829_18"
down_revision = "20260827_17"
branch_labels = None
depends_on = None

_OWNED_COLUMNS = {
    "id",
    "job_id",
    "project_id",
    "job_attempt",
    "dispatch_no",
    "route_switched",
    "outcome",
    "provider",
    "model_id",
    "catalog_model_id",
    "connection_id",
    "selected_key_id",
    "request_id",
    "started_at",
    "finished_at",
    "duration_ms",
    "usage",
    "route_reason",
    "route_score",
    "error_code",
    "error_message",
    "created_at",
    "updated_at",
    "version",
}


def upgrade() -> None:
    tables = set(inspect(op.get_bind()).get_table_names())
    if "model_call_attempts" in tables:
        # Fail loudly on a foreign or partial table instead of stamping this
        # revision over a schema we do not own.
        columns = {
            column["name"]
            for column in inspect(op.get_bind()).get_columns("model_call_attempts")
        }
        if columns != _OWNED_COLUMNS:
            raise RuntimeError(
                "model_call_attempts 已存在但结构与本迁移不匹配，请人工处理后再升级"
            )
        return
    op.create_table(
        "model_call_attempts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "job_id",
            sa.String(length=36),
            sa.ForeignKey("generation_jobs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("job_attempt", sa.Integer(), nullable=False),
        sa.Column("dispatch_no", sa.Integer(), nullable=False),
        sa.Column("route_switched", sa.Boolean(), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=True),
        sa.Column("provider", sa.String(length=120), nullable=False),
        sa.Column("model_id", sa.String(length=128), nullable=False),
        sa.Column(
            "catalog_model_id",
            sa.String(length=36),
            sa.ForeignKey("ai_models.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "connection_id",
            sa.String(length=36),
            sa.ForeignKey("provider_connections.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "selected_key_id",
            sa.String(length=36),
            sa.ForeignKey("provider_keys.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("request_id", sa.String(length=200), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("usage", sa.JSON(), nullable=True),
        sa.Column("route_reason", sa.String(length=32), nullable=True),
        sa.Column("route_score", sa.Float(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "outcome IS NULL OR outcome IN ('SUCCEEDED', 'FAILED')",
            name="ck_model_call_attempts_outcome",
        ),
        sa.CheckConstraint(
            "dispatch_no >= 1", name="ck_model_call_attempts_dispatch_no"
        ),
        sa.CheckConstraint(
            "job_attempt >= 1", name="ck_model_call_attempts_job_attempt"
        ),
        sa.CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name="ck_model_call_attempts_duration",
        ),
        sa.CheckConstraint(
            "NOT route_switched OR dispatch_no >= 2",
            name="ck_model_call_attempts_route_switch",
        ),
        sa.UniqueConstraint(
            "job_id",
            "job_attempt",
            "dispatch_no",
            name="uq_model_call_attempts_job_attempt_dispatch",
        ),
    )
    op.create_index(
        "ix_model_call_attempts_job_started",
        "model_call_attempts",
        ["job_id", "started_at"],
    )
    op.create_index(
        "ix_model_call_attempts_outcome_started",
        "model_call_attempts",
        ["outcome", "started_at"],
    )
    op.create_index(
        "ix_model_call_attempts_catalog_model",
        "model_call_attempts",
        ["catalog_model_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_model_call_attempts_catalog_model", table_name="model_call_attempts")
    op.drop_index("ix_model_call_attempts_outcome_started", table_name="model_call_attempts")
    op.drop_index("ix_model_call_attempts_job_started", table_name="model_call_attempts")
    op.drop_table("model_call_attempts")
