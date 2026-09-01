"""Add first-class scene assets, variants and their reference bindings.

Purely additive tables plus two nullable foreign keys on ``scenes``. No data
rewrites: ``scenes.location`` text stays untouched and no backfill occurs;
legacy scenes keep NULL asset ids and fall back to location text at consumption
time. Downgrade refuses to drop anything while scene bindings or rows exist.

Revision ID: 20260901_24
Revises: 20260901_23
Create Date: 2026-09-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine.reflection import Inspector

revision = "20260901_24"
down_revision = "20260901_23"
branch_labels = None
depends_on = None

_SCENE_ASSET_COLUMNS = {
    "id",
    "project_id",
    "name",
    "normalized_name",
    "description",
    "location_hint",
    "structured",
    "status",
    "locked_fields",
    "deleted_at",
    "created_at",
    "updated_at",
    "version",
}
_SCENE_ASSET_REFERENCE_COLUMNS = {
    "id",
    "scene_asset_id",
    "asset_id",
    "role",
    "is_canonical",
    "created_at",
}
_SCENE_ASSET_VARIANT_COLUMNS = {
    "id",
    "scene_asset_id",
    "name",
    "structured_overrides",
    "is_canonical",
    "deleted_at",
    "created_at",
    "updated_at",
    "version",
}
_SCENE_ASSET_VARIANT_REFERENCE_COLUMNS = {
    "id",
    "variant_id",
    "asset_id",
    "role",
    "sort_order",
    "created_at",
}
_OWNED_INDEXES = {
    "scene_assets": {
        "uq_scene_assets_project_active_name",
        "ix_scene_assets_project_deleted_created",
    },
    "scene_asset_references": {"ix_scene_asset_references_scene_asset_id"},
    "scene_asset_variants": {
        "ix_scene_asset_variants_asset_canonical",
        "uq_scene_asset_variants_asset_canonical",
    },
    "scene_asset_variant_references": {"ix_scene_asset_variant_references_variant_id"},
}


def _has_owned_schema(inspector: Inspector, table: str, owned_columns: set[str]) -> bool:
    if table not in inspector.get_table_names():
        return False
    columns = {column["name"] for column in inspector.get_columns(table)}
    if columns != owned_columns:
        return False
    index_names = {index["name"] for index in inspector.get_indexes(table)}
    return _OWNED_INDEXES[table] <= index_names


def _ensure_owned_or_create(inspector: Inspector) -> None:
    for table, owned_columns in (
        ("scene_assets", _SCENE_ASSET_COLUMNS),
        ("scene_asset_references", _SCENE_ASSET_REFERENCE_COLUMNS),
        ("scene_asset_variants", _SCENE_ASSET_VARIANT_COLUMNS),
        ("scene_asset_variant_references", _SCENE_ASSET_VARIANT_REFERENCE_COLUMNS),
    ):
        if table in inspector.get_table_names() and not _has_owned_schema(
            inspector, table, owned_columns
        ):
            raise RuntimeError(
                f"{table} 已存在但结构与本迁移不匹配，请人工处理后再次升级"
            )


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    _ensure_owned_or_create(inspector)
    if "scene_assets" not in inspector.get_table_names():
        op.create_table(
            "scene_assets",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "project_id",
                sa.String(length=36),
                sa.ForeignKey("projects.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("normalized_name", sa.String(length=120), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("location_hint", sa.String(length=200), nullable=False),
            sa.Column("structured", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("locked_fields", sa.JSON(), nullable=False),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
        )
        op.create_index(
            "ix_scene_assets_project_deleted_created",
            "scene_assets",
            ["project_id", "deleted_at", "created_at"],
        )
        op.create_index(
            "uq_scene_assets_project_active_name",
            "scene_assets",
            ["project_id", "normalized_name"],
            unique=True,
            postgresql_where=sa.text("deleted_at IS NULL"),
            sqlite_where=sa.text("deleted_at IS NULL"),
        )
    if "scene_asset_references" not in inspector.get_table_names():
        op.create_table(
            "scene_asset_references",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "scene_asset_id",
                sa.String(length=36),
                sa.ForeignKey("scene_assets.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "asset_id",
                sa.String(length=36),
                sa.ForeignKey("assets.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column("role", sa.String(length=32), nullable=False),
            sa.Column("is_canonical", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "scene_asset_id",
                "asset_id",
                "role",
                name="uq_scene_asset_reference_asset_role",
            ),
        )
        op.create_index(
            "ix_scene_asset_references_scene_asset_id",
            "scene_asset_references",
            ["scene_asset_id"],
        )
        op.create_index(
            "ix_scene_asset_references_asset_id",
            "scene_asset_references",
            ["asset_id"],
        )
    if "scene_asset_variants" not in inspector.get_table_names():
        op.create_table(
            "scene_asset_variants",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "scene_asset_id",
                sa.String(length=36),
                sa.ForeignKey("scene_assets.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("structured_overrides", sa.JSON(), nullable=False),
            sa.Column("is_canonical", sa.Boolean(), nullable=False),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
        )
        op.create_index(
            "ix_scene_asset_variants_asset_canonical",
            "scene_asset_variants",
            ["scene_asset_id", "is_canonical"],
        )
        op.create_index(
            "uq_scene_asset_variants_asset_canonical",
            "scene_asset_variants",
            ["scene_asset_id"],
            unique=True,
            postgresql_where=sa.text("is_canonical AND deleted_at IS NULL"),
            sqlite_where=sa.text("is_canonical AND deleted_at IS NULL"),
        )
    if "scene_asset_variant_references" not in inspector.get_table_names():
        op.create_table(
            "scene_asset_variant_references",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "variant_id",
                sa.String(length=36),
                sa.ForeignKey("scene_asset_variants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "asset_id",
                sa.String(length=36),
                sa.ForeignKey("assets.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column("role", sa.String(length=32), nullable=False),
            sa.Column("sort_order", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "variant_id",
                "asset_id",
                "role",
                name="uq_scene_asset_variant_reference_asset_role",
            ),
        )
        op.create_index(
            "ix_scene_asset_variant_references_variant_id",
            "scene_asset_variant_references",
            ["variant_id"],
        )
        op.create_index(
            "ix_scene_asset_variant_references_asset_id",
            "scene_asset_variant_references",
            ["asset_id"],
        )
    scene_columns = {column["name"] for column in inspector.get_columns("scenes")}
    if "scene_asset_id" not in scene_columns:
        # SQLite cannot ALTER through Alembic's constraint path; raw ADD COLUMN
        # with a nullable FK works identically on SQLite and PostgreSQL.
        op.execute(
            sa.text(
                "ALTER TABLE scenes ADD COLUMN scene_asset_id VARCHAR(36) "
                "REFERENCES scene_assets(id) ON DELETE SET NULL"
            )
        )
        op.create_index("ix_scenes_scene_asset_id", "scenes", ["scene_asset_id"])
    if "scene_asset_variant_id" not in scene_columns:
        op.execute(
            sa.text(
                "ALTER TABLE scenes ADD COLUMN scene_asset_variant_id VARCHAR(36) "
                "REFERENCES scene_asset_variants(id) ON DELETE SET NULL"
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    active_bindings = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM scenes "
            "WHERE scene_asset_id IS NOT NULL OR scene_asset_variant_id IS NOT NULL"
        )
    ).scalar_one()
    if active_bindings:
        raise RuntimeError(
            "refusing downgrade: scenes still reference scene assets "
            "(unbind before removing the schema)"
        )
    for table in (
        "scene_assets",
        "scene_asset_references",
        "scene_asset_variants",
        "scene_asset_variant_references",
    ):
        count = bind.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
        if count:
            raise RuntimeError(
                f"refusing downgrade: {table} rows must be removed before dropping the table"
            )
    bind.execute(sa.text("DROP INDEX IF EXISTS ix_scenes_scene_asset_id"))
    bind.execute(sa.text("ALTER TABLE scenes DROP COLUMN scene_asset_id"))
    bind.execute(sa.text("ALTER TABLE scenes DROP COLUMN scene_asset_variant_id"))
    op.drop_table("scene_asset_variant_references")
    op.drop_table("scene_asset_variants")
    op.drop_table("scene_asset_references")
    op.drop_table("scene_assets")
