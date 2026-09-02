"""Add character model packages, versions and their relation matrices.

Purely additive tables plus a backfill that only INSERTs new rows: every
existing Character gets a compatible ACTIVE package with a V1 DRAFT snapshot
(published_version_id NULL, never published). No existing row is rewritten:
Character/CharacterReference/Outfit/StyleProfile/Asset/PageCandidate/
GenerationRecord stay byte-identical, and no image file is copied. Downgrade
refuses whenever a package is no longer in its migration-created shape.

The packages->versions pointer is a circular reference; PostgreSQL creates the
constraint after both tables exist while SQLite inlines the forward reference.

Revision ID: 20260902_25
Revises: 20260901_24
Create Date: 2026-09-02
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine.reflection import Inspector

revision = "20260902_25"
down_revision = "20260901_24"
branch_labels = None
depends_on = None

_PACKAGE_COLUMNS = {
    "id",
    "character_id",
    "project_id",
    "identity_spec",
    "visual_spec",
    "negative_constraints",
    "published_version_id",
    "status",
    "created_at",
    "updated_at",
    "version",
}
_VERSION_COLUMNS = {
    "id",
    "package_id",
    "version_number",
    "status",
    "spec_snapshot",
    "derived_from_version_id",
    "published_at",
    "created_at",
    "updated_at",
    "version",
}
_REFERENCE_COLUMNS = {
    "id",
    "version_id",
    "asset_id",
    "role",
    "label",
    "sort_order",
    "created_at",
}
_OUTFIT_COLUMNS = {
    "id",
    "version_id",
    "outfit_id",
    "is_default",
    "sort_order",
    "created_at",
}
_OWNED_INDEXES = {
    "character_model_packages": {
        "uq_character_model_packages_character",
        "ix_character_model_packages_project_status_created",
    },
    "character_model_package_versions": {
        "uq_character_model_package_versions_number",
        "ix_character_model_package_versions_package_status",
        "uq_character_model_package_versions_one_draft",
    },
    "character_model_package_version_references": {
        "ix_character_model_package_version_references_version_id",
        "ix_character_model_package_version_references_asset_id",
    },
    "character_model_package_version_outfits": {
        "ix_character_model_package_version_outfits_version_id",
        "ix_character_model_package_version_outfits_outfit_id",
    },
}
_CORE_ROLES = {"cover", "front", "side", "back", "three_quarter"}
_LABELED_ROLES = {"expression", "pose", "extra"}


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
        ("character_model_packages", _PACKAGE_COLUMNS),
        ("character_model_package_versions", _VERSION_COLUMNS),
        ("character_model_package_version_references", _REFERENCE_COLUMNS),
        ("character_model_package_version_outfits", _OUTFIT_COLUMNS),
    ):
        if table in inspector.get_table_names() and not _has_owned_schema(
            inspector, table, owned_columns
        ):
            raise RuntimeError(
                f"{table} 已存在但结构与本迁移不匹配，请人工处理后再次升级"
            )


def _create_package_indexes() -> None:
    op.create_index(
        "uq_character_model_packages_character",
        "character_model_packages",
        ["character_id"],
        unique=True,
    )
    op.create_index(
        "ix_character_model_packages_project_status_created",
        "character_model_packages",
        ["project_id", "status", "created_at"],
    )


def _create_version_indexes() -> None:
    op.create_index(
        "uq_character_model_package_versions_number",
        "character_model_package_versions",
        ["package_id", "version_number"],
        unique=True,
    )
    op.create_index(
        "ix_character_model_package_versions_package_status",
        "character_model_package_versions",
        ["package_id", "status"],
    )
    op.create_index(
        "uq_character_model_package_versions_one_draft",
        "character_model_package_versions",
        ["package_id"],
        unique=True,
        postgresql_where=sa.text("status = 'DRAFT'"),
        sqlite_where=sa.text("status = 'DRAFT'"),
    )


def _angle_to_role_label(angle: str) -> tuple[str, str]:
    """Deterministic contract §6.1 mapping from CharacterReference.angle.

    Stripped-lowercase in {front,side,back,three_quarter,cover} maps to the
    same role with an empty label; expression/pose map to the same role with
    the built-in ``unspecified`` label; everything else lands in ``extra`` and
    keeps its original text (or ``unspecified``) as the label.
    """

    normalized = angle.strip().lower()
    if normalized in _CORE_ROLES:
        return normalized, ""
    if normalized in {"expression", "pose"}:
        return normalized, "unspecified"
    return "extra", normalized or (angle.strip() or "unspecified")


def _backfill_packages(bind, now) -> None:
    """Backfill one compatible ACTIVE package + V1 DRAFT per existing Character.

    Only new rows are inserted (contract §6.1). The V1 relations copy live
    CharacterReference/Outfit bindings with a deterministic angle mapping; the
    slot collision rule appends a ``-{n}`` suffix so the backfill can never
    violate the ``(version_id, role, label)`` uniqueness constraint.
    """

    characters = bind.execute(
        sa.text("SELECT id, project_id FROM characters ORDER BY created_at, id")
    ).mappings()

    package_rows = []
    version_rows = []
    packed_packages = []
    for character in characters:
        import uuid

        package_id = str(uuid.uuid4())
        version_id = str(uuid.uuid4())
        package_rows.append(
            {
                "id": package_id,
                "character_id": character["id"],
                "project_id": character["project_id"],
                "identity_spec": json.dumps({}),
                "visual_spec": json.dumps({}),
                "negative_constraints": json.dumps([]),
                "published_version_id": None,
                "status": "ACTIVE",
                "created_at": now,
                "updated_at": now,
                "version": 1,
            }
        )
        version_rows.append(
            {
                "id": version_id,
                "package_id": package_id,
                "version_number": 1,
                "status": "DRAFT",
                "spec_snapshot": json.dumps(
                    {
                        "identity_spec": {},
                        "visual_spec": {},
                        "negative_constraints": [],
                        "frozen_from": "migration",
                    }
                ),
                "derived_from_version_id": None,
                "published_at": None,
                "created_at": now,
                "updated_at": now,
                "version": 1,
            }
        )
        packed_packages.append((package_id, version_id, character["id"]))

    bind.execute(
        sa.text(
            "INSERT INTO character_model_packages ("
            "id, character_id, project_id, identity_spec, visual_spec, "
            "negative_constraints, published_version_id, status, created_at, "
            "updated_at, version"
            ") VALUES ("
            ":id, :character_id, :project_id, :identity_spec, :visual_spec, "
            ":negative_constraints, :published_version_id, :status, :created_at, "
            ":updated_at, :version"
            ")"
        ),
        package_rows,
    )
    bind.execute(
        sa.text(
            "INSERT INTO character_model_package_versions ("
            "id, package_id, version_number, status, spec_snapshot, "
            "derived_from_version_id, published_at, created_at, updated_at, "
            "version"
            ") VALUES ("
            ":id, :package_id, :version_number, :status, :spec_snapshot, "
            ":derived_from_version_id, :published_at, :created_at, :updated_at, "
            ":version"
            ")"
        ),
        version_rows,
    )

    for package_id, version_id, character_id in packed_packages:
        _backfill_version_relations(bind, package_id, version_id, character_id, now)


def _backfill_version_relations(bind, package_id, version_id, character_id, now) -> None:
    import uuid

    reference_rows = bind.execute(
        sa.text(
            "SELECT cr.asset_id, cr.angle, cr.created_at, cr.id "
            "FROM character_references cr "
            "JOIN assets a ON a.id = cr.asset_id "
            "WHERE cr.character_id = :character_id AND a.deleted_at IS NULL "
            "ORDER BY cr.created_at, cr.id"
        ),
        {"character_id": character_id},
    ).mappings()

    occupied_slots: dict[tuple[str, str], bool] = {}
    inserts = []
    for reference in reference_rows:
        role, label = _angle_to_role_label(reference["angle"])
        if (role, label) in occupied_slots:
            base = reference["angle"].strip() or "unspecified"
            if role in _CORE_ROLES:
                # Core roles forbid labels (schema CHECK); a duplicate core
                # slot degrades to ``extra`` with its original text label.
                role = "extra"
                label = base
            suffix = 2
            while (role, label) in occupied_slots:
                label = f"{base}-{suffix}"
                suffix += 1
        occupied_slots[(role, label)] = True
        inserts.append(
            {
                "id": str(uuid.uuid4()),
                "version_id": version_id,
                "asset_id": reference["asset_id"],
                "role": role,
                "label": label,
                "sort_order": 0,
                "created_at": now,
            }
        )
    if inserts:
        bind.execute(
            sa.text(
                "INSERT INTO character_model_package_version_references ("
                "id, version_id, asset_id, role, label, sort_order, created_at"
                ") VALUES ("
                ":id, :version_id, :asset_id, :role, :label, :sort_order, :created_at"
                ")"
            ),
            inserts,
        )

    outfit_rows = bind.execute(
        sa.text(
            "SELECT id FROM outfits WHERE character_id = :character_id "
            "ORDER BY created_at, id"
        ),
        {"character_id": character_id},
    ).mappings()
    outfit_inserts = [
        {
            "id": str(uuid.uuid4()),
            "version_id": version_id,
            "outfit_id": outfit["id"],
            "is_default": False,
            "sort_order": index,
            "created_at": now,
        }
        for index, outfit in enumerate(outfit_rows)
    ]
    if outfit_inserts:
        bind.execute(
            sa.text(
                "INSERT INTO character_model_package_version_outfits ("
                "id, version_id, outfit_id, is_default, sort_order, created_at"
                ") VALUES ("
                ":id, :version_id, :outfit_id, :is_default, :sort_order, :created_at"
                ")"
            ),
            outfit_inserts,
        )


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    _ensure_owned_or_create(inspector)
    dialect = op.get_bind().dialect.name
    if "character_model_packages" not in inspector.get_table_names():
        package_columns = [
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "character_id",
                sa.String(length=36),
                sa.ForeignKey("characters.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "project_id",
                sa.String(length=36),
                sa.ForeignKey("projects.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("identity_spec", sa.JSON(), nullable=False),
            sa.Column("visual_spec", sa.JSON(), nullable=False),
            sa.Column("negative_constraints", sa.JSON(), nullable=False),
        ]
        if dialect == "sqlite":
            # SQLite validates foreign keys at DML time, so a forward reference
            # to the version table created later in this transaction is legal.
            package_columns.append(
                sa.Column(
                    "published_version_id",
                    sa.String(length=36),
                    sa.ForeignKey(
                        "character_model_package_versions.id", ondelete="SET NULL"
                    ),
                    nullable=True,
                )
            )
        else:
            package_columns.append(
                sa.Column("published_version_id", sa.String(length=36), nullable=True)
            )
        package_columns.extend(
            [
                sa.Column("status", sa.String(length=16), nullable=False),
                sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
                sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
                sa.Column("version", sa.Integer(), nullable=False),
            ]
        )
        op.create_table("character_model_packages", *package_columns)
        _create_package_indexes()
    if "character_model_package_versions" not in inspector.get_table_names():
        op.create_table(
            "character_model_package_versions",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "package_id",
                sa.String(length=36),
                sa.ForeignKey("character_model_packages.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("version_number", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("spec_snapshot", sa.JSON(), nullable=False),
            sa.Column(
                "derived_from_version_id",
                sa.String(length=36),
                sa.ForeignKey(
                    "character_model_package_versions.id", ondelete="SET NULL"
                ),
                nullable=True,
            ),
            sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
        )
        _create_version_indexes()
    if dialect == "postgresql":
        op.execute(
            sa.text(
                "ALTER TABLE character_model_packages "
                "ADD CONSTRAINT fk_character_model_packages_published_version "
                "FOREIGN KEY (published_version_id) "
                "REFERENCES character_model_package_versions(id) ON DELETE SET NULL"
            )
        )
    if "character_model_package_version_references" not in inspector.get_table_names():
        op.create_table(
            "character_model_package_version_references",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "version_id",
                sa.String(length=36),
                sa.ForeignKey(
                    "character_model_package_versions.id", ondelete="CASCADE"
                ),
                nullable=False,
            ),
            sa.Column(
                "asset_id",
                sa.String(length=36),
                sa.ForeignKey("assets.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column("role", sa.String(length=32), nullable=False),
            sa.Column("label", sa.String(length=48), nullable=False),
            sa.Column("sort_order", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint(
                "(role IN ('cover','front','side','back','three_quarter') AND label = '') "
                "OR (role IN ('expression','pose','extra') AND label <> '')",
                name="ck_character_model_package_version_reference_role_label",
            ),
        )
        op.create_index(
            "uq_character_model_package_version_reference_slot",
            "character_model_package_version_references",
            ["version_id", "role", "label"],
            unique=True,
        )
        op.create_index(
            "ix_character_model_package_version_references_version_id",
            "character_model_package_version_references",
            ["version_id"],
        )
        op.create_index(
            "ix_character_model_package_version_references_asset_id",
            "character_model_package_version_references",
            ["asset_id"],
        )
    if "character_model_package_version_outfits" not in inspector.get_table_names():
        op.create_table(
            "character_model_package_version_outfits",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "version_id",
                sa.String(length=36),
                sa.ForeignKey(
                    "character_model_package_versions.id", ondelete="CASCADE"
                ),
                nullable=False,
            ),
            sa.Column(
                "outfit_id",
                sa.String(length=36),
                sa.ForeignKey("outfits.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column("is_default", sa.Boolean(), nullable=False),
            sa.Column("sort_order", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index(
            "uq_character_model_package_version_outfit",
            "character_model_package_version_outfits",
            ["version_id", "outfit_id"],
            unique=True,
        )
        op.create_index(
            "ix_character_model_package_version_outfits_version_id",
            "character_model_package_version_outfits",
            ["version_id"],
        )
        op.create_index(
            "ix_character_model_package_version_outfits_outfit_id",
            "character_model_package_version_outfits",
            ["outfit_id"],
        )
        op.create_index(
            "uq_character_model_package_version_outfit_default",
            "character_model_package_version_outfits",
            ["version_id"],
            unique=True,
            postgresql_where=sa.text("is_default"),
            sqlite_where=sa.text("is_default"),
        )

    from datetime import UTC, datetime

    bind = op.get_bind()
    character_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM characters")
    ).scalar_one()
    if character_count:
        _backfill_packages(bind, datetime.now(UTC))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM character_model_package_versions "
            "WHERE status <> 'DRAFT'"
        )
    ).scalar_one():
        raise RuntimeError(
            "refusing downgrade: package versions have been published or used in production"
        )
    if bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM character_model_packages "
            "WHERE published_version_id IS NOT NULL"
        )
    ).scalar_one():
        raise RuntimeError("refusing downgrade: packages still have a published version")
    if bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM character_model_packages "
            "WHERE id IN ("
            "SELECT package_id FROM character_model_package_versions "
            "GROUP BY package_id HAVING COUNT(*) > 1)"
        )
    ).scalar_one():
        raise RuntimeError("refusing downgrade: packages have more than one version")
    if bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM character_model_packages WHERE status = 'ARCHIVED'"
        )
    ).scalar_one():
        raise RuntimeError("refusing downgrade: archived packages exist")

    if bind.dialect.name == "postgresql":
        bind.execute(
            sa.text(
                "ALTER TABLE character_model_packages "
                "DROP CONSTRAINT fk_character_model_packages_published_version"
            )
        )
    bind.execute(sa.text("DROP TABLE IF EXISTS character_model_package_version_references"))
    bind.execute(sa.text("DROP TABLE IF EXISTS character_model_package_version_outfits"))
    bind.execute(sa.text("DROP TABLE IF EXISTS character_model_package_versions"))
    bind.execute(sa.text("DROP TABLE IF EXISTS character_model_packages"))
