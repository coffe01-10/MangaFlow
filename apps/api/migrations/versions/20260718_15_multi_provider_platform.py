"""add multi-provider model platform

Revision ID: 20260718_15
Revises: 20260717_14
Create Date: 2026-07-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260718_15"
down_revision: str | None = "20260717_14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CATALOG_REFERENCE_COLUMNS = {
    "projects": [
        ("fk_projects_default_text_model", "default_text_model_id"),
        ("fk_projects_last_image_model", "last_image_model_id"),
    ],
    "generation_jobs": [("fk_generation_jobs_catalog_model", "catalog_model_id")],
    "generation_records": [
        ("fk_generation_records_catalog_model", "catalog_model_id")
    ],
    "page_candidates": [("fk_page_candidates_catalog_model", "catalog_model_id")],
    "asset_candidates": [("fk_asset_candidates_catalog_model", "catalog_model_id")],
}


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
    ]


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "sqlite":
        # SQLite batch mode temporarily drops and recreates referenced tables.
        # Foreign-key enforcement must be suspended for that narrow operation;
        # the migration validates the finished schema before turning it back on.
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        if connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() != 0:
            raise RuntimeError("无法暂停 SQLite 外键检查，已停止迁移")
        table_names = set(sa.inspect(connection).get_table_names())
        for table in _CATALOG_REFERENCE_COLUMNS:
            temporary = f"_alembic_tmp_{table}"
            if table in table_names and temporary in table_names:
                op.drop_table(temporary)
    op.create_table(
        "provider_profiles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("preset_key", sa.String(length=80), nullable=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("built_in", sa.Boolean(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("risk_label", sa.String(length=32), nullable=False),
        sa.Column("documentation_url", sa.String(length=500), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("preset_key"),
        if_not_exists=True,
    )
    op.create_index(
        "ix_provider_profiles_preset_key",
        "provider_profiles",
        ["preset_key"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_provider_profiles_name", "provider_profiles", ["name"], if_not_exists=True
    )

    op.create_table(
        "provider_connections",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("provider_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("protocol", sa.String(length=24), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("use_responses_api", sa.Boolean(), nullable=False),
        sa.Column("endpoint_templates", sa.JSON(), nullable=False),
        sa.Column("extra_headers", sa.JSON(), nullable=False),
        sa.Column("balance_config", sa.JSON(), nullable=False),
        sa.Column("nonsecret_config", sa.JSON(), nullable=False),
        sa.Column("health_state", sa.String(length=32), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["provider_id"], ["provider_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_id", "name"),
        if_not_exists=True,
    )
    op.create_index(
        "ix_provider_connections_provider_enabled",
        "provider_connections",
        ["provider_id", "enabled"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_provider_connections_provider_id",
        "provider_connections",
        ["provider_id"],
        if_not_exists=True,
    )

    op.create_table(
        "provider_keys",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("connection_id", sa.String(length=36), nullable=False),
        sa.Column("label", sa.String(length=80), nullable=False),
        sa.Column("encrypted_secret", sa.Text(), nullable=False),
        sa.Column("key_hint", sa.String(length=16), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("health_state", sa.String(length=32), nullable=False),
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["connection_id"], ["provider_connections.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("connection_id", "label"),
        if_not_exists=True,
    )
    op.create_index(
        "ix_provider_keys_connection_enabled",
        "provider_keys",
        ["connection_id", "enabled"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_provider_keys_connection_id",
        "provider_keys",
        ["connection_id"],
        if_not_exists=True,
    )

    op.create_table(
        "ai_models",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("connection_id", sa.String(length=36), nullable=False),
        sa.Column("provider_model_id", sa.String(length=200), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("legacy_alias", sa.String(length=64), nullable=True),
        sa.Column("model_type", sa.String(length=24), nullable=False),
        sa.Column("input_modalities", sa.JSON(), nullable=False),
        sa.Column("output_modalities", sa.JSON(), nullable=False),
        sa.Column("operations", sa.JSON(), nullable=False),
        sa.Column("api_surfaces", sa.JSON(), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("pricing", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(length=24), nullable=False),
        sa.Column("confidence", sa.String(length=24), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("success_rate", sa.Float(), nullable=True),
        sa.Column("median_latency_ms", sa.Integer(), nullable=True),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["connection_id"], ["provider_connections.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("connection_id", "provider_model_id"),
        sa.UniqueConstraint("legacy_alias"),
        if_not_exists=True,
    )
    op.create_index(
        "ix_ai_models_connection_id", "ai_models", ["connection_id"], if_not_exists=True
    )
    op.create_index(
        "ix_ai_models_legacy_alias", "ai_models", ["legacy_alias"], if_not_exists=True
    )
    op.create_index(
        "ix_ai_models_type_enabled",
        "ai_models",
        ["model_type", "enabled"],
        if_not_exists=True,
    )

    op.create_table(
        "model_probes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("connection_id", sa.String(length=36), nullable=False),
        sa.Column("model_id", sa.String(length=36), nullable=True),
        sa.Column("probe_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["connection_id"], ["provider_connections.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["model_id"], ["ai_models.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        if_not_exists=True,
    )
    op.create_index(
        "ix_model_probes_connection_id",
        "model_probes",
        ["connection_id"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_model_probes_model_id", "model_probes", ["model_id"], if_not_exists=True
    )

    op.create_table(
        "job_asset_references",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("asset_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_id"], ["generation_jobs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "asset_id"),
        if_not_exists=True,
    )
    op.create_index(
        "ix_job_asset_references_job_id",
        "job_asset_references",
        ["job_id"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_job_asset_references_asset_id",
        "job_asset_references",
        ["asset_id"],
        if_not_exists=True,
    )

    op.create_table(
        "routing_policies",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("task_kind", sa.String(length=48), nullable=False),
        sa.Column("mode", sa.String(length=24), nullable=False),
        sa.Column("required_operations", sa.JSON(), nullable=False),
        sa.Column("weights", sa.JSON(), nullable=False),
        sa.Column("fallback_config", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "task_kind"),
        if_not_exists=True,
    )
    op.create_index(
        "ix_routing_policies_project_id",
        "routing_policies",
        ["project_id"],
        if_not_exists=True,
    )

    additions = {
        "projects": [
            sa.Column("default_text_model_id", sa.String(length=36), nullable=True),
            sa.Column("last_image_model_id", sa.String(length=36), nullable=True),
        ],
        "generation_jobs": [
            sa.Column("catalog_model_id", sa.String(length=36), nullable=True)
        ],
        "generation_records": [
            sa.Column("catalog_model_id", sa.String(length=36), nullable=True)
        ],
        "page_candidates": [
            sa.Column("catalog_model_id", sa.String(length=36), nullable=True)
        ],
        "asset_candidates": [
            sa.Column("catalog_model_id", sa.String(length=36), nullable=True)
        ],
    }
    inspector = sa.inspect(op.get_bind())
    for table, columns in additions.items():
        existing_columns = {item["name"] for item in inspector.get_columns(table)}
        with op.batch_alter_table(table) as batch:
            for column in columns:
                if column.name in existing_columns:
                    continue
                batch.add_column(column)

    # A previous interrupted SQLite migration may already have added the
    # nullable columns while missing their FK/index batch. Inspect the actual
    # schema so rerunning the revision repairs that state and remains idempotent.
    for table, values in _CATALOG_REFERENCE_COLUMNS.items():
        table_inspector = sa.inspect(connection)
        foreign_key_columns = {
            tuple(item.get("constrained_columns") or [])
            for item in table_inspector.get_foreign_keys(table)
        }
        index_names = {
            item["name"] for item in table_inspector.get_indexes(table) if item["name"]
        }
        missing_foreign_keys = [
            (name, column)
            for name, column in values
            if (column,) not in foreign_key_columns
        ]
        missing_indexes = [
            column
            for _, column in values
            if f"ix_{table}_{column}" not in index_names
        ]
        if not missing_foreign_keys and not missing_indexes:
            continue
        with op.batch_alter_table(table) as batch:
            for name, column in missing_foreign_keys:
                batch.create_foreign_key(name, "ai_models", [column], ["id"], ondelete="SET NULL")
            for column in missing_indexes:
                batch.create_index(f"ix_{table}_{column}", [column])

    if connection.dialect.name == "sqlite":
        violations = list(connection.exec_driver_sql("PRAGMA foreign_key_check"))
        if violations:
            raise RuntimeError(f"多供应商迁移产生外键异常：{violations[:5]}")
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "sqlite":
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        if connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() != 0:
            raise RuntimeError("无法暂停 SQLite 外键检查，已停止回滚")
    foreign_keys = dict(reversed(list(_CATALOG_REFERENCE_COLUMNS.items())))
    for table, values in foreign_keys.items():
        with op.batch_alter_table(table) as batch:
            for name, column in values:
                batch.drop_index(f"ix_{table}_{column}")
                batch.drop_constraint(name, type_="foreignkey")
                batch.drop_column(column)

    op.drop_index("ix_routing_policies_project_id", table_name="routing_policies")
    op.drop_table("routing_policies")
    op.drop_index(
        "ix_job_asset_references_asset_id", table_name="job_asset_references"
    )
    op.drop_index("ix_job_asset_references_job_id", table_name="job_asset_references")
    op.drop_table("job_asset_references")
    op.drop_index("ix_model_probes_model_id", table_name="model_probes")
    op.drop_index("ix_model_probes_connection_id", table_name="model_probes")
    op.drop_table("model_probes")
    op.drop_index("ix_ai_models_type_enabled", table_name="ai_models")
    op.drop_index("ix_ai_models_legacy_alias", table_name="ai_models")
    op.drop_index("ix_ai_models_connection_id", table_name="ai_models")
    op.drop_table("ai_models")
    op.drop_index("ix_provider_keys_connection_id", table_name="provider_keys")
    op.drop_index("ix_provider_keys_connection_enabled", table_name="provider_keys")
    op.drop_table("provider_keys")
    op.drop_index("ix_provider_connections_provider_id", table_name="provider_connections")
    op.drop_index(
        "ix_provider_connections_provider_enabled", table_name="provider_connections"
    )
    op.drop_table("provider_connections")
    op.drop_index("ix_provider_profiles_name", table_name="provider_profiles")
    op.drop_index("ix_provider_profiles_preset_key", table_name="provider_profiles")
    op.drop_table("provider_profiles")
    if connection.dialect.name == "sqlite":
        violations = list(connection.exec_driver_sql("PRAGMA foreign_key_check"))
        if violations:
            raise RuntimeError(f"多供应商回滚产生外键异常：{violations[:5]}")
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
