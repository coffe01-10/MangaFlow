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
    ModelPricingVersion,
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
    assert "director_command_groups" in schema.get_table_names()
    assert "director_commands" in schema.get_table_names()
    lineage_indexes = {index["name"] for index in schema.get_indexes("candidate_lineage")}
    assert "candidate_lineage" in schema.get_table_names()
    assert {"ix_candidate_lineage_parent_candidate_id", "ix_candidate_lineage_source_command_id"} <= lineage_indexes
    engine.dispose()

    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = create_engine(database_url)
    assert "provider_health" in inspect(engine).get_table_names()
    assert "model_pricing_versions" in inspect(engine).get_table_names()
    assert "candidate_lineage" in inspect(engine).get_table_names()
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



def test_model_pricing_versions_migration_roundtrip(tmp_path, monkeypatch):
    database_url = f"sqlite:///{(tmp_path / 'model-pricing.db').as_posix()}"
    monkeypatch.setattr(get_settings(), "database_url", database_url)
    config = Config("apps/api/alembic.ini")

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    schema = inspect(engine)
    assert "model_pricing_versions" in schema.get_table_names()
    assert {index["name"] for index in schema.get_indexes("model_pricing_versions")} == {
        "ix_model_pricing_versions_lookup"
    }
    engine.dispose()

    command.downgrade(config, "20260829_18")
    engine = create_engine(database_url)
    assert "model_pricing_versions" not in inspect(engine).get_table_names()
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    assert "model_pricing_versions" in inspect(engine).get_table_names()
    engine.dispose()



def test_model_pricing_migration_refuses_incomplete_existing_table(
    tmp_path, monkeypatch
):
    database_url = f"sqlite:///{(tmp_path / 'partial-pricing.db').as_posix()}"
    monkeypatch.setattr(get_settings(), "database_url", database_url)
    config = Config("apps/api/alembic.ini")
    command.upgrade(config, "20260829_18")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        ModelPricingVersion.__table__.create(connection)
        connection.execute(text("DROP INDEX ix_model_pricing_versions_lookup"))
    engine.dispose()

    with pytest.raises(RuntimeError, match="结构与本迁移不匹配"):
        command.upgrade(config, "head")

    engine = create_engine(database_url)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "20260829_18"
        )
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE model_pricing_versions"))
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    assert "model_pricing_versions" in inspect(engine).get_table_names()
    engine.dispose()


def _insert_preference_migration_fixture(database_url: str) -> None:
    engine = create_engine(database_url)
    timestamp = "2026-08-30 00:00:00"
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO provider_profiles (
                    id, preset_key, name, category, description, built_in,
                    enabled, risk_label, documentation_url,
                    created_at, updated_at, version
                ) VALUES (
                    'preference-provider', NULL, 'Preference Provider',
                    'CUSTOM', 'migration fixture', 0, 1, 'LOW', NULL,
                    :timestamp, :timestamp, 4
                )
                """
            ),
            {"timestamp": timestamp},
        )
        connection.execute(
            text(
                """
                INSERT INTO provider_connections (
                    id, provider_id, name, protocol, base_url, enabled,
                    use_responses_api, endpoint_templates, extra_headers,
                    balance_config, nonsecret_config, health_state,
                    last_checked_at, last_success_at, latency_ms, error_code,
                    message, created_at, updated_at, version
                ) VALUES (
                    'preference-connection', 'preference-provider', 'Primary',
                    'OPENAI', 'https://preference.example.com/v1', 1, 0,
                    '{}', '{}', '{}', '{}', 'HEALTHY', NULL, NULL, 25, NULL,
                    'ready', :timestamp, :timestamp, 5
                )
                """
            ),
            {"timestamp": timestamp},
        )
        connection.execute(
            text(
                """
                INSERT INTO ai_models (
                    id, connection_id, provider_model_id, display_name,
                    legacy_alias, model_type, input_modalities,
                    output_modalities, operations, api_surfaces,
                    capabilities, pricing, source, confidence, enabled,
                    priority, success_rate, median_latency_ms,
                    last_verified_at, created_at, updated_at, version
                ) VALUES (
                    'preference-model', 'preference-connection', 'model-v1',
                    'Model V1', NULL, 'TEXT', '["TEXT"]', '["TEXT"]',
                    '["structured_text"]', '["CHAT_COMPLETIONS"]',
                    '{"context": 128000}', '{"input": 1}', 'DISCOVERED',
                    'VERIFIED', 1, 77, 0.98, 321, :timestamp,
                    :timestamp, :timestamp, 6
                )
                """
            ),
            {"timestamp": timestamp},
        )
    engine.dispose()


def test_model_display_preference_migration_preserves_existing_model(
    tmp_path, monkeypatch
):
    database_url = f"sqlite:///{(tmp_path / 'model-preference.db').as_posix()}"
    monkeypatch.setattr(get_settings(), "database_url", database_url)
    config = Config("apps/api/alembic.ini")
    command.upgrade(config, "20260830_19")
    _insert_preference_migration_fixture(database_url)

    engine = create_engine(database_url)
    with engine.connect() as connection:
        before = dict(
            connection.execute(
                text("SELECT * FROM ai_models WHERE id = 'preference-model'")
            ).mappings().one()
        )
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    schema = inspect(engine)
    columns = {column["name"]: column for column in schema.get_columns("ai_models")}
    assert columns["display_enabled"]["nullable"] is False
    with engine.connect() as connection:
        after = dict(
            connection.execute(
                text("SELECT * FROM ai_models WHERE id = 'preference-model'")
            ).mappings().one()
        )
    assert after.pop("display_enabled") == 1
    assert after == before
    engine.dispose()

    command.downgrade(config, "20260830_19")
    engine = create_engine(database_url)
    assert "display_enabled" not in {
        column["name"] for column in inspect(engine).get_columns("ai_models")
    }
    with engine.connect() as connection:
        assert connection.scalar(
            text("SELECT COUNT(*) FROM ai_models WHERE id = 'preference-model'")
        ) == 1
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    with engine.connect() as connection:
        assert connection.scalar(
            text(
                "SELECT display_enabled FROM ai_models "
                "WHERE id = 'preference-model'"
            )
        ) == 1
    engine.dispose()


def test_model_display_preference_downgrade_refuses_hidden_values(
    tmp_path, monkeypatch
):
    database_url = f"sqlite:///{(tmp_path / 'hidden-preference.db').as_posix()}"
    monkeypatch.setattr(get_settings(), "database_url", database_url)
    config = Config("apps/api/alembic.ini")
    command.upgrade(config, "20260830_19")
    _insert_preference_migration_fixture(database_url)
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE ai_models SET display_enabled = false "
                "WHERE id = 'preference-model'"
            )
        )
    engine.dispose()

    with pytest.raises(RuntimeError, match="refusing downgrade"):
        command.downgrade(config, "20260830_19")

    engine = create_engine(database_url)
    assert "display_enabled" in {
        column["name"] for column in inspect(engine).get_columns("ai_models")
    }
    with engine.connect() as connection:
        assert connection.scalar(
            text(
                "SELECT display_enabled FROM ai_models "
                "WHERE id = 'preference-model'"
            )
        ) == 0
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "20260830_20"
        )
    with engine.begin() as connection:
        connection.execute(text("UPDATE ai_models SET display_enabled = true"))
    engine.dispose()

    command.downgrade(config, "20260830_19")
    engine = create_engine(database_url)
    assert "display_enabled" not in {
        column["name"] for column in inspect(engine).get_columns("ai_models")
    }
    engine.dispose()


def _insert_phase_c_project(
    database_url: str,
    *,
    project_id: str,
    text_model_alias: str | None,
    image_model_alias: str | None,
) -> None:
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
                    :project_id, 'Phase C fixture', 'zh-CN', 'rtl',
                    'b5_portrait', 'STANDARD_2K', 'DRAFT_1K', 'SEMI_AUTO',
                    4, 0, 1, NULL, :text_model_alias, :image_model_alias,
                    NULL, '2026-08-31 00:00:00',
                    '2026-08-31 00:00:00', 7
                )
                """
            ),
            {
                "project_id": project_id,
                "text_model_alias": text_model_alias,
                "image_model_alias": image_model_alias,
            },
        )
    engine.dispose()


def test_provider_neutral_alias_migration_roundtrip_preserves_historical_project(
    tmp_path, monkeypatch
):
    database_url = f"sqlite:///{(tmp_path / 'neutral-alias-roundtrip.db').as_posix()}"
    monkeypatch.setattr(get_settings(), "database_url", database_url)
    config = Config("apps/api/alembic.ini")
    command.upgrade(config, "20260830_20")
    _insert_phase_c_project(
        database_url,
        project_id="legacy-project",
        text_model_alias="text.fast",
        image_model_alias="image.nano_banana_2",
    )

    engine = create_engine(database_url)
    with engine.connect() as connection:
        before = dict(
            connection.execute(
                text("SELECT * FROM projects WHERE id = 'legacy-project'")
            ).mappings().one()
        )
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    columns = {column["name"]: column for column in inspect(engine).get_columns("projects")}
    assert columns["text_model_alias"]["nullable"] is True
    assert columns["image_model_alias"]["nullable"] is True
    with engine.connect() as connection:
        assert dict(
            connection.execute(
                text("SELECT * FROM projects WHERE id = 'legacy-project'")
            ).mappings().one()
        ) == before
    engine.dispose()

    command.downgrade(config, "20260830_20")
    engine = create_engine(database_url)
    columns = {column["name"]: column for column in inspect(engine).get_columns("projects")}
    assert columns["text_model_alias"]["nullable"] is False
    assert columns["image_model_alias"]["nullable"] is False
    with engine.connect() as connection:
        assert dict(
            connection.execute(
                text("SELECT * FROM projects WHERE id = 'legacy-project'")
            ).mappings().one()
        ) == before
    engine.dispose()

    command.upgrade(config, "head")


def test_provider_neutral_alias_downgrade_refuses_null_project_without_data_loss(
    tmp_path, monkeypatch
):
    database_url = f"sqlite:///{(tmp_path / 'neutral-alias-null.db').as_posix()}"
    monkeypatch.setattr(get_settings(), "database_url", database_url)
    config = Config("apps/api/alembic.ini")
    command.upgrade(config, "head")
    _insert_phase_c_project(
        database_url,
        project_id="neutral-project",
        text_model_alias=None,
        image_model_alias=None,
    )

    with pytest.raises(RuntimeError, match="projects with NULL legacy model aliases"):
        command.downgrade(config, "20260830_20")

    engine = create_engine(database_url)
    columns = {column["name"]: column for column in inspect(engine).get_columns("projects")}
    assert columns["text_model_alias"]["nullable"] is True
    assert columns["image_model_alias"]["nullable"] is True
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT text_model_alias, image_model_alias, version "
                "FROM projects WHERE id = 'neutral-project'"
            )
        ).one()
        assert tuple(row) == (None, None, 7)
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "20260831_21"
        )
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


_PKG_SEED_ROWS = [
    """
    INSERT INTO projects (
        id, name, language, reading_direction, page_ratio,
        default_resolution, draft_resolution, workflow_mode,
        default_concurrency, ocr_enabled, consistency_check_enabled,
        default_style_id, text_model_alias, image_model_alias,
        deleted_at, created_at, updated_at, version
    ) VALUES (
        'project-pkg', '角色包升级项目', 'zh-CN', 'rtl', 'b5_portrait',
        'STANDARD_2K', 'DRAFT_1K', 'SEMI_AUTO', 4, 0, 1, NULL,
        'text.fast', 'image.nano_banana_2', NULL,
        '2026-08-01 10:00:00', '2026-08-01 10:00:00', 1
    )
    """,
    """
    INSERT INTO characters (
        id, project_id, primary_name, aliases, aliases_normalized,
        alias_conflict, canonical_description, locked_features,
        forbidden_changes, status, created_at, updated_at, version
    ) VALUES
        ('char-a', 'project-pkg', '林澈', '["阿澈"]', '["阿澈"]',
         0, '主角', '["发型"]', '[]', 'CANONICAL',
         '2026-08-01 10:00:00', '2026-08-01 10:00:00', 1),
        ('char-b', 'project-pkg', '陈昊', '[]', '[]',
         0, '第二主角', '[]', '[]', 'UPLOADED',
         '2026-08-01 10:01:00', '2026-08-01 10:01:00', 1),
        ('char-c', 'project-pkg', '无参考', '[]', '[]',
         0, '', '[]', '[]', 'UPLOADED',
         '2026-08-01 10:02:00', '2026-08-01 10:02:00', 1)
    """,
    """
    INSERT INTO assets (
        id, project_id, kind, original_name, display_name, storage_key,
        thumbnail_320_key, thumbnail_640_key, mime_type, byte_size, sha256,
        width, height, source, status, deleted_at, created_at, updated_at, version
    ) VALUES
        ('asset-live', 'project-pkg', 'CHARACTER_REFERENCE', 'front.png', NULL,
         'project-pkg/asset-live.png', NULL, NULL, 'image/png', 100,
         'a000000000000000000000000000000000000000000000000000000000000000',
         64, 64, 'USER_UPLOAD', 'UPLOADED', NULL,
         '2026-08-01 10:00:00', '2026-08-01 10:00:00', 1),
        ('asset-dup1', 'project-pkg', 'CHARACTER_REFERENCE', 'dup1.png', NULL,
         'project-pkg/asset-dup1.png', NULL, NULL, 'image/png', 100,
         'b000000000000000000000000000000000000000000000000000000000000000',
         64, 64, 'USER_UPLOAD', 'UPLOADED', NULL,
         '2026-08-01 10:00:00', '2026-08-01 10:00:00', 1),
        ('asset-dup2', 'project-pkg', 'CHARACTER_REFERENCE', 'dup2.png', NULL,
         'project-pkg/asset-dup2.png', NULL, NULL, 'image/png', 100,
         'c000000000000000000000000000000000000000000000000000000000000000',
         64, 64, 'USER_UPLOAD', 'UPLOADED', NULL,
         '2026-08-01 10:00:00', '2026-08-01 10:00:00', 1),
        ('asset-unspecified', 'project-pkg', 'CHARACTER_REFERENCE', 'unspec.png', NULL,
         'project-pkg/asset-unspec.png', NULL, NULL, 'image/png', 100,
         'd000000000000000000000000000000000000000000000000000000000000000',
         64, 64, 'USER_UPLOAD', 'UPLOADED', NULL,
         '2026-08-01 10:00:00', '2026-08-01 10:00:00', 1),
        ('asset-deleted', 'project-pkg', 'CHARACTER_REFERENCE', 'gone.png', NULL,
         'project-pkg/asset-gone.png', NULL, NULL, 'image/png', 100,
         'e000000000000000000000000000000000000000000000000000000000000000',
         64, 64, 'USER_UPLOAD', 'UPLOADED',
         '2026-08-01 10:00:00', '2026-08-01 10:00:00', '2026-08-01 10:00:00', 1),
        ('asset-outfit', 'project-pkg', 'OUTFIT_REFERENCE', 'outfit.png', NULL,
         'project-pkg/asset-outfit.png', NULL, NULL, 'image/png', 100,
         'f000000000000000000000000000000000000000000000000000000000000000',
         64, 64, 'USER_UPLOAD', 'UPLOADED', NULL,
         '2026-08-01 10:00:00', '2026-08-01 10:00:00', 1)
    """,
    """
    INSERT INTO character_references (
        id, character_id, asset_id, angle, is_canonical, created_at
    ) VALUES
        ('ref-a1', 'char-a', 'asset-live', 'front', 1, '2026-08-01 10:00:00'),
        ('ref-b1', 'char-b', 'asset-dup1', 'front', 0, '2026-08-01 10:00:00'),
        ('ref-b2', 'char-b', 'asset-dup2', 'front', 0, '2026-08-01 10:00:01'),
        ('ref-b3', 'char-b', 'asset-unspecified', 'unspecified', 0,
         '2026-08-01 10:00:02'),
        ('ref-c1', 'char-c', 'asset-deleted', 'side', 0, '2026-08-01 10:00:00')
    """,
    """
    INSERT INTO outfits (
        id, project_id, character_id, name, components, state_rules,
        locked_fields, reference_asset_ids, status, created_at, updated_at, version
    ) VALUES
        ('outfit-a', 'project-pkg', 'char-a', '校服', '{"top": "衬衫"}', '{}',
         '[]', '["asset-outfit"]', 'CANONICAL',
         '2026-08-01 10:00:00', '2026-08-01 10:00:00', 1),
        ('outfit-b', 'project-pkg', 'char-b', '便服', '{}', '{}',
         '[]', '[]', 'UPLOADED',
         '2026-08-01 10:00:00', '2026-08-01 10:00:00', 1)
    """,
    """
    INSERT INTO style_profiles (
        id, project_id, name, color_mode, profile, locked_fields,
        status, created_at, updated_at, version
    ) VALUES (
        'style-pkg', 'project-pkg', '日漫彩稿', 'color',
        '{"reference_asset_ids": ["asset-outfit"], "palette_confirmed": true}',
        '[]', 'ACTIVE', '2026-08-01 10:00:00', '2026-08-01 10:00:00', 3
    )
    """,
    """
    INSERT INTO chapters (
        id, project_id, title, ordinal, status, current_source_revision_id,
        deleted_at, created_at, updated_at, version
    ) VALUES (
        'chapter-pkg', 'project-pkg', '第一章', 1, 'SCRIPT_READY', NULL, NULL,
        '2026-08-01 10:00:00', '2026-08-01 10:00:00', 1
    )
    """,
    """
    INSERT INTO manga_pages (
        id, chapter_id, page_number, revision_no, page_function, panel_count,
        reading_direction, resolution, style_id, status, scene_ids, beat_ids,
        locked_fields, estimated_text_chars, estimated_bubbles,
        source_coverage, selected_candidate_id, storyboard_version,
        selected_candidate_ack_version, continuity_status,
        created_at, updated_at, version
    ) VALUES (
        'page-pkg', 'chapter-pkg', 1, 1, 'dialogue', 4, 'rtl', 'DRAFT_1K',
        NULL, 'STORYBOARDED', '[]', '[]', '[]', 80, 6,
        '{"complete": true, "ranges": []}', NULL, 2, NULL, 'NOT_CHECKED',
        '2026-08-01 10:00:00', '2026-08-01 10:00:00', 1
    )
    """,
    """
    INSERT INTO generation_batches (
        id, project_id, chapter_id, page_id, target_type, target_id, ordinal,
        generation_kind, status, closed_at, created_at, updated_at, version
    ) VALUES (
        'batch-pkg', 'project-pkg', 'chapter-pkg', 'page-pkg', NULL, NULL, 1,
        'PAGE', 'CLOSED', '2026-08-01 11:00:00',
        '2026-08-01 10:00:00', '2026-08-01 11:00:00', 1
    )
    """,
    """
    INSERT INTO generation_jobs (
        id, project_id, target_type, target_id, job_type, priority, status,
        attempt_count, max_attempts, model_alias, catalog_model_id,
        request_parameters, progress, idempotency_key, scheduled_at,
        started_at, finished_at, lease_owner, lease_expires_at, error_code,
        error_message, cancelled_at, archived_at, created_at, updated_at, version
    ) VALUES (
        'job-pkg', 'project-pkg', 'PAGE_CANDIDATE', 'cand-pkg', 'PAGE_GENERATE',
        50, 'COMPLETED', 1, 3, 'image.nano_banana_2', NULL, '{}', 100, NULL,
        NULL, '2026-08-01 10:30:00', '2026-08-01 10:31:00', NULL, NULL,
        NULL, NULL, NULL, NULL, '2026-08-01 10:00:00', '2026-08-01 10:31:00', 1
    )
    """,
    """
    INSERT INTO page_candidates (
        id, batch_id, page_id, ordinal, model_alias, catalog_model_id,
        resolution, status, asset_id, job_id, generation_record_id,
        based_on_storyboard_version, is_favorite, is_selected,
        prompt_snapshot, deleted_at, created_at, updated_at, version
    ) VALUES (
        'cand-pkg', 'batch-pkg', 'page-pkg', 1, 'image.nano_banana_2', NULL,
        'DRAFT_1K', 'READY', NULL, 'job-pkg', NULL, 2, 0, 0,
        '{"reference_selections": {"char-a": {"character_asset_id": "asset-live", "outfit_id": "outfit-a", "outfit_asset_id": "asset-outfit"}}, "storyboard_version": 2, "scene_asset": {"scene_asset_id": null, "scene_asset_version": null}}',
        NULL, '2026-08-01 10:00:00', '2026-08-01 10:31:00', 1
    )
    """,
    """
    INSERT INTO generation_records (
        id, job_id, provider, model_id, catalog_model_id, location,
        parameters, prompt_template, prompt_version, prompt_checksum,
        input_versions, reference_asset_ids, provider_request_id, started_at,
        finished_at, usage, output_asset_ids, status, error_code, error_message
    ) VALUES (
        'record-pkg', 'job-pkg', 'vertex', 'image-model', NULL, 'us-central1',
        '{"resolution": "1K"}', 'page-v2.1.0', 'page-v2.1.0',
        'deadbeef', '{"page": 1, "storyboard": 2}',
        '["asset-live", "asset-outfit"]', NULL, '2026-08-01 10:30:00',
        '2026-08-01 10:31:00', '{}', '[]', 'COMPLETED', NULL, NULL
    )
    """,
]

_PKG_SNAPSHOT_TABLES = (
    "characters",
    "character_references",
    "outfits",
    "style_profiles",
    "assets",
    "page_candidates",
    "generation_records",
)


def _seed_package_upgrade_database(database_url: str) -> None:
    engine = create_engine(database_url)
    with engine.begin() as connection:
        for statement in _PKG_SEED_ROWS:
            connection.execute(text(statement))
    engine.dispose()


def _table_rows(connection, table: str) -> list[tuple]:
    return [tuple(row) for row in connection.execute(text(f"SELECT * FROM {table}"))]


def test_character_package_migration_backfills_compat_packages(tmp_path, monkeypatch):
    """PKG-S1/S2: upgrade is loss-free and keeps every existing id byte-identical."""
    import json as _json

    database_url = f"sqlite:///{(tmp_path / 'pkg-backfill.db').as_posix()}"
    monkeypatch.setattr(get_settings(), "database_url", database_url)
    config = Config("apps/api/alembic.ini")
    command.upgrade(config, "20260901_24")
    _seed_package_upgrade_database(database_url)

    engine = create_engine(database_url)
    with engine.connect() as connection:
        before = {table: _table_rows(connection, table) for table in _PKG_SNAPSHOT_TABLES}
        prompt_before = connection.execute(
            text("SELECT prompt_snapshot FROM page_candidates WHERE id = 'cand-pkg'")
        ).scalar_one()
        versions_before = connection.execute(
            text("SELECT input_versions FROM generation_records WHERE id = 'record-pkg'")
        ).scalar_one()
    engine.dispose()

    command.upgrade(config, "head")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
        after = {table: _table_rows(connection, table) for table in _PKG_SNAPSHOT_TABLES}
        for table in _PKG_SNAPSHOT_TABLES:
            assert before[table] == after[table], f"{table} 行被迁移改写"
        assert connection.execute(
            text("SELECT prompt_snapshot FROM page_candidates WHERE id = 'cand-pkg'")
        ).scalar_one() == prompt_before
        assert connection.execute(
            text("SELECT input_versions FROM generation_records WHERE id = 'record-pkg'")
        ).scalar_one() == versions_before

        packages = [
            dict(row)
            for row in connection.execute(
                text(
                    "SELECT id, character_id, project_id, identity_spec, visual_spec, "
                    "negative_constraints, published_version_id, status, version "
                    "FROM character_model_packages ORDER BY character_id"
                )
            ).mappings()
        ]
        assert [item["character_id"] for item in packages] == ["char-a", "char-b", "char-c"]
        package_by_character = {item["character_id"]: item for item in packages}
        for item in packages:
            assert item["status"] == "ACTIVE"
            assert item["published_version_id"] is None
            assert item["version"] == 1
            assert _json.loads(item["identity_spec"]) == {}
            assert _json.loads(item["visual_spec"]) == {}
            assert _json.loads(item["negative_constraints"]) == []

        versions = [
            dict(row)
            for row in connection.execute(
                text(
                    "SELECT v.id, v.package_id, v.version_number, v.status, "
                    "v.spec_snapshot, v.derived_from_version_id, v.published_at "
                    "FROM character_model_package_versions v ORDER BY v.package_id"
                )
            ).mappings()
        ]
        assert len(versions) == 3
        for item in versions:
            assert item["version_number"] == 1
            assert item["status"] == "DRAFT"
            assert item["derived_from_version_id"] is None
            assert item["published_at"] is None
            snapshot = _json.loads(item["spec_snapshot"]) if isinstance(
                item["spec_snapshot"], str
            ) else item["spec_snapshot"]
            assert snapshot == {
                "identity_spec": {},
                "visual_spec": {},
                "negative_constraints": [],
                "frozen_from": "migration",
            }

        references = [
            dict(row)
            for row in connection.execute(
                text(
                    "SELECT r.version_id, r.asset_id, r.role, r.label, r.sort_order "
                    "FROM character_model_package_version_references r "
                    "JOIN character_model_package_versions v ON v.id = r.version_id "
                    "ORDER BY v.package_id, r.created_at"
                )
            ).mappings()
        ]
        char_a_package = package_by_character["char-a"]["id"]
        char_b_package = package_by_character["char-b"]["id"]
        version_by_package = {item["package_id"]: item["id"] for item in versions}
        char_a_version = version_by_package[char_a_package]
        char_b_version = version_by_package[char_b_package]
        by_package = {
            "char-a": [
                (item["asset_id"], item["role"], item["label"], item["sort_order"])
                for item in references
                if item["version_id"] == char_a_version
            ],
            "char-b": [
                (item["asset_id"], item["role"], item["label"], item["sort_order"])
                for item in references
                if item["version_id"] == char_b_version
            ],
        }
        assert by_package["char-a"] == [
            ("asset-live", "front", "", 0)
        ]
        # dup front degrades to extra with suffix; unspecified stays extra.
        assert by_package["char-b"] == [
            ("asset-dup1", "front", "", 0),
            ("asset-dup2", "extra", "front", 0),
            ("asset-unspecified", "extra", "unspecified", 0),
        ]
        # char-c only bound the soft-deleted asset: its matrix must be empty.
        assert sum(
            1 for item in references if item["version_id"] not in {char_a_version, char_b_version}
        ) == 0

        outfit_rows = [
            dict(row)
            for row in connection.execute(
                text(
                    "SELECT o.version_id, o.outfit_id, o.is_default, o.sort_order "
                    "FROM character_model_package_version_outfits o "
                    "JOIN character_model_package_versions v ON v.id = o.version_id "
                    "ORDER BY v.package_id, o.sort_order"
                )
            ).mappings()
        ]
        assert sorted(
            (item["outfit_id"], item["is_default"], item["sort_order"])
            for item in outfit_rows
        ) == [
            ("outfit-a", 0, 0),
            ("outfit-b", 0, 0),
        ]
    schema = inspect(engine)
    assert {index["name"] for index in schema.get_indexes("character_model_package_versions")} >= {
        "ix_character_model_package_versions_package_status",
        "uq_character_model_package_versions_one_draft",
    }
    assert {index["name"] for index in schema.get_indexes("character_model_package_version_outfits")} >= {
        "uq_character_model_package_version_outfit_default",
    }
    engine.dispose()


def test_character_package_migration_downgrade_roundtrip(tmp_path, monkeypatch):
    """PKG-S13: a pure migration-shaped database round-trips downgrade->upgrade."""
    database_url = f"sqlite:///{(tmp_path / 'pkg-roundtrip.db').as_posix()}"
    monkeypatch.setattr(get_settings(), "database_url", database_url)
    config = Config("apps/api/alembic.ini")
    command.upgrade(config, "20260901_24")
    _seed_package_upgrade_database(database_url)
    command.upgrade(config, "head")

    command.downgrade(config, "20260901_24")
    engine = create_engine(database_url)
    with engine.connect() as connection:
        assert "character_model_packages" not in inspect(connection).get_table_names()
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        assert connection.execute(
            text("SELECT COUNT(*) FROM character_model_packages")
        ).scalar_one() == 3
        assert connection.execute(
            text("SELECT COUNT(*) FROM character_model_package_versions")
        ).scalar_one() == 3
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
    engine.dispose()


def test_character_package_migration_downgrade_refuses_evolved_shape(tmp_path, monkeypatch):
    """PKG-S13: publish, second version or archived package blocks downgrade intact."""
    database_url = f"sqlite:///{(tmp_path / 'pkg-refuse.db').as_posix()}"
    monkeypatch.setattr(get_settings(), "database_url", database_url)
    config = Config("apps/api/alembic.ini")
    command.upgrade(config, "20260901_24")
    _seed_package_upgrade_database(database_url)
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        package_id = connection.execute(
            text("SELECT id FROM character_model_packages WHERE character_id = 'char-a'")
        ).scalar_one()
        version_id = connection.execute(
            text(
                "SELECT id FROM character_model_package_versions WHERE package_id = :pkg"
            ),
            {"pkg": package_id},
        ).scalar_one()
        connection.execute(
            text(
                "UPDATE character_model_package_versions SET status = 'READY', "
                "published_at = '2026-08-02 10:00:00' WHERE id = :vid"
            ),
            {"vid": version_id},
        )
        connection.execute(
            text(
                "UPDATE character_model_packages SET published_version_id = :vid "
                "WHERE id = :pkg"
            ),
            {"vid": version_id, "pkg": package_id},
        )
    engine.dispose()
    with pytest.raises(RuntimeError, match="published or used in production"):
        command.downgrade(config, "20260901_24")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        assert connection.execute(
            text("SELECT COUNT(*) FROM character_model_packages")
        ).scalar_one() == 3
        assert connection.execute(
            text(
                "SELECT published_version_id FROM character_model_packages "
                "WHERE character_id = 'char-a'"
            )
        ).scalar_one() == version_id
    engine.dispose()

    database_url = f"sqlite:///{(tmp_path / 'pkg-refuse-multi.db').as_posix()}"
    monkeypatch.setattr(get_settings(), "database_url", database_url)
    command.upgrade(config, "20260901_24")
    _seed_package_upgrade_database(database_url)
    command.upgrade(config, "head")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        # The one-DRAFT partial index makes two DRAFT versions unconstructible
        # through normal paths, so remove it to exercise the guard itself.
        connection.execute(
            text("DROP INDEX uq_character_model_package_versions_one_draft")
        )
        connection.execute(
            text(
                "INSERT INTO character_model_package_versions ("
                "id, package_id, version_number, status, spec_snapshot, "
                "created_at, updated_at, version"
                ") SELECT 'version-2-b', id, 2, 'DRAFT', '{}', "
                "'2026-08-02 10:00:00', '2026-08-02 10:00:00', 1 "
                "FROM character_model_packages WHERE character_id = 'char-b'"
            )
        )
    engine.dispose()
    with pytest.raises(RuntimeError, match="more than one version"):
        command.downgrade(config, "20260901_24")

    database_url = f"sqlite:///{(tmp_path / 'pkg-refuse-archived.db').as_posix()}"
    monkeypatch.setattr(get_settings(), "database_url", database_url)
    command.upgrade(config, "20260901_24")
    _seed_package_upgrade_database(database_url)
    command.upgrade(config, "head")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE character_model_packages SET status = 'ARCHIVED' "
                "WHERE character_id = 'char-c'"
            )
        )
    engine.dispose()
    with pytest.raises(RuntimeError, match="archived packages exist"):
        command.downgrade(config, "20260901_24")


def test_character_package_migration_refuses_partial_schema(tmp_path, monkeypatch):
    database_url = f"sqlite:///{(tmp_path / 'pkg-partial.db').as_posix()}"
    monkeypatch.setattr(get_settings(), "database_url", database_url)
    config = Config("apps/api/alembic.ini")
    command.upgrade(config, "20260901_24")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE character_model_packages (id VARCHAR(36) PRIMARY KEY)"
            )
        )
    engine.dispose()
    with pytest.raises(RuntimeError, match="已存在但结构与本迁移不匹配"):
        command.upgrade(config, "head")


def test_storyboard_layout_columns_migration_roundtrip(tmp_path, monkeypatch):
    """L1: pure nullable JSON additions round-trip on an empty database."""
    database_url = f"sqlite:///{(tmp_path / 'layout-columns.db').as_posix()}"
    monkeypatch.setattr(get_settings(), "database_url", database_url)
    config = Config("apps/api/alembic.ini")

    def column_map(engine, table):
        return {
            column["name"]: column["nullable"]
            for column in inspect(engine).get_columns(table)
        }

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    assert column_map(engine, "manga_pages")["canvas"] is True
    assert column_map(engine, "manga_pages")["geometry_save_command"] is True
    assert column_map(engine, "panels")["geometry"] is True
    assert column_map(engine, "dialogues")["bubble"] is True
    engine.dispose()

    command.downgrade(config, "20260902_25")
    engine = create_engine(database_url)
    assert "canvas" not in column_map(engine, "manga_pages")
    assert "geometry_save_command" not in column_map(engine, "manga_pages")
    assert "geometry" not in column_map(engine, "panels")
    assert "bubble" not in column_map(engine, "dialogues")
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    assert column_map(engine, "manga_pages")["canvas"] is True
    assert column_map(engine, "manga_pages")["geometry_save_command"] is True
    assert column_map(engine, "panels")["geometry"] is True
    assert column_map(engine, "dialogues")["bubble"] is True
    engine.dispose()


_LAYOUT_SEED_ROWS = [
    """
    INSERT INTO projects (
        id, name, language, reading_direction, page_ratio,
        default_resolution, draft_resolution, workflow_mode,
        default_concurrency, ocr_enabled, consistency_check_enabled,
        default_style_id, text_model_alias, image_model_alias,
        deleted_at, created_at, updated_at, version
    ) VALUES (
        'project-layout', '分镜布局迁移项目', 'zh-CN', 'rtl', 'b5_portrait',
        'STANDARD_2K', 'DRAFT_1K', 'SEMI_AUTO', 4, 0, 1, NULL,
        'text.fast', 'image.nano_banana_2', NULL,
        '2026-08-01 10:00:00', '2026-08-01 10:00:00', 1
    )
    """,
    """
    INSERT INTO chapters (
        id, project_id, title, ordinal, status, current_source_revision_id,
        deleted_at, created_at, updated_at, version
    ) VALUES (
        'chapter-layout', 'project-layout', '第一章', 1, 'SCRIPT_READY', NULL,
        NULL, '2026-08-01 10:00:00', '2026-08-01 10:00:00', 1
    )
    """,
    """
    INSERT INTO manga_pages (
        id, chapter_id, page_number, revision_no, page_function, panel_count,
        reading_direction, resolution, style_id, status, scene_ids, beat_ids,
        locked_fields, estimated_text_chars, estimated_bubbles,
        source_coverage, selected_candidate_id, storyboard_version,
        selected_candidate_ack_version, continuity_status,
        created_at, updated_at, version
    ) VALUES (
        'page-layout', 'chapter-layout', 1, 1, 'dialogue', 3, 'rtl', 'DRAFT_1K',
        NULL, 'STORYBOARDED', '[]', '[]', '[]', 4, 1,
        '{"complete": true, "ranges": []}', NULL, 2, NULL, 'NOT_CHECKED',
        '2026-08-01 10:00:00', '2026-08-01 10:00:00', 1
    )
    """,
    """
    INSERT INTO panels (
        id, page_id, reading_order, bounds, shot_type, camera_angle,
        camera_height, characters, character_presence, props, outfits,
        actions, expressions, background, bubble_regions, sound_effects,
        bleed, borderless, locked_fields, created_at, updated_at, version
    ) VALUES (
        'panel-layout', 'page-layout', 1,
        '{"x": 0.012, "y": 0.012, "width": 0.976, "height": 0.448}',
        'medium_close_up', 'eye_level', 'eye_level', '[]', '{}', '[]', '{}',
        '{}', '{}', '教室', '[]', '["ドンッ"]', 0, 0, '[]',
        '2026-08-01 10:00:00', '2026-08-01 10:00:00', 3
    )
    """,
    """
    INSERT INTO dialogues (
        id, panel_id, speaker_character_id, target_text, reading_order,
        text_direction, region, rewrite_forbidden
    ) VALUES (
        'dialogue-layout', 'panel-layout', NULL, '你来了。', 1, 'vertical',
        '{"preferred": "upper_inner"}', 1
    )
    """,
]

_LAYOUT_LEGACY_TABLES = ("manga_pages", "panels", "dialogues")


def test_storyboard_layout_migration_preserves_legacy_storyboard_rows(
    tmp_path, monkeypatch
):
    """L1: upgrade adds NULL columns and never rewrites legacy storyboard data."""
    database_url = f"sqlite:///{(tmp_path / 'layout-legacy.db').as_posix()}"
    monkeypatch.setattr(get_settings(), "database_url", database_url)
    config = Config("apps/api/alembic.ini")
    command.upgrade(config, "20260902_25")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        for statement in _LAYOUT_SEED_ROWS:
            connection.execute(text(statement))
        before = {
            table: [
                dict(row._mapping)
                for row in connection.execute(text(f"SELECT * FROM {table}"))
            ]
            for table in _LAYOUT_LEGACY_TABLES
        }
    engine.dispose()

    command.upgrade(config, "head")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
        for table in _LAYOUT_LEGACY_TABLES:
            after = [
                dict(row._mapping)
                for row in connection.execute(text(f"SELECT * FROM {table}"))
            ]
            assert len(after) == len(before[table])
            for row_before, row_after in zip(before[table], after):
                for column, value in row_before.items():
                    assert row_after[column] == value, f"{table}.{column} 被迁移改写"
        assert connection.execute(
            text("SELECT canvas FROM manga_pages WHERE id = 'page-layout'")
        ).scalar_one() is None
        assert connection.execute(
            text("SELECT geometry FROM panels WHERE id = 'panel-layout'")
        ).scalar_one() is None
        assert connection.execute(
            text("SELECT bubble FROM dialogues WHERE id = 'dialogue-layout'")
        ).scalar_one() is None
    engine.dispose()

    command.downgrade(config, "20260902_25")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        assert connection.execute(
            text("SELECT bounds FROM panels WHERE id = 'panel-layout'")
        ).scalar_one() == '{"x": 0.012, "y": 0.012, "width": 0.976, "height": 0.448}'
        assert connection.execute(
            text("SELECT region FROM dialogues WHERE id = 'dialogue-layout'")
        ).scalar_one() == '{"preferred": "upper_inner"}'
        assert connection.execute(
            text("SELECT sound_effects FROM panels WHERE id = 'panel-layout'")
        ).scalar_one() == '["ドンッ"]'
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        assert connection.execute(
            text("SELECT geometry FROM panels WHERE id = 'panel-layout'")
        ).scalar_one() is None
    engine.dispose()


def test_candidate_lineage_backfills_repair_and_upscale_history(
    tmp_path, monkeypatch
):
    """L4: request_parameters.original_candidate_id becomes lineage rows.

    Missing parent candidates keep the lineage row with a NULL parent. Real
    PostgreSQL upgrade/downgrade stays NOT RUN.
    """

    from datetime import UTC, datetime

    from app.models import GenerationBatch, GenerationJob, MangaPage, PageCandidate

    database_path = tmp_path / "lineage-backfill.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    monkeypatch.setattr(get_settings(), "database_url", database_url)
    config = Config("apps/api/alembic.ini")
    command.upgrade(config, "20260903_27")

    engine = create_engine(database_url)
    with Session(engine) as db:
        project = Project(name="血缘回填")
        db.add(project)
        db.flush()
        chapter = Chapter(project_id=project.id, title="第一章", ordinal=1)
        db.add(chapter)
        db.flush()
        page = MangaPage(chapter_id=chapter.id, page_number=1, panel_count=3)
        db.add(page)
        db.flush()
        page_batch = GenerationBatch(
            project_id=project.id, page_id=page.id, ordinal=1, generation_kind="PAGE"
        )
        repair_batch = GenerationBatch(
            project_id=project.id, page_id=page.id, ordinal=2, generation_kind="REPAIR"
        )
        upscale_batch = GenerationBatch(
            project_id=project.id, page_id=page.id, ordinal=3, generation_kind="UPSCALE"
        )
        orphan_batch = GenerationBatch(
            project_id=project.id, page_id=page.id, ordinal=4, generation_kind="REPAIR"
        )
        db.add_all([page_batch, repair_batch, upscale_batch, orphan_batch])
        db.flush()
        created = datetime.now(UTC)
        parent = PageCandidate(
            batch_id=page_batch.id,
            page_id=page.id,
            ordinal=1,
            model_alias="image.fast",
            resolution="1K",
            status="READY",
        )
        repair_child = PageCandidate(
            batch_id=repair_batch.id,
            page_id=page.id,
            ordinal=1,
            model_alias="image.fast",
            resolution="2K",
            status="READY",
        )
        upscale_child = PageCandidate(
            batch_id=upscale_batch.id,
            page_id=page.id,
            ordinal=1,
            model_alias="image.quality",
            resolution="4K",
            status="READY",
        )
        orphan_child = PageCandidate(
            batch_id=orphan_batch.id,
            page_id=page.id,
            ordinal=1,
            model_alias="image.fast",
            resolution="1K",
            status="READY",
        )
        db.add_all([parent, repair_child, upscale_child, orphan_child])
        db.flush()
        repair_job = GenerationJob(
            project_id=project.id,
            target_type="PAGE_CANDIDATE",
            target_id=repair_child.id,
            job_type="PAGE_REPAIR",
            request_parameters={"original_candidate_id": parent.id},
        )
        upscale_job = GenerationJob(
            project_id=project.id,
            target_type="PAGE_CANDIDATE",
            target_id=upscale_child.id,
            job_type="PAGE_UPSCALE",
            request_parameters={"original_candidate_id": parent.id},
        )
        orphan_job = GenerationJob(
            project_id=project.id,
            target_type="PAGE_CANDIDATE",
            target_id=orphan_child.id,
            job_type="PAGE_REPAIR",
            request_parameters={"original_candidate_id": "missing-parent"},
        )
        db.add_all([repair_job, upscale_job, orphan_job])
        db.flush()
        repair_child.job_id = repair_job.id
        upscale_child.job_id = upscale_job.id
        orphan_child.job_id = orphan_job.id
        for candidate in (parent, repair_child, upscale_child, orphan_child):
            candidate.created_at = created
        db.commit()
        ids = {
            "parent": parent.id,
            "repair_child": repair_child.id,
            "upscale_child": upscale_child.id,
            "orphan_child": orphan_child.id,
        }
    engine.dispose()

    command.upgrade(config, "head")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
        rows = connection.execute(
            text(
                """
                SELECT child_candidate_id, parent_candidate_id, lineage_kind,
                       model_alias, resolution
                FROM candidate_lineage
                ORDER BY lineage_kind
                """
            )
        ).all()
    by_child = {row[0]: row for row in rows}
    assert len(rows) == 3
    repaired = by_child[ids["repair_child"]]
    assert repaired[1] == ids["parent"]
    assert repaired[2] == "REPAIRED"
    assert repaired[4] == "STANDARD_2K"
    upscaled = by_child[ids["upscale_child"]]
    assert upscaled[1] == ids["parent"]
    assert upscaled[2] == "UPSCALED"
    assert upscaled[3] == "image.quality"
    assert upscaled[4] == "HIGH_4K"
    orphan = by_child[ids["orphan_child"]]
    assert orphan[1] is None
    assert orphan[2] == "REPAIRED"
    assert orphan[3] == "image.fast"
    engine.dispose()

    command.downgrade(config, "20260903_27")
    engine = create_engine(database_url)
    assert "candidate_lineage" not in inspect(engine).get_table_names()
    engine.dispose()
