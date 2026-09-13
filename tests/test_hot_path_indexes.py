"""Hot-path index guards for issue #663.

The five indexes below are deliberately hardcoded (not imported from the
migration module) so that removing or renaming one on either side — the
``models.py`` ``__table_args__`` declarations or migration 20260913_31 —
fails these tests rather than silently agreeing with itself.
``tests/test_migration_parity.py`` additionally compares the whole migrated
schema against ``Base.metadata.create_all``.
"""

from alembic import command
from alembic.config import Config
from app.config import get_settings
from app.database import Base
from sqlalchemy import create_engine, inspect, text

# table -> index name -> exact indexed column order
HOT_PATH_INDEX_COLUMNS: dict[str, dict[str, list[str]]] = {
    "model_call_attempts": {
        "ix_model_call_attempts_provider_model_started": [
            "provider",
            "model_id",
            "started_at",
        ],
    },
    "page_candidates": {
        "ix_page_candidates_job_id": ["job_id"],
        "ix_page_candidates_asset_id": ["asset_id"],
    },
    "asset_candidates": {
        "ix_asset_candidates_job_id": ["job_id"],
        "ix_asset_candidates_asset_id": ["asset_id"],
    },
}


def _sqlite_master_index_names(connection) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            text("SELECT name FROM sqlite_master WHERE type='index'")
        )
    }


def test_hot_path_indexes_appear_on_upgrade_and_drop_on_downgrade(
    tmp_path, monkeypatch
):
    database_url = f"sqlite:///{(tmp_path / 'hot-path-indexes.db').as_posix()}"
    monkeypatch.setattr(get_settings(), "database_url", database_url)
    config = Config("apps/api/alembic.ini")

    command.upgrade(config, "20260906_30")
    engine = create_engine(database_url)
    with engine.connect() as connection:
        absent_before = _sqlite_master_index_names(connection)
    engine.dispose()
    for expected in HOT_PATH_INDEX_COLUMNS.values():
        for name in expected:
            assert name not in absent_before

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    inspector = inspect(engine)
    with engine.connect() as connection:
        present_after = _sqlite_master_index_names(connection)
    for table, expected in HOT_PATH_INDEX_COLUMNS.items():
        columns_by_name = {
            index["name"]: list(index["column_names"] or [])
            for index in inspector.get_indexes(table)
        }
        for name, columns in expected.items():
            assert name in present_after
            assert columns_by_name[name] == columns
    engine.dispose()

    command.downgrade(config, "20260906_30")
    engine = create_engine(database_url)
    with engine.connect() as connection:
        absent_after = _sqlite_master_index_names(connection)
    engine.dispose()
    for expected in HOT_PATH_INDEX_COLUMNS.values():
        for name in expected:
            assert name not in absent_after


def test_models_declare_the_same_hot_path_indexes():
    for table, expected in HOT_PATH_INDEX_COLUMNS.items():
        declared = {
            index.name: [column.name for column in index.columns]
            for index in Base.metadata.tables[table].indexes
        }
        for name, columns in expected.items():
            assert declared[name] == columns
