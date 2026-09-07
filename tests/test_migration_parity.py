"""Alembic-head vs create_all schema parity guard.

The offline suite builds databases with ``Base.metadata.create_all`` while
deployed databases come from ``alembic upgrade head``; any drift between the
two paths is invisible to normal tests. This module builds one SQLite database
per path in a tmp directory and asserts the reflected schemas are equivalent.

Comparison and normalization rules (SQLite on both sides, so dialect quirks
cancel out; anything normalized here is a documented, deliberate decision):

1. ``alembic_version`` exists only on the migrated side and is excluded.
2. Columns: name, nullable, and a type signature are compared. For columns the
   models declare as ``sa.Enum``, SQLite compiles create_all to
   ``VARCHAR(max_name_length) (+ unnamed CHECK)`` while migrations used
   hand-picked ``sa.String`` lengths (e.g. manga_pages.status 18 vs 19,
   scene_assets.status 18 vs 32), so the VARCHAR length is stripped for those
   columns only. All other types compare exactly, including lengths. SQLite
   cannot distinguish ``DateTime`` timezone-ness (both compile to TIMESTAMP),
   so PostgreSQL TIMESTAMPTZ parity for generation_jobs.archived_at is owned by
   migration 20260906_30 and verified on live PostgreSQL, not here.
3. Indexes: name, column order, and uniqueness are compared. Partial-index
   WHERE clauses are compared by compiled text; the inspector returns TextClause
   objects whose ``str()`` contains object ids, so compiling is the only
   content-based comparison.
4. Unique constraints: compared by sorted column tuples. Constraint names are
   auto-generated per dialect/DDL form and are not meaningful for parity.
5. Foreign keys: compared through ``PRAGMA foreign_key_list`` rather than
   ``sqlalchemy.inspect``. SQLAlchemy 2.0.51's SQLite inspector drops the
   ondelete option for foreign keys that ALTER TABLE ADD COLUMN attached
   inline (migrated scenes.scene_asset_id reports ``options={}`` while the
   pragma correctly reports ``SET NULL``), so the pragma is the source of truth.
6. Named CHECK constraints are compared by name plus a normalized rendering
   of the constraint's SQL text (SQLite reflects the DDL text on both paths).
   Runs of whitespace are collapsed and the text lowercased, because the same
   expression may be phrased with different spacing or keyword/identifier
   case between models and migrations; quoting and anything else compare
   exactly. Unnamed CHECKs (the enum-value CHECKs SQLAlchemy emits for
   ``sa.Enum`` columns on SQLite) are not compared, because the migration
   history predates some of them by design; the PG-native enums are the
   enforcement parity point.
7. Server defaults are not compared: models use client-side ORM defaults while
   several migrations added server defaults for in-place upgrades; only raw SQL
   inserts can observe that difference and it is out of scope here.

Real PostgreSQL parity is NOT RUN here (no live PostgreSQL in this
environment); this test pins the offline SQLite contract.
"""

from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine

from app.database import Base

REPO_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = REPO_ROOT / "apps" / "api" / "alembic.ini"


def _type_signature(type_obj: object, *, strip_varchar_length: bool) -> str:
    signature = str(type_obj)
    if strip_varchar_length and type_obj.__class__.__name__ == "VARCHAR":
        return "VARCHAR"
    return signature


def _enum_model_columns() -> set[tuple[str, str]]:
    return {
        (table.name, column.name)
        for table in Base.metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, sa.Enum)
    }


def _index_where_text(index_entry: dict) -> str | None:
    options = index_entry.get("dialect_options") or {}
    clause = options.get("sqlite_where")
    if clause is None:
        return None
    return str(clause.compile())


def _index_signature(index_entry: dict) -> tuple:
    return (
        tuple(index_entry["column_names"] or []),
        bool(index_entry["unique"]),
        _index_where_text(index_entry),
    )


def _check_signature(constraint: dict) -> tuple[str, str]:
    # Whitespace-run collapse + lowercase only: models and migrations may
    # phrase one expression with different spacing or case, while literal
    # quoting and everything else must match exactly. A missing/unreflected
    # sqltext degrades to "" so it can never silently equal a real text.
    sqltext = constraint.get("sqltext") or ""
    return (constraint["name"], " ".join(sqltext.split()).lower())


def _foreign_keys(connection, table: str) -> list[tuple]:
    rows = connection.exec_driver_sql(f"PRAGMA foreign_key_list({table})")
    # (id, seq, referred_table, from_column, to_column, on_update, on_delete, match)
    return sorted((row[3], row[2], row[4], row[5], row[6]) for row in rows)


def _collect_schema(engine) -> dict:
    """Reflect a SQLite schema into comparable structures."""

    inspector = sa.inspect(engine)
    enum_columns = _enum_model_columns()
    tables = set(inspector.get_table_names()) - {"alembic_version"}
    schema: dict = {"tables": tables}
    with engine.connect() as connection:
        schema["columns"] = {}
        schema["indexes"] = {}
        schema["uniques"] = {}
        schema["checks"] = {}
        schema["foreign_keys"] = {}
        for table in sorted(tables):
            schema["columns"][table] = {
                column["name"]: (
                    column["nullable"],
                    _type_signature(
                        column["type"],
                        strip_varchar_length=(table, column["name"]) in enum_columns,
                    ),
                )
                for column in inspector.get_columns(table)
            }
            schema["indexes"][table] = {
                index["name"]: _index_signature(index)
                for index in inspector.get_indexes(table)
                if index["name"]
            }
            schema["uniques"][table] = sorted(
                tuple(constraint["column_names"] or [])
                for constraint in inspector.get_unique_constraints(table)
            )
            schema["checks"][table] = sorted(
                _check_signature(constraint)
                for constraint in inspector.get_check_constraints(table)
                if constraint.get("name")
            )
            schema["foreign_keys"][table] = _foreign_keys(connection, table)
    return schema


def _diff_schemas(migrated: dict, created: dict) -> list[str]:
    diffs: list[str] = []
    if migrated["tables"] != created["tables"]:
        diffs.append(
            f"tables only in migrated: {sorted(migrated['tables'] - created['tables'])}; "
            f"only in create_all: {sorted(created['tables'] - migrated['tables'])}"
        )
    for table in sorted(migrated["tables"] & created["tables"]):
        migrated_columns = migrated["columns"][table]
        created_columns = created["columns"][table]
        for name in sorted(set(migrated_columns) | set(created_columns)):
            migrated_value = migrated_columns.get(name)
            created_value = created_columns.get(name)
            if migrated_value != created_value:
                diffs.append(
                    f"{table}.{name}: migrated={migrated_value} create_all={created_value}"
                )
        migrated_indexes = migrated["indexes"][table]
        created_indexes = created["indexes"][table]
        for name in sorted(set(migrated_indexes) | set(created_indexes)):
            migrated_value = migrated_indexes.get(name)
            created_value = created_indexes.get(name)
            if migrated_value != created_value:
                diffs.append(
                    f"{table} index {name}: migrated={migrated_value} "
                    f"create_all={created_value}"
                )
        if migrated["uniques"][table] != created["uniques"][table]:
            diffs.append(
                f"{table} unique constraints: migrated={migrated['uniques'][table]} "
                f"create_all={created['uniques'][table]}"
            )
        if migrated["checks"][table] != created["checks"][table]:
            diffs.append(
                f"{table} named checks: migrated={migrated['checks'][table]} "
                f"create_all={created['checks'][table]}"
            )
        if migrated["foreign_keys"][table] != created["foreign_keys"][table]:
            diffs.append(
                f"{table} foreign keys: migrated={migrated['foreign_keys'][table]} "
                f"create_all={created['foreign_keys'][table]}"
            )
    return diffs


def _build_migrated_database(url: str) -> None:
    config = Config(str(ALEMBIC_INI))
    engine = create_engine(url)
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        connection.commit()
    engine.dispose()


def _build_created_database(url: str, metadata: sa.MetaData = Base.metadata) -> None:
    engine = create_engine(url)
    metadata.create_all(engine)
    engine.dispose()


def test_alembic_head_matches_create_all_schema(tmp_path_factory):
    migrated_path = tmp_path_factory.mktemp("parity-migrated") / "migrated.db"
    created_path = tmp_path_factory.mktemp("parity-created") / "created.db"
    _build_migrated_database(f"sqlite:///{migrated_path.as_posix()}")
    _build_created_database(f"sqlite:///{created_path.as_posix()}")

    migrated_engine = create_engine(f"sqlite:///{migrated_path.as_posix()}")
    created_engine = create_engine(f"sqlite:///{created_path.as_posix()}")
    try:
        migrated = _collect_schema(migrated_engine)
        created = _collect_schema(created_engine)
    finally:
        migrated_engine.dispose()
        created_engine.dispose()

    diffs = _diff_schemas(migrated, created)
    assert not diffs, "schema drift between alembic head and create_all:\n" + "\n".join(diffs)


def test_parity_check_detects_a_deliberately_drifted_schema(tmp_path, tmp_path_factory):
    """The comparator must bite: an extra model-side index has to be reported.

    Clones the model metadata, adds one redundant single-column index exactly
    like the pre-fix drift (models declaring an index migrations never
    created), and asserts the difference list names it.
    """

    drifted_path = tmp_path / "drifted.db"
    drifted_metadata = sa.MetaData()
    for table in Base.metadata.sorted_tables:
        table.to_metadata(drifted_metadata)
    sa.Index(
        "ix_scene_assets_project_id",
        drifted_metadata.tables["scene_assets"].c.project_id,
    )
    _build_created_database(f"sqlite:///{drifted_path.as_posix()}", drifted_metadata)

    migrated_path = tmp_path_factory.mktemp("parity-migrated-bite") / "migrated.db"
    _build_migrated_database(f"sqlite:///{migrated_path.as_posix()}")

    drifted_engine = create_engine(f"sqlite:///{drifted_path.as_posix()}")
    migrated_engine = create_engine(f"sqlite:///{migrated_path.as_posix()}")
    try:
        drifted = _collect_schema(drifted_engine)
        migrated = _collect_schema(migrated_engine)
    finally:
        drifted_engine.dispose()
        migrated_engine.dispose()

    diffs = _diff_schemas(migrated, drifted)
    assert any(
        "ix_scene_assets_project_id" in line and "scene_assets index" in line
        for line in diffs
    ), f"comparator missed the deliberate drift; reported:\n{'\n'.join(diffs)}"


def test_parity_check_detects_a_drifted_named_check_sqltext(tmp_path, tmp_path_factory):
    """The comparator must bite on CHECK text, not just constraint names.

    Clones the model metadata and rewrites one named CHECK expression in
    place (same constraint name, different SQL text) — exactly the drift a
    name-only comparison lets through — and asserts the difference list
    reports the drifted named checks.
    """

    drifted_path = tmp_path / "drifted-check.db"
    drifted_metadata = sa.MetaData()
    for table in Base.metadata.sorted_tables:
        table.to_metadata(drifted_metadata)
    attempts = drifted_metadata.tables["model_call_attempts"]
    for constraint in attempts.constraints:
        if constraint.name == "ck_model_call_attempts_dispatch_no":
            constraint.sqltext = attempts.c.dispatch_no >= 0
    _build_created_database(f"sqlite:///{drifted_path.as_posix()}", drifted_metadata)

    migrated_path = tmp_path_factory.mktemp("parity-migrated-check") / "migrated.db"
    _build_migrated_database(f"sqlite:///{migrated_path.as_posix()}")

    drifted_engine = create_engine(f"sqlite:///{drifted_path.as_posix()}")
    migrated_engine = create_engine(f"sqlite:///{migrated_path.as_posix()}")
    try:
        drifted = _collect_schema(drifted_engine)
        migrated = _collect_schema(migrated_engine)
    finally:
        drifted_engine.dispose()
        migrated_engine.dispose()

    diffs = _diff_schemas(migrated, drifted)
    assert any(
        "ck_model_call_attempts_dispatch_no" in line
        and "model_call_attempts named checks" in line
        for line in diffs
    ), f"comparator missed the drifted CHECK sqltext; reported:\n{'\n'.join(diffs)}"
