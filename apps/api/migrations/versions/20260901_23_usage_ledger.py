"""Extend the model-call usage ledger and add billing reconciliations.

Revision ID: 20260901_23
Revises: 20260831_22
Create Date: 2026-09-01
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

import sqlalchemy as sa
from alembic import op

revision = "20260901_23"
down_revision = "20260831_22"
branch_labels = None
depends_on = None

_ATTEMPT_INDEXES = (
    ("ix_model_call_attempts_project_started", ("project_id", "started_at")),
    ("ix_model_call_attempts_channel_started", ("channel", "started_at")),
    ("ix_model_call_attempts_chapter_started", ("chapter_id", "started_at")),
    ("ix_model_call_attempts_page_started", ("page_id", "started_at")),
    ("ix_model_call_attempts_candidate_started", ("candidate_id", "started_at")),
)
_KNOWN_USAGE_KEYS = {
    "input_tokens",
    "prompt_tokens",
    "prompt_token_count",
    "output_tokens",
    "completion_tokens",
    "candidates_token_count",
    "cached_input_tokens",
    "cached_content_token_count",
    "cache_read_input_tokens",
    "prompt_tokens_details",
    "output_images",
    "total_tokens",
    "total_token_count",
    "cache_creation_input_tokens",
    "estimated_cost",
    "cost_source",
    "cleanup_warning",
}


def _decimal(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not result.is_finite() or result < 0 or result != result.to_integral_value():
        return None
    return int(result)


def _is_positive_number(value: object) -> bool:
    if isinstance(value, bool):
        return False
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return False
    return result.is_finite() and result > 0


def _first(usage: dict, *paths: tuple[str, ...]) -> int | None:
    for path in paths:
        value: object = usage
        for part in path:
            if not isinstance(value, dict) or part not in value:
                value = None
                break
            value = value[part]
        parsed = _decimal(value)
        if parsed is not None:
            return parsed
    return None


def _normalize(usage: object) -> dict[str, object | None]:
    if isinstance(usage, str):
        try:
            usage = json.loads(usage)
        except (TypeError, ValueError):
            usage = None
    if not isinstance(usage, dict):
        return {
            "usage_status": "UNKNOWN",
            "usage_source": None,
            "unit_kind": "UNKNOWN",
            "input_tokens": None,
            "output_tokens": None,
            "cached_input_tokens": None,
            "cache_hit": None,
            "output_images": None,
        }
    input_tokens = _first(
        usage,
        ("input_tokens",),
        ("prompt_tokens",),
        ("prompt_token_count",),
    )
    output_tokens = _first(
        usage,
        ("output_tokens",),
        ("completion_tokens",),
        ("candidates_token_count",),
    )
    cached = _first(
        usage,
        ("cached_input_tokens",),
        ("cached_content_token_count",),
        ("cache_read_input_tokens",),
        ("prompt_tokens_details", "cached_tokens"),
    )
    images = _first(usage, ("output_images",))
    has_unmapped_positive = any(
        key not in _KNOWN_USAGE_KEYS and _is_positive_number(value)
        for key, value in usage.items()
    )
    token_present = any(item is not None for item in (input_tokens, output_tokens, cached))
    image_present = images is not None
    if token_present and image_present:
        unit_kind = "MIXED"
    elif token_present:
        unit_kind = "TEXT_TOKENS"
    elif image_present:
        unit_kind = "IMAGES"
    else:
        unit_kind = "UNKNOWN"
    if not token_present and not image_present and has_unmapped_positive:
        status = "PARTIAL"
    elif not token_present and not image_present:
        status = "UNKNOWN"
    elif (image_present and not token_present) or (
        input_tokens is not None and output_tokens is not None
    ):
        status = "COMPLETE"
    else:
        status = "PARTIAL"
    if has_unmapped_positive and status == "COMPLETE":
        status = "PARTIAL"
    return {
        "usage_status": status,
        "usage_source": "PROVIDER_REPORTED" if status != "UNKNOWN" else None,
        "unit_kind": unit_kind,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_input_tokens": cached,
        "cache_hit": None if cached is None else cached > 0,
        "output_images": images,
    }


def _set_sqlite_foreign_keys(enabled: bool) -> None:
    connection = op.get_bind()
    if connection.dialect.name != "sqlite":
        return
    # SQLite ignores foreign_keys changes inside a transaction. Alembic's
    # autocommit block makes the setting effective on the physical connection
    # that performs the batch table recreation, then starts a fresh migration
    # transaction so version stamping still remains Alembic-owned.
    with op.get_context().autocommit_block():
        connection.exec_driver_sql(f"PRAGMA foreign_keys={'ON' if enabled else 'OFF'}")
        expected = 1 if enabled else 0
        if connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() != expected:
            raise RuntimeError("could not change SQLite foreign-key mode for usage migration")


def _ensure_postgresql_reconciliation_exclusion() -> None:
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        return
    # Text equality in a GiST exclusion constraint is supplied by btree_gist.
    # The fixed-name extension and DDL contain no user-controlled identifiers.
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    exists = connection.scalar(
        sa.text(
            "SELECT EXISTS ("
            "SELECT 1 FROM pg_constraint "
            "WHERE conname = 'excl_usage_reconciliations_period' "
            "AND conrelid = to_regclass('provider_usage_reconciliations')"
            ")"
        )
    )
    if not exists:
        op.execute(
            "ALTER TABLE provider_usage_reconciliations "
            "ADD CONSTRAINT excl_usage_reconciliations_period "
            "EXCLUDE USING gist ("
            "billing_account_id WITH =, provider WITH =, model_id WITH =, "
            "channel WITH =, tstzrange(period_start, period_end, '[)') WITH &&"
            ")"
        )


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    attempt_columns = {
        column["name"] for column in inspector.get_columns("model_call_attempts")
    }
    if "dispatch_request_id" not in attempt_columns:
        _set_sqlite_foreign_keys(False)
        with op.batch_alter_table("model_call_attempts") as batch_op:
            batch_op.alter_column(
                "job_id",
                existing_type=sa.String(36),
                nullable=True,
            )
            batch_op.alter_column(
                "project_id",
                existing_type=sa.String(36),
                nullable=True,
            )
            batch_op.add_column(sa.Column("dispatch_request_id", sa.String(160)))
            batch_op.add_column(
                sa.Column(
                    "channel",
                    sa.String(16),
                    nullable=False,
                    server_default="HTTP_API",
                )
            )
            batch_op.add_column(sa.Column("probe_id", sa.String(36)))
            batch_op.add_column(sa.Column("chapter_id", sa.String(36)))
            batch_op.add_column(sa.Column("page_id", sa.String(36)))
            batch_op.add_column(sa.Column("panel_id", sa.String(36)))
            batch_op.add_column(sa.Column("candidate_id", sa.String(36)))
            batch_op.add_column(sa.Column("usage_status", sa.String(16)))
            batch_op.add_column(sa.Column("usage_source", sa.String(24)))
            batch_op.add_column(sa.Column("unit_kind", sa.String(16)))
            batch_op.add_column(sa.Column("input_tokens", sa.Numeric(20, 0)))
            batch_op.add_column(sa.Column("output_tokens", sa.Numeric(20, 0)))
            batch_op.add_column(sa.Column("cached_input_tokens", sa.Numeric(20, 0)))
            batch_op.add_column(sa.Column("cache_hit", sa.Boolean()))
            batch_op.add_column(sa.Column("output_images", sa.Numeric(20, 0)))
            batch_op.add_column(sa.Column("output_image_dims", sa.JSON()))
            batch_op.add_column(sa.Column("output_asset_ids", sa.JSON()))
            batch_op.create_unique_constraint(
                "uq_model_call_attempts_dispatch_request_id",
                ["dispatch_request_id"],
            )
            batch_op.create_check_constraint(
                "ck_model_call_attempts_channel",
                "channel IN ('HTTP_API', 'CLI')",
            )
            batch_op.create_check_constraint(
                "ck_model_call_attempts_usage_status",
                "usage_status IS NULL OR usage_status IN ('UNKNOWN', 'PARTIAL', 'COMPLETE')",
            )
            batch_op.create_check_constraint(
                "ck_model_call_attempts_usage_source",
                "usage_source IS NULL OR usage_source IN "
                "('ADAPTER_ESTIMATED', 'PROVIDER_REPORTED', 'OPERATOR_BILLED')",
            )
            batch_op.create_check_constraint(
                "ck_model_call_attempts_unit_kind",
                "unit_kind IS NULL OR unit_kind IN "
                "('TEXT_TOKENS', 'IMAGES', 'MIXED', 'UNKNOWN')",
            )
            for column in (
                "input_tokens",
                "output_tokens",
                "cached_input_tokens",
                "output_images",
            ):
                batch_op.create_check_constraint(
                    f"ck_model_call_attempts_{column}",
                    f"{column} IS NULL OR {column} >= 0",
                )
    existing_indexes = {
        index["name"] for index in sa.inspect(connection).get_indexes("model_call_attempts")
    }
    for name, columns in _ATTEMPT_INDEXES:
        if name not in existing_indexes:
            op.create_index(name, "model_call_attempts", list(columns))

    pricing_columns = {
        column["name"] for column in sa.inspect(connection).get_columns("model_pricing_versions")
    }
    if "cached_input_tokens_per_million" not in pricing_columns:
        with op.batch_alter_table("model_pricing_versions") as batch_op:
            batch_op.add_column(
                sa.Column("cached_input_tokens_per_million", sa.Numeric(20, 8))
            )
            batch_op.create_check_constraint(
                "ck_model_pricing_versions_cached_input_rate",
                "cached_input_tokens_per_million IS NULL "
                "OR cached_input_tokens_per_million >= 0",
            )

    if "provider_usage_reconciliations" not in sa.inspect(connection).get_table_names():
        op.create_table(
            "provider_usage_reconciliations",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("provider", sa.String(120), nullable=False),
            sa.Column("model_id", sa.String(128), nullable=False),
            sa.Column("channel", sa.String(16), nullable=False),
            sa.Column("connection_id", sa.String(36)),
            sa.Column("billing_account_id", sa.String(160), nullable=False),
            sa.Column("import_batch_id", sa.String(160), nullable=False),
            sa.Column("idempotency_key", sa.String(160), nullable=False),
            sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
            sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
            sa.Column("currency", sa.String(3), nullable=False),
            sa.Column("billed_amount", sa.Numeric(20, 8), nullable=False),
            sa.Column("source_note", sa.String(500), nullable=False),
            sa.Column("entered_by", sa.String(120), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "billing_account_id",
                "import_batch_id",
                "idempotency_key",
                name="uq_usage_reconciliations_idempotency",
            ),
            sa.CheckConstraint(
                "channel IN ('HTTP_API', 'CLI')",
                name="ck_usage_reconciliations_channel",
            ),
            sa.CheckConstraint(
                "period_end > period_start",
                name="ck_usage_reconciliations_window",
            ),
            sa.CheckConstraint(
                "billed_amount >= 0",
                name="ck_usage_reconciliations_amount",
            ),
        )
        op.create_index(
            "ix_usage_reconciliations_lookup",
            "provider_usage_reconciliations",
            ["provider", "model_id", "channel", "period_start"],
        )
    _ensure_postgresql_reconciliation_exclusion()

    rows = connection.execute(
        sa.text(
            "SELECT id, usage FROM model_call_attempts "
            "WHERE usage_status IS NULL"
        )
    ).mappings()
    for row in rows:
        normalized = _normalize(row["usage"])
        connection.execute(
            sa.text(
                "UPDATE model_call_attempts SET "
                "usage_status=:usage_status, usage_source=:usage_source, "
                "unit_kind=:unit_kind, input_tokens=:input_tokens, "
                "output_tokens=:output_tokens, cached_input_tokens=:cached_input_tokens, "
                "cache_hit=:cache_hit, output_images=:output_images "
                "WHERE id=:id AND usage_status IS NULL"
            ),
            {"id": row["id"], **normalized},
        )
    _set_sqlite_foreign_keys(True)


def downgrade() -> None:
    connection = op.get_bind()
    attempt_columns = {
        column["name"] for column in sa.inspect(connection).get_columns("model_call_attempts")
    }
    if "dispatch_request_id" in attempt_columns:
        missing_job_count = connection.execute(
            sa.text(
                "SELECT COUNT(*) FROM model_call_attempts "
                "WHERE job_id IS NULL OR project_id IS NULL"
            )
        ).scalar_one()
        if missing_job_count:
            raise RuntimeError(
                "refusing downgrade: archive probe-only usage attempts before "
                "restoring required job ownership"
            )
    _set_sqlite_foreign_keys(False)
    if "provider_usage_reconciliations" in sa.inspect(connection).get_table_names():
        op.drop_index(
            "ix_usage_reconciliations_lookup",
            table_name="provider_usage_reconciliations",
        )
        op.drop_table("provider_usage_reconciliations")

    pricing_columns = {
        column["name"] for column in sa.inspect(connection).get_columns("model_pricing_versions")
    }
    if "cached_input_tokens_per_million" in pricing_columns:
        with op.batch_alter_table("model_pricing_versions") as batch_op:
            batch_op.drop_constraint(
                "ck_model_pricing_versions_cached_input_rate",
                type_="check",
            )
            batch_op.drop_column("cached_input_tokens_per_million")

    existing_indexes = {
        index["name"] for index in sa.inspect(connection).get_indexes("model_call_attempts")
    }
    for name, _columns in reversed(_ATTEMPT_INDEXES):
        if name in existing_indexes:
            op.drop_index(name, table_name="model_call_attempts")

    if "dispatch_request_id" in attempt_columns:
        with op.batch_alter_table("model_call_attempts") as batch_op:
            batch_op.drop_constraint(
                "uq_model_call_attempts_dispatch_request_id", type_="unique"
            )
            for name in (
                "ck_model_call_attempts_channel",
                "ck_model_call_attempts_usage_status",
                "ck_model_call_attempts_usage_source",
                "ck_model_call_attempts_unit_kind",
                "ck_model_call_attempts_input_tokens",
                "ck_model_call_attempts_output_tokens",
                "ck_model_call_attempts_cached_input_tokens",
                "ck_model_call_attempts_output_images",
            ):
                batch_op.drop_constraint(name, type_="check")
            for column in (
                "output_asset_ids",
                "output_image_dims",
                "output_images",
                "cache_hit",
                "cached_input_tokens",
                "output_tokens",
                "input_tokens",
                "unit_kind",
                "usage_source",
                "usage_status",
                "candidate_id",
                "panel_id",
                "page_id",
                "chapter_id",
                "probe_id",
                "channel",
                "dispatch_request_id",
            ):
                batch_op.drop_column(column)
            batch_op.alter_column(
                "project_id",
                existing_type=sa.String(36),
                nullable=False,
            )
            batch_op.alter_column(
                "job_id",
                existing_type=sa.String(36),
                nullable=False,
            )
    _set_sqlite_foreign_keys(True)
