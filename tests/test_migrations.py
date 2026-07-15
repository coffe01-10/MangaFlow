from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.config import get_settings
from app.database import Base


def test_empty_database_upgrade_downgrade_and_upgrade(tmp_path, monkeypatch):
    database_path = tmp_path / "migration-roundtrip.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    monkeypatch.setattr(get_settings(), "database_url", database_url)
    config = Config("apps/api/alembic.ini")

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    schema = inspect(engine)
    assert "workflow_definitions" in schema.get_table_names()
    assert {column["name"] for column in schema.get_columns("assets")} >= {
        "thumbnail_320_key",
        "thumbnail_640_key",
    }
    assert {index["name"] for index in schema.get_indexes("generation_batches")} >= {
        "ix_generation_batches_project_created_id",
    }
    assert "archived_at" in {
        column["name"] for column in schema.get_columns("generation_jobs")
    }
    engine.dispose()

    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = create_engine(database_url)
    assert "provider_health" in inspect(engine).get_table_names()
    engine.dispose()


def test_upgrade_adopts_complete_schema_created_by_early_local_build(tmp_path, monkeypatch):
    database_path = tmp_path / "precreated-local.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    monkeypatch.setattr(get_settings(), "database_url", database_url)
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    engine.dispose()
    config = Config("apps/api/alembic.ini")
    command.stamp(config, "20260715_05")

    command.upgrade(config, "head")

    engine = create_engine(database_url)
    schema = inspect(engine)
    assert "workflow_definitions" in schema.get_table_names()
    assert {column["name"] for column in schema.get_columns("assets")} >= {
        "thumbnail_320_key",
        "thumbnail_640_key",
    }
    assert "archived_at" in {
        column["name"] for column in schema.get_columns("generation_jobs")
    }
    engine.dispose()
