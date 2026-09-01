"""Add immutable, effective-dated model pricing versions.

Revision ID: 20260830_19
Revises: 20260829_18
Create Date: 2026-08-30
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine.reflection import Inspector

revision = "20260830_19"
down_revision = "20260829_18"
branch_labels = None
depends_on = None

_OWNED_COLUMNS = {
    "id": ("string", 36, None, None, False),
    "provider": ("string", 120, None, None, False),
    "model_id": ("string", 128, None, None, False),
    "pricing_version": ("string", 64, None, None, False),
    "currency": ("string", 3, None, None, False),
    "effective_from": ("datetime", None, None, None, False),
    "effective_to": ("datetime", None, None, None, True),
    "input_tokens_per_million": ("numeric", None, 20, 8, True),
    "output_tokens_per_million": ("numeric", None, 20, 8, True),
    "output_image_each": ("numeric", None, 20, 8, True),
    "request_each": ("numeric", None, 20, 8, True),
    "created_at": ("datetime", None, None, None, False),
}
_OWNED_CHECKS = {
    "ck_model_pricing_versions_window",
    "ck_model_pricing_versions_input_rate",
    "ck_model_pricing_versions_output_rate",
    "ck_model_pricing_versions_image_rate",
    "ck_model_pricing_versions_request_rate",
    "ck_model_pricing_versions_has_rate",
}
_KNOWN_LATER_COLUMNS = {
    "cached_input_tokens_per_million": ("numeric", None, 20, 8, True),
}


def _column_family(column_type: sa.types.TypeEngine) -> str:
    if isinstance(column_type, sa.String):
        return "string"
    if isinstance(column_type, sa.DateTime):
        return "datetime"
    if isinstance(column_type, sa.Numeric):
        return "numeric"
    return type(column_type).__name__.lower()


def _has_owned_schema(inspector: Inspector) -> bool:
    columns = {
        column["name"]: (
            _column_family(column["type"]),
            getattr(column["type"], "length", None),
            getattr(column["type"], "precision", None),
            getattr(column["type"], "scale", None),
            bool(column["nullable"]),
        )
        for column in inspector.get_columns("model_pricing_versions")
    }
    if not set(_OWNED_COLUMNS) <= set(columns):
        return False
    if set(columns) - set(_OWNED_COLUMNS) - set(_KNOWN_LATER_COLUMNS):
        return False
    if any(columns[name] != expected for name, expected in _OWNED_COLUMNS.items()):
        return False
    if any(
        columns.get(name) != expected
        for name, expected in _KNOWN_LATER_COLUMNS.items()
        if name in columns
    ):
        return False
    if tuple(
        inspector.get_pk_constraint("model_pricing_versions").get(
            "constrained_columns"
        )
        or ()
    ) != ("id",):
        return False
    unique_constraints = {
        tuple(constraint.get("column_names") or ())
        for constraint in inspector.get_unique_constraints("model_pricing_versions")
    }
    if ("provider", "model_id", "pricing_version") not in unique_constraints:
        return False
    indexes = {
        index["name"]: tuple(index.get("column_names") or ())
        for index in inspector.get_indexes("model_pricing_versions")
        if not index.get("unique")
    }
    if indexes.get("ix_model_pricing_versions_lookup") != (
        "provider",
        "model_id",
        "effective_from",
    ):
        return False
    checks = {
        constraint.get("name")
        for constraint in inspector.get_check_constraints("model_pricing_versions")
    }
    return checks >= _OWNED_CHECKS


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "model_pricing_versions" in inspector.get_table_names():
        if not _has_owned_schema(inspector):
            raise RuntimeError(
                "model_pricing_versions 已存在但结构与本迁移不匹配，请人工处理后再升级"
            )
        return
    op.create_table(
        "model_pricing_versions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("provider", sa.String(length=120), nullable=False),
        sa.Column("model_id", sa.String(length=128), nullable=False),
        sa.Column("pricing_version", sa.String(length=64), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "input_tokens_per_million", sa.Numeric(20, 8), nullable=True
        ),
        sa.Column(
            "output_tokens_per_million", sa.Numeric(20, 8), nullable=True
        ),
        sa.Column("output_image_each", sa.Numeric(20, 8), nullable=True),
        sa.Column("request_each", sa.Numeric(20, 8), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "provider",
            "model_id",
            "pricing_version",
            name="uq_model_pricing_versions_provider_model_version",
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_model_pricing_versions_window",
        ),
        sa.CheckConstraint(
            "input_tokens_per_million IS NULL OR input_tokens_per_million >= 0",
            name="ck_model_pricing_versions_input_rate",
        ),
        sa.CheckConstraint(
            "output_tokens_per_million IS NULL OR output_tokens_per_million >= 0",
            name="ck_model_pricing_versions_output_rate",
        ),
        sa.CheckConstraint(
            "output_image_each IS NULL OR output_image_each >= 0",
            name="ck_model_pricing_versions_image_rate",
        ),
        sa.CheckConstraint(
            "request_each IS NULL OR request_each >= 0",
            name="ck_model_pricing_versions_request_rate",
        ),
        sa.CheckConstraint(
            "input_tokens_per_million IS NOT NULL "
            "OR output_tokens_per_million IS NOT NULL "
            "OR output_image_each IS NOT NULL "
            "OR request_each IS NOT NULL",
            name="ck_model_pricing_versions_has_rate",
        ),
    )
    op.create_index(
        "ix_model_pricing_versions_lookup",
        "model_pricing_versions",
        ["provider", "model_id", "effective_from"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_model_pricing_versions_lookup", table_name="model_pricing_versions"
    )
    op.drop_table("model_pricing_versions")
