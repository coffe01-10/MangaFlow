"""schema parity fixes between app.models and migration history

Revision ID: 20260906_30
Revises: 20260904_29
Create Date: 2026-09-06

The offline test suite builds its schema with ``Base.metadata.create_all`` while
every deployed database was built by ``alembic upgrade head``; drift between the
two paths is invisible to those tests. ``tests/test_migration_parity.py`` now
compares the two schemas on SQLite, and this migration closes the drift that
comparison (and the lead review) found on the migration side. Model-side parity
edits live in this change's ``models.py`` (dropped redundant index flags and the
named-unique-index declarations).

Fixes, each guarded and idempotent per database shape:

1. ``generation_jobs.archived_at``: migration 20260715_08 created it as
   ``TIMESTAMP WITHOUT TIME ZONE`` while the model declares
   ``DateTime(timezone=True)``. PostgreSQL only: ``ALTER COLUMN ... TYPE
   timestamptz USING archived_at AT TIME ZONE 'UTC'``. ``'UTC'`` is chosen
   deliberately: the application writes aware UTC datetimes
   (``app.models.utcnow`` -> ``datetime.now(UTC)``), and PostgreSQL stores the
   UTC wall-clock fields when an offset-bearing literal lands in a
   without-time-zone column, so re-interpreting the stored value as UTC restores
   the intended instants (assuming the standard UTC ``TimeZone`` deployment).
   SQLite needs no action: both ``DateTime()`` and ``DateTime(timezone=True)``
   compile to the same ``TIMESTAMP`` storage, verified by the parity test.

2. ``scene_assets.status``: migration 20260901_24 created ``VARCHAR(32)`` while
   the model (and every other ``AssetStatus`` column since the initial
   migration: assets/characters/outfits) uses a native enum type. Direction
   chosen: convert the column to the existing ``assetstatus`` enum
   (``ALTER COLUMN ... USING status::assetstatus``), following the repo
   convention that migrations are corrected toward the models (cf. 20260904_29)
   and keeping SQLAlchemy's bind-time enum validation. The cast cannot invent
   data: the application only ever writes ``AssetStatus`` names, so any failure
   means manual corruption and should stop loudly. Trade-off accepted: one
   ``ALTER COLUMN`` table rewrite on a table created in 20260901_24. SQLite is a
   verified no-op (TEXT affinity; the VARCHAR length/check-constraint difference
   is advisory only and normalized in the parity test).

3. Migration-side artifacts models never declared, dropped so migrated
   databases match ``create_all``:
   - ``ix_ai_models_legacy_alias`` (redundant next to the UNIQUE constraint
     migration 20260718_15 also created; its backing index serves lookups).
   - ``ix_provider_profiles_preset_key`` (same pattern, same migration).
   - legacy ``UNIQUE (character_id, asset_id)`` on ``character_references``
     from migration 20260714_01, made redundant by migration 20260717_13's
     unique index on ``asset_id``. PostgreSQL drops the definition-matched
     constraint; SQLite cannot drop an unnamed inline constraint, so the table
     is rebuilt through batch mode with a ``copy_from`` matching the models
     schema (the 20260904_29 approach), with foreign_keys toggled off/on and a
     ``PRAGMA foreign_key_check`` guard afterwards.

PostgreSQL upgrade/downgrade is NOT RUN in this environment (no live
PostgreSQL); SQLite round-trip is covered by the migration and parity tests.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260906_30"
down_revision: str | None = "20260904_29"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ASSET_STATUS_ENUM = sa.Enum(
    "UPLOADED",
    "ANALYZED",
    "GENERATED",
    "NEEDS_CONFIRMATION",
    "CANONICAL",
    "ARCHIVED",
    name="assetstatus",
)


def _set_sqlite_foreign_keys(enabled: bool) -> None:
    connection = op.get_bind()
    if connection.dialect.name != "sqlite":
        return
    # Same treatment as migrations 20260901_23/20260904_29: SQLite ignores
    # foreign_keys changes inside a transaction, so toggle it on the physical
    # connection through an autocommit block around the batch table rebuild.
    with op.get_context().autocommit_block():
        connection.exec_driver_sql(f"PRAGMA foreign_keys={'ON' if enabled else 'OFF'}")
        expected = 1 if enabled else 0
        if connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() != expected:
            raise RuntimeError("could not change SQLite foreign-key mode")


def _character_references_target(include_legacy_composite: bool) -> sa.Table:
    """Authoritative character_references schema (models.py at this revision)."""

    metadata = sa.MetaData()
    args: list = [
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("character_id", sa.String(length=36), nullable=False),
        sa.Column("asset_id", sa.String(length=36), nullable=False),
        sa.Column("angle", sa.String(length=32), nullable=False),
        sa.Column("is_canonical", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["character_id"], ["characters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="RESTRICT"),
        sa.Index("uq_character_reference_asset", "asset_id", unique=True),
        sa.Index("ix_character_references_character_id", "character_id"),
        sa.Index("ix_character_references_asset_id", "asset_id"),
    ]
    if include_legacy_composite:
        args.append(sa.UniqueConstraint("character_id", "asset_id"))
    return sa.Table("character_references", metadata, *args)


def _index_exists(table: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(
        index["name"] == index_name for index in inspector.get_indexes(table) if index["name"]
    )


def _column_type(table: str, column_name: str):
    bind = op.get_bind()
    columns = sa.inspect(bind).get_columns(table)
    for column in columns:
        if column["name"] == column_name:
            return column["type"]
    raise RuntimeError(f"column {table}.{column_name} not found")


def _drop_index_if_present(table: str, index_name: str) -> None:
    if _index_exists(table, index_name):
        op.drop_index(index_name, table_name=table)


def _create_index_if_missing(table: str, index_name: str, columns: list[str]) -> None:
    if not _index_exists(table, index_name):
        op.create_index(index_name, table, columns, unique=False)


def _composite_unique_constraint_names(bind, table: str, columns: list[str]) -> list[str]:
    inspector = sa.inspect(bind)
    names: list[str] = []
    for constraint in inspector.get_unique_constraints(table):
        if list(constraint.get("column_names") or []) == columns:
            name = constraint.get("name")
            if name:
                names.append(name)
    return names


def _sqlite_has_composite_unique(table: str, columns: list[str]) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(
        list(constraint.get("column_names") or []) == columns
        for constraint in inspector.get_unique_constraints(table)
    )


def _fix_archived_at_timezone() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite: DateTime() and DateTime(timezone=True) both compile to
        # TIMESTAMP; nothing to change (see migration docstring).
        return
    archived_at = _column_type("generation_jobs", "archived_at")
    if getattr(archived_at, "timezone", False):
        return
    bind.execute(
        sa.text(
            "ALTER TABLE generation_jobs ALTER COLUMN archived_at TYPE "
            "TIMESTAMP WITH TIME ZONE USING archived_at AT TIME ZONE 'UTC'"
        )
    )


def _fix_scene_asset_status_enum() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite: TEXT affinity both before and after; nothing to change.
        return
    status = _column_type("scene_assets", "status")
    if getattr(status, "enum_name", None) is not None:
        return
    _ASSET_STATUS_ENUM.create(bind, checkfirst=True)
    bind.execute(
        sa.text(
            "ALTER TABLE scene_assets ALTER COLUMN status TYPE assetstatus "
            "USING status::assetstatus"
        )
    )


def _drop_legacy_character_references_composite_unique() -> None:
    bind = op.get_bind()
    columns = ["character_id", "asset_id"]
    if bind.dialect.name == "postgresql":
        for name in _composite_unique_constraint_names(bind, "character_references", columns):
            op.drop_constraint(name, "character_references", type_="unique")
        return
    if not _sqlite_has_composite_unique("character_references", columns):
        return
    _set_sqlite_foreign_keys(False)
    try:
        with op.batch_alter_table(
            "character_references",
            copy_from=_character_references_target(include_legacy_composite=False),
            recreate="always",
        ):
            pass
    finally:
        _set_sqlite_foreign_keys(True)
    violations = bind.exec_driver_sql(
        "PRAGMA foreign_key_check(character_references)"
    ).fetchall()
    if violations:
        raise RuntimeError(
            f"foreign_key_check failed after character_references rebuild: {violations[:3]}"
        )


def upgrade() -> None:
    _fix_archived_at_timezone()
    _fix_scene_asset_status_enum()
    _drop_index_if_present("ai_models", "ix_ai_models_legacy_alias")
    _drop_index_if_present("provider_profiles", "ix_provider_profiles_preset_key")
    _drop_legacy_character_references_composite_unique()


def _restore_legacy_character_references_composite_unique() -> None:
    bind = op.get_bind()
    columns = ["character_id", "asset_id"]
    if bind.dialect.name == "postgresql":
        if not _composite_unique_constraint_names(bind, "character_references", columns):
            op.create_unique_constraint(
                "character_references_character_id_asset_id_key",
                "character_references",
                columns,
            )
        return
    if _sqlite_has_composite_unique("character_references", columns):
        return
    _set_sqlite_foreign_keys(False)
    try:
        with op.batch_alter_table(
            "character_references",
            copy_from=_character_references_target(include_legacy_composite=True),
            recreate="always",
        ):
            pass
    finally:
        _set_sqlite_foreign_keys(True)
    violations = bind.exec_driver_sql(
        "PRAGMA foreign_key_check(character_references)"
    ).fetchall()
    if violations:
        raise RuntimeError(
            "foreign_key_check failed after character_references downgrade rebuild: "
            f"{violations[:3]}"
        )


def _restore_archived_at_naive() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    archived_at = _column_type("generation_jobs", "archived_at")
    if not getattr(archived_at, "timezone", False):
        return
    bind.execute(
        sa.text(
            "ALTER TABLE generation_jobs ALTER COLUMN archived_at TYPE "
            "TIMESTAMP WITHOUT TIME ZONE USING archived_at AT TIME ZONE 'UTC'"
        )
    )


def _restore_scene_asset_status_varchar() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    status = _column_type("scene_assets", "status")
    if getattr(status, "enum_name", None) is None:
        return
    bind.execute(
        sa.text(
            "ALTER TABLE scene_assets ALTER COLUMN status TYPE VARCHAR(32) "
            "USING status::text"
        )
    )


def downgrade() -> None:
    _restore_legacy_character_references_composite_unique()
    _create_index_if_missing(
        "provider_profiles", "ix_provider_profiles_preset_key", ["preset_key"]
    )
    _create_index_if_missing("ai_models", "ix_ai_models_legacy_alias", ["legacy_alias"])
    _restore_archived_at_naive()
    _restore_scene_asset_status_varchar()
