"""Add the per-attempt model call audit ledger.

Purely additive table: one durable, redacted row per actual provider dispatch
attempt, independent of the successful GenerationRecord.

Revision ID: 20260829_18
Revises: 20260827_17
Create Date: 2026-08-29
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine.reflection import Inspector

revision = "20260829_18"
down_revision = "20260827_17"
branch_labels = None
depends_on = None

_OWNED_COLUMNS = {
    "id": ("string", 36, False),
    "job_id": ("string", 36, False),
    "project_id": ("string", 36, False),
    "job_attempt": ("integer", None, False),
    "dispatch_no": ("integer", None, False),
    "route_switched": ("boolean", None, False),
    "outcome": ("string", 16, True),
    "provider": ("string", 120, False),
    "model_id": ("string", 128, False),
    "catalog_model_id": ("string", 36, True),
    "connection_id": ("string", 36, True),
    "selected_key_id": ("string", 36, True),
    "request_id": ("string", 200, True),
    "started_at": ("datetime", None, False),
    "finished_at": ("datetime", None, True),
    "duration_ms": ("integer", None, True),
    "usage": ("json", None, True),
    "route_reason": ("string", 32, True),
    "route_score": ("float", None, True),
    "error_code": ("string", 64, True),
    "error_message": ("string", 500, True),
    "created_at": ("datetime", None, False),
    "updated_at": ("datetime", None, False),
    "version": ("integer", None, False),
}
_OWNED_INDEXES = {
    "ix_model_call_attempts_job_started": ("job_id", "started_at"),
    "ix_model_call_attempts_outcome_started": ("outcome", "started_at"),
    "ix_model_call_attempts_catalog_model": ("catalog_model_id",),
    "ix_model_call_attempts_project_id": ("project_id",),
}
_OWNED_FOREIGN_KEYS = {
    ("job_id",): ("generation_jobs", ("id",), "RESTRICT"),
    ("project_id",): ("projects", ("id",), "CASCADE"),
    ("catalog_model_id",): ("ai_models", ("id",), "SET NULL"),
    ("connection_id",): ("provider_connections", ("id",), "SET NULL"),
    ("selected_key_id",): ("provider_keys", ("id",), "SET NULL"),
}
_OWNED_CHECKS = {
    "ck_model_call_attempts_outcome",
    "ck_model_call_attempts_dispatch_no",
    "ck_model_call_attempts_job_attempt",
    "ck_model_call_attempts_duration",
    "ck_model_call_attempts_route_switch",
}


def _column_family(column_type: sa.types.TypeEngine) -> str:
    if isinstance(column_type, sa.String):
        return "string"
    if isinstance(column_type, sa.Boolean):
        return "boolean"
    if isinstance(column_type, sa.Integer):
        return "integer"
    if isinstance(column_type, sa.DateTime):
        return "datetime"
    if isinstance(column_type, sa.JSON):
        return "json"
    if isinstance(column_type, sa.Float):
        return "float"
    return type(column_type).__name__.lower()


def _has_owned_schema(inspector: Inspector) -> bool:
    columns = {
        column["name"]: (
            _column_family(column["type"]),
            getattr(column["type"], "length", None),
            bool(column["nullable"]),
        )
        for column in inspector.get_columns("model_call_attempts")
    }
    if columns != _OWNED_COLUMNS:
        return False

    primary_key = inspector.get_pk_constraint("model_call_attempts")
    if tuple(primary_key.get("constrained_columns") or ()) != ("id",):
        return False

    unique_constraints = {
        tuple(constraint.get("column_names") or ())
        for constraint in inspector.get_unique_constraints("model_call_attempts")
    }
    if unique_constraints != {("job_id", "job_attempt", "dispatch_no")}:
        return False

    indexes = {
        index["name"]: tuple(index.get("column_names") or ())
        for index in inspector.get_indexes("model_call_attempts")
        if not index.get("unique")
    }
    if indexes != _OWNED_INDEXES:
        return False

    foreign_keys = {
        tuple(foreign_key.get("constrained_columns") or ()): (
            foreign_key.get("referred_table"),
            tuple(foreign_key.get("referred_columns") or ()),
            str((foreign_key.get("options") or {}).get("ondelete", "")).upper(),
        )
        for foreign_key in inspector.get_foreign_keys("model_call_attempts")
    }
    if foreign_keys != _OWNED_FOREIGN_KEYS:
        return False

    checks = {
        constraint.get("name")
        for constraint in inspector.get_check_constraints("model_call_attempts")
    }
    return checks == _OWNED_CHECKS


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "model_call_attempts" in inspector.get_table_names():
        if not _has_owned_schema(inspector):
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
    op.create_index(
        "ix_model_call_attempts_project_id",
        "model_call_attempts",
        ["project_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_model_call_attempts_project_id", table_name="model_call_attempts")
    op.drop_index("ix_model_call_attempts_catalog_model", table_name="model_call_attempts")
    op.drop_index("ix_model_call_attempts_outcome_started", table_name="model_call_attempts")
    op.drop_index("ix_model_call_attempts_job_started", table_name="model_call_attempts")
    op.drop_table("model_call_attempts")
