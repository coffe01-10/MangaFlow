import pytest
from alembic import command
from alembic.config import Config
from app.config import get_settings
from app.database import Base
from app.models import (
    Beat,
    Chapter,
    Dialogue,
    MangaPage,
    ModelCallAttempt,
    Panel,
    Project,
    Scene,
)
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session


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
    character_reference_indexes = {
        index["name"]: index for index in schema.get_indexes("character_references")
    }
    assert character_reference_indexes["uq_character_reference_asset"]["unique"]
    assert "archived_at" in {
        column["name"] for column in schema.get_columns("generation_jobs")
    }
    assert "ix_generation_jobs_status_lease" in {
        index["name"] for index in schema.get_indexes("generation_jobs")
    }
    assert "storyboard_version" in {
        column["name"] for column in schema.get_columns("inspection_results")
    }
    assert {"character_presence", "props"}.issubset(
        {column["name"] for column in schema.get_columns("panels")}
    )
    engine.dispose()

    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = create_engine(database_url)
    assert "provider_health" in inspect(engine).get_table_names()
    assert "ix_generation_jobs_status_lease" in {
        index["name"] for index in schema.get_indexes("generation_jobs")
    }
    engine.dispose()


def test_populated_database_upgrades_to_provider_platform(tmp_path, monkeypatch):
    database_path = tmp_path / "populated-provider-migration.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    monkeypatch.setattr(get_settings(), "database_url", database_url)
    config = Config("apps/api/alembic.ini")
    command.upgrade(config, "20260717_14")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO projects (
                    id, name, language, reading_direction, page_ratio,
                    default_resolution, draft_resolution, workflow_mode,
                    default_concurrency, ocr_enabled,
                    consistency_check_enabled, default_style_id,
                    text_model_alias, image_model_alias, deleted_at,
                    created_at, updated_at, version
                ) VALUES (
                    'project-existing', '现有项目', 'zh-CN', 'rtl', 'b5_portrait',
                    'STANDARD_2K', 'DRAFT_1K', 'SEMI_AUTO', 4, 0, 1, NULL,
                    'text.fast', 'image.nano_banana_2', NULL,
                    '2026-07-18 00:00:00', '2026-07-18 00:00:00', 1
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO chapters (
                    id, project_id, title, ordinal, status,
                    current_source_revision_id, created_at, updated_at,
                    version, deleted_at
                ) VALUES (
                    'chapter-existing', 'project-existing', '第一章', 1,
                    'IMPORTED', NULL, '2026-07-18 00:00:00',
                    '2026-07-18 00:00:00', 1, NULL
                )
                """
            )
        )
    engine.dispose()

    command.upgrade(config, "head")

    engine = create_engine(database_url)
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
        assert connection.execute(text("SELECT count(*) FROM projects")).scalar_one() == 1
        assert connection.execute(text("SELECT count(*) FROM chapters")).scalar_one() == 1
    project_foreign_keys = inspect(engine).get_foreign_keys("projects")
    assert {tuple(item["constrained_columns"]) for item in project_foreign_keys} >= {
        ("default_text_model_id",),
        ("last_image_model_id",),
    }
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


def test_assert_database_is_current_fails_on_unmigrated_database(tmp_path, monkeypatch):
    import pytest
    from app import main

    database_path = tmp_path / "unmigrated.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    monkeypatch.setattr(get_settings(), "database_url", database_url)
    engine = create_engine(database_url)
    monkeypatch.setattr(main, "engine", engine)

    with pytest.raises(RuntimeError, match="数据库迁移版本不匹配") as exc_info:
        main._assert_database_is_current()
    assert "未初始化" in str(exc_info.value)
    engine.dispose()


def test_assert_database_is_current_fails_on_outdated_database(tmp_path, monkeypatch):
    import pytest
    from app import main

    database_path = tmp_path / "outdated.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    monkeypatch.setattr(get_settings(), "database_url", database_url)
    config = Config("apps/api/alembic.ini")
    command.upgrade(config, "20260717_14")

    engine = create_engine(database_url)
    monkeypatch.setattr(main, "engine", engine)

    with pytest.raises(RuntimeError, match="数据库迁移版本不匹配") as exc_info:
        main._assert_database_is_current()
    assert "20260717_14" in str(exc_info.value)
    engine.dispose()


def test_assert_database_is_current_succeeds_on_head(tmp_path, monkeypatch):
    from app import main

    database_path = tmp_path / "current.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    monkeypatch.setattr(get_settings(), "database_url", database_url)
    config = Config("apps/api/alembic.ini")
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    monkeypatch.setattr(main, "engine", engine)

    # Should not raise
    main._assert_database_is_current()
    engine.dispose()


def test_inspection_version_migration_preserves_unknown_legacy_results(tmp_path, monkeypatch):
    database_url = f"sqlite:///{(tmp_path / 'inspection-version.db').as_posix()}"
    monkeypatch.setattr(get_settings(), "database_url", database_url)
    config = Config("apps/api/alembic.ini")
    command.upgrade(config, "20260801_16")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO inspection_results "
            "(id, category, outcome, details, regions, severity, created_at) "
            "VALUES ('legacy', 'CONTINUITY', 'PASS', '{}', '[]', 'INFO', CURRENT_TIMESTAMP)"
        ))
    engine.dispose()

    command.upgrade(config, "head")
    with engine.connect() as connection:
        row = connection.execute(text(
            "SELECT id, storyboard_version FROM inspection_results WHERE id = 'legacy'"
        )).one()
        assert row.id == "legacy"
        assert row.storyboard_version is None
    engine.dispose()

    command.downgrade(config, "20260801_16")
    assert "storyboard_version" not in {
        column["name"] for column in inspect(engine).get_columns("inspection_results")
    }
    engine.dispose()

    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM inspection_results")).scalar_one() == 1
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
    engine.dispose()


def test_model_call_attempts_migration_roundtrip(tmp_path, monkeypatch):
    database_url = f"sqlite:///{(tmp_path / 'model-call-attempts.db').as_posix()}"
    monkeypatch.setattr(get_settings(), "database_url", database_url)
    config = Config("apps/api/alembic.ini")

    command.upgrade(config, "20260827_17")
    engine = create_engine(database_url)
    assert "model_call_attempts" not in inspect(engine).get_table_names()
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    schema = inspect(engine)
    assert "model_call_attempts" in schema.get_table_names()
    assert {column["name"] for column in schema.get_columns("model_call_attempts")} >= {
        "id",
        "job_id",
        "project_id",
        "job_attempt",
        "dispatch_no",
        "route_switched",
        "outcome",
        "provider",
        "model_id",
        "catalog_model_id",
        "connection_id",
        "selected_key_id",
        "request_id",
        "started_at",
        "finished_at",
        "duration_ms",
        "usage",
        "route_reason",
        "route_score",
        "error_code",
        "error_message",
        "created_at",
        "updated_at",
        "version",
    }
    job_foreign_keys = {
        tuple(item["constrained_columns"]): item
        for item in schema.get_foreign_keys("model_call_attempts")
    }
    assert job_foreign_keys[("job_id",)]["options"].get("ondelete") == "RESTRICT"
    indexes = {
        index["name"]: set(index["column_names"])
        for index in schema.get_indexes("model_call_attempts")
    }
    assert indexes["ix_model_call_attempts_job_started"] == {"job_id", "started_at"}
    assert indexes["ix_model_call_attempts_outcome_started"] == {"outcome", "started_at"}
    assert indexes["ix_model_call_attempts_catalog_model"] == {"catalog_model_id"}
    unique = {
        tuple(item["column_names"])
        for item in schema.get_unique_constraints("model_call_attempts")
    }
    assert ("job_id", "job_attempt", "dispatch_no") in unique
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO projects (
                    id, name, language, reading_direction, page_ratio,
                    default_resolution, draft_resolution, workflow_mode,
                    default_concurrency, ocr_enabled,
                    consistency_check_enabled, default_style_id,
                    text_model_alias, image_model_alias, deleted_at,
                    created_at, updated_at, version
                ) VALUES (
                    'project-1', '账本迁移项目', 'zh-CN', 'rtl', 'b5_portrait',
                    'STANDARD_2K', 'DRAFT_1K', 'SEMI_AUTO', 2, 0, 1, NULL,
                    'text.fast', 'image.nano_banana_2', NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 1
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO generation_jobs (
                    id, project_id, target_type, target_id, job_type, status,
                    priority, attempt_count, max_attempts, request_parameters,
                    progress, created_at, updated_at, version
                ) VALUES (
                    'job-1', 'project-1', 'WORKFLOW_NODE', 'node-1',
                    'WORKFLOW_NODE', 'COMPLETED', 50, 1, 3, '{}', 100,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 1
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO model_call_attempts (
                    id, job_id, project_id, job_attempt, dispatch_no,
                    route_switched, outcome, provider, model_id,
                    started_at, finished_at, duration_ms, usage,
                    created_at, updated_at, version
                ) VALUES (
                    'attempt-1', 'job-1', 'project-1', 1, 1,
                    0, 'SUCCEEDED', 'preset', 'model-1',
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 120, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 1
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO model_call_attempts (
                    id, job_id, project_id, job_attempt, dispatch_no,
                    route_switched, provider, model_id, started_at,
                    created_at, updated_at, version
                ) VALUES (
                    'attempt-2', 'job-1', 'project-1', 1, 2,
                    1, 'preset', 'model-1', CURRENT_TIMESTAMP,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 1
                )
                """
            )
        )
    engine.dispose()

    command.downgrade(config, "20260827_17")
    engine = create_engine(database_url)
    assert "model_call_attempts" not in inspect(engine).get_table_names()
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    assert "model_call_attempts" in inspect(engine).get_table_names()
    engine.dispose()


def test_model_call_attempts_migration_fails_loudly_on_partial_table(
    tmp_path, monkeypatch
):
    database_url = f"sqlite:///{(tmp_path / 'partial-ledger.db').as_posix()}"
    monkeypatch.setattr(get_settings(), "database_url", database_url)
    config = Config("apps/api/alembic.ini")
    command.upgrade(config, "20260827_17")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        ModelCallAttempt.__table__.create(connection)
        connection.execute(text("DROP INDEX ix_model_call_attempts_project_id"))
    engine.dispose()

    # Matching columns alone are insufficient: a missing owned index must keep
    # Alembic from stamping this revision over an incomplete schema.
    with pytest.raises(RuntimeError, match="结构与本迁移不匹配"):
        command.upgrade(config, "head")

    engine = create_engine(database_url)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "20260827_17"
        )
        columns = inspect(connection).get_columns("model_call_attempts")
        assert {column["name"] for column in columns} == {
            column.name for column in ModelCallAttempt.__table__.columns
        }
        indexes = {
            index["name"]
            for index in inspect(connection).get_indexes("model_call_attempts")
        }
        assert "ix_model_call_attempts_project_id" not in indexes

    with engine.begin() as connection:
        connection.execute(text("DROP TABLE model_call_attempts"))
    engine.dispose()

    # After the obstacle is removed the upgrade completes normally.
    command.upgrade(config, "head")
    engine = create_engine(database_url)
    schema = inspect(engine)
    assert "model_call_attempts" in schema.get_table_names()
    assert "ix_model_call_attempts_project_id" in {
        index["name"] for index in schema.get_indexes("model_call_attempts")
    }
    engine.dispose()


def test_programmatic_migrations_use_supplied_connection_without_default_engine(
    tmp_path, monkeypatch
):
    import sqlalchemy
    from alembic.script import ScriptDirectory

    database_url = f"sqlite:///{(tmp_path / 'supplied-connection.db').as_posix()}"
    engine = create_engine(database_url)
    config = Config("apps/api/alembic.ini")
    # Any attempt to fall back to the default URL/engine must fail this test.
    monkeypatch.setattr(get_settings(), "database_url", "not-a-database")

    def reject_default_engine(*args, **kwargs):
        raise AssertionError("Migration tried to create an unowned default engine")

    monkeypatch.setattr(sqlalchemy, "engine_from_config", reject_default_engine)
    try:
        with engine.connect() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                ScriptDirectory.from_config(config).get_current_head()
            )
            assert "workflow_node_runs" in inspect(connection).get_table_names()
            connection.commit()
            command.downgrade(config, "base")
            assert "projects" not in inspect(connection).get_table_names()
            connection.commit()
            command.upgrade(config, "head")
            assert not connection.closed
    finally:
        engine.dispose()
