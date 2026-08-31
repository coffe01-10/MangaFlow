"""Add durable external CLI execution state.

Revision ID: 20260831_22
Revises: 20260831_21
Create Date: 2026-08-31
"""

import sqlalchemy as sa
from alembic import op

revision = "20260831_22"
down_revision = "20260831_21"
branch_labels = None
depends_on = None

_COLUMN_NAMES = {
    "id",
    "job_id",
    "model_call_attempt_id",
    "connection_id",
    "catalog_model_id",
    "run_token",
    "relative_path",
    "operation",
    "state",
    "cleanup_state",
    "lease_slot",
    "request_checksum",
    "output_manifest",
    "exit_code",
    "stdout_checksum",
    "stderr_checksum",
    "started_at",
    "finished_at",
    "error_code",
    "error_message",
    "created_at",
    "updated_at",
    "version",
}
_INDEXES = {
    "ix_cli_execution_runs_catalog_model_id",
    "ix_cli_execution_runs_connection_state",
    "ix_cli_execution_runs_job_created",
    "ix_cli_execution_runs_model_call_attempt_id",
    "ix_cli_execution_runs_state_updated",
}


def _existing_table_is_owned(inspector: sa.Inspector) -> bool:
    columns = {column["name"] for column in inspector.get_columns("cli_execution_runs")}
    indexes = {
        index["name"]
        for index in inspector.get_indexes("cli_execution_runs")
        if not index.get("unique")
    }
    primary_key = tuple(
        inspector.get_pk_constraint("cli_execution_runs").get("constrained_columns") or ()
    )
    uniques = {
        tuple(constraint.get("column_names") or ())
        for constraint in inspector.get_unique_constraints("cli_execution_runs")
    }
    return (
        columns == _COLUMN_NAMES
        and indexes == _INDEXES
        and primary_key == ("id",)
        and uniques
        == {
            ("model_call_attempt_id",),
            ("relative_path",),
            ("run_token",),
            ("connection_id", "lease_slot"),
        }
    )


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "cli_execution_runs" in inspector.get_table_names():
        if not _existing_table_is_owned(inspector):
            raise RuntimeError(
                "cli_execution_runs 已存在但结构与本迁移不匹配，请人工处理后再升级"
            )
        return
    op.create_table(
        "cli_execution_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("model_call_attempt_id", sa.String(length=36), nullable=False),
        sa.Column("connection_id", sa.String(length=36), nullable=True),
        sa.Column("catalog_model_id", sa.String(length=36), nullable=True),
        sa.Column("run_token", sa.String(length=32), nullable=False),
        sa.Column("relative_path", sa.String(length=160), nullable=False),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("cleanup_state", sa.String(length=16), nullable=False),
        sa.Column("lease_slot", sa.Integer(), nullable=True),
        sa.Column("request_checksum", sa.String(length=64), nullable=False),
        sa.Column("output_manifest", sa.JSON(), nullable=False),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("stdout_checksum", sa.String(length=64), nullable=True),
        sa.Column("stderr_checksum", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "state IN ('QUEUED', 'PREPARING', 'RUNNING', 'COMPLETED', 'FAILED')",
            name="ck_cli_execution_runs_state",
        ),
        sa.CheckConstraint(
            "cleanup_state IN ('PENDING', 'CLEANED', 'RETAINED', 'FAILED')",
            name="ck_cli_execution_runs_cleanup_state",
        ),
        sa.CheckConstraint(
            "lease_slot IS NULL OR lease_slot >= 1",
            name="ck_cli_execution_runs_lease_slot",
        ),
        sa.ForeignKeyConstraint(["catalog_model_id"], ["ai_models.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["connection_id"], ["provider_connections.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["job_id"], ["generation_jobs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["model_call_attempt_id"], ["model_call_attempts.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("model_call_attempt_id"),
        sa.UniqueConstraint("relative_path"),
        sa.UniqueConstraint("run_token"),
        sa.UniqueConstraint(
            "connection_id",
            "lease_slot",
            name="uq_cli_execution_runs_connection_slot",
        ),
    )
    op.create_index(
        "ix_cli_execution_runs_catalog_model_id", "cli_execution_runs", ["catalog_model_id"]
    )
    op.create_index(
        "ix_cli_execution_runs_connection_state",
        "cli_execution_runs",
        ["connection_id", "state"],
    )
    op.create_index(
        "ix_cli_execution_runs_job_created", "cli_execution_runs", ["job_id", "created_at"]
    )
    op.create_index(
        "ix_cli_execution_runs_model_call_attempt_id",
        "cli_execution_runs",
        ["model_call_attempt_id"],
    )
    op.create_index(
        "ix_cli_execution_runs_state_updated", "cli_execution_runs", ["state", "updated_at"]
    )


def downgrade() -> None:
    count = op.get_bind().execute(sa.text("SELECT COUNT(*) FROM cli_execution_runs")).scalar_one()
    if count:
        raise RuntimeError(
            "refusing downgrade: CLI execution audit rows must be archived "
            "before removing the table"
        )
    for name in (
        "ix_cli_execution_runs_state_updated",
        "ix_cli_execution_runs_model_call_attempt_id",
        "ix_cli_execution_runs_job_created",
        "ix_cli_execution_runs_connection_state",
        "ix_cli_execution_runs_catalog_model_id",
    ):
        op.drop_index(name, table_name="cli_execution_runs")
    op.drop_table("cli_execution_runs")
