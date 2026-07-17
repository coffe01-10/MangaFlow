from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import Base
from app.models import Beat, Chapter, Dialogue, MangaPage, Panel, Project, Scene


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
    assert {"character_presence", "props"}.issubset(
        {column["name"] for column in schema.get_columns("panels")}
    )
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


def test_integrity_migration_cleans_orphans_and_repairs_page_references(
    tmp_path, monkeypatch
):
    database_path = tmp_path / "dirty-local.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    monkeypatch.setattr(get_settings(), "database_url", database_url)
    config = Config("apps/api/alembic.ini")
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    engine.dispose()
    command.stamp(config, "20260716_09")

    engine = create_engine(database_url)
    with engine.connect() as connection:
        connection.execute(text("PRAGMA foreign_keys=OFF"))
        connection.commit()
        with Session(bind=connection, expire_on_commit=False) as session:
            project = Project(name="脏库迁移")
            session.add(project)
            session.flush()
            chapter = Chapter(project_id=project.id, title="第一章", ordinal=1)
            session.add(chapter)
            session.flush()
            scene = Scene(chapter_id=chapter.id, ordinal=1)
            session.add(scene)
            session.flush()
            beat = Beat(scene_id=scene.id, ordinal=1)
            session.add(beat)
            session.flush()
            page = MangaPage(
                chapter_id=chapter.id,
                page_number=1,
                beat_ids=[beat.id, "missing-beat"],
                scene_ids=["missing-scene"],
            )
            orphan_panel = Panel(page_id="missing-page", reading_order=1)
            orphan_beat = Beat(scene_id="missing-scene", ordinal=1)
            session.add_all([page, orphan_panel, orphan_beat])
            session.flush()
            session.add(
                Dialogue(panel_id=orphan_panel.id, target_text="孤立对白", reading_order=1)
            )
            session.commit()
            page_id = page.id
        connection.commit()
        connection.execute(text("PRAGMA foreign_keys=ON"))
        connection.commit()
    engine.dispose()

    command.upgrade(config, "head")

    engine = create_engine(database_url)
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
        assert connection.execute(text("SELECT count(*) FROM panels")).scalar_one() == 0
        assert connection.execute(text("SELECT count(*) FROM dialogues")).scalar_one() == 0
        assert connection.execute(text("SELECT count(*) FROM beats")).scalar_one() == 1
        repaired = connection.execute(
            text(
                "SELECT beat_ids, scene_ids, continuity_status "
                "FROM manga_pages WHERE id = :page_id"
            ),
            {"page_id": page_id},
        ).one()
        assert beat.id in repaired.beat_ids
        assert "missing-beat" not in repaired.beat_ids
        assert scene.id in repaired.scene_ids
        assert repaired.continuity_status == "NEEDS_REVIEW"
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
    assert {"character_presence", "props"}.issubset(
        {column["name"] for column in schema.get_columns("panels")}
    )
    engine.dispose()
