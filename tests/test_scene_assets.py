"""V02-20B scene asset regression suite (contract doc §14.1 matrix S1-S11).

Real PostgreSQL upgrade/downgrade and real provider generation stay NOT RUN;
SQLite round trips cover migration behavior and the offline API surface.
"""

import io

import pytest
from alembic import command
from alembic.config import Config
from PIL import Image
from sqlalchemy import create_engine, inspect, select, text

from app.config import get_settings
from app.domain.states import JobStatus
from app.models import (
    Asset,
    Beat,
    Chapter,
    GenerationJob,
    JobAssetReference,
    MangaPage,
    Panel,
    Project,
    Scene,
    SceneAsset,
    SceneAssetReference,
    SceneAssetVariant,
    SceneAssetVariantReference,
    ScriptRevision,
    SourceSegment,
    utcnow,
)

from app.services.scene_assets import (
    resolve_scene_background,
    scene_asset_snapshot,
    scene_reference_assets,
)

_hash_counter = 0


def _new_hash() -> str:
    global _hash_counter
    _hash_counter += 1
    return f"{_hash_counter:064x}"


def _png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def _project(client, name="场景资产测试") -> dict:
    return client.post("/api/v1/projects", json={"name": name}).json()


def _skip_page_readiness(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.page_readiness.ensure_page_ready", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        "app.services.ordinal_allocator.ensure_page_ready", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        "app.api.routes.workflow.generation.ensure_page_ready",
        lambda *_args, **_kwargs: None,
    )


def _chapter_and_page(client, db_session, project_id: str):
    paragraph = "故事发生在老教学楼。她推开门，看见窗边的朋友，轻声问道：你回来了吗。"
    imported = client.post(
        f"/api/v1/projects/{project_id}/sources/import",
        json={"title": "第一章", "text": "\n\n".join([paragraph] * 2)},
    )
    assert imported.status_code == 201
    chapter = imported.json()["chapters"][0]
    segments = list(
        db_session.scalars(
            select(SourceSegment)
            .where(
                SourceSegment.source_revision_id == chapter["current_source_revision_id"]
            )
            .order_by(SourceSegment.ordinal)
        )
    )
    scene = Scene(
        chapter_id=chapter["id"],
        ordinal=1,
        location="老教学楼",
        source_range={"segment_ids": [item.id for item in segments]},
    )
    db_session.add(scene)
    db_session.flush()
    for index, segment in enumerate(segments, 1):
        db_session.add(
            Beat(
                scene_id=scene.id,
                ordinal=index,
                action=segment.text,
                source_range={"segment_ids": [segment.id]},
            )
        )
    db_session.add(
        ScriptRevision(
            chapter_id=chapter["id"],
            source_revision_id=chapter["current_source_revision_id"],
            revision_no=1,
            status="READY",
            coverage={
                "expected": len(segments),
                "covered": len(segments),
                "ratio": 1,
                "missing_segment_ids": [],
            },
        )
    )
    chapter_record = db_session.get(Chapter, chapter["id"])
    chapter_record.status = "SCRIPT_READY"
    db_session.commit()
    planned = client.post(
        f"/api/v1/chapters/{chapter['id']}/plan",
        json={"replace_existing": True},
    )
    assert planned.status_code == 200
    page = db_session.get(MangaPage, planned.json()["pages"][0]["id"])
    return chapter, planned.json(), scene, page


def _create_scene_asset(client, project_id: str, **overrides) -> dict:
    payload = {
        "name": "高三（2）班教室",
        "description": "旧教学楼二层朝南的教室",
        "structured": {
            "place": "校园·教学楼",
            "subareas": ["高三（2）班教室", "走廊"],
            "interior": True,
            "time_of_day": "day",
            "weather": "clear",
            "season": "spring",
            "lighting": "soft_diffuse",
            "palette": {"dominant": ["#f2efe9"], "mood": "bright"},
            "fixed_props": ["讲台", "黑板", "窗"],
            "spatial_relations": [{"from": "讲台", "to": "黑板", "relation": "in_front_of"}],
        },
    }
    payload.update(overrides)
    response = client.post(
        f"/api/v1/projects/{project_id}/scene-assets", json=payload
    )
    assert response.status_code == 201, response.text
    return response.json()


def _reference_asset(db_session, project_id: str, **overrides) -> Asset:
    asset = Asset(
        project_id=project_id,
        kind="SCENE_REFERENCE",
        original_name="scene-ref.png",
        storage_key=f"uploads/{project_id}/scene-ref-{_new_hash()}.png",
        mime_type="image/png",
        byte_size=120,
        sha256=_new_hash(),
        source="USER_UPLOAD",
        status="UPLOADED",
    )
    for key, value in overrides.items():
        setattr(asset, key, value)
    db_session.add(asset)
    db_session.commit()
    db_session.refresh(asset)
    return asset


# --- S1: migration round trips preserve legacy location text ---------------


def test_migration_upgrade_preserves_legacy_location(tmp_path, monkeypatch):
    database_path = tmp_path / "scene-assets-migration.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    monkeypatch.setattr(get_settings(), "database_url", database_url)
    config = Config("apps/api/alembic.ini")
    command.upgrade(config, "20260901_23")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO projects (id, name, language, reading_direction, "
                "page_ratio, default_resolution, draft_resolution, workflow_mode, "
                "default_concurrency, ocr_enabled, consistency_check_enabled, "
                "created_at, updated_at, version) VALUES "
                "('p1', '项目', 'zh-CN', 'rtl', 'b5_portrait', 'STANDARD_2K', "
                "'DRAFT_1K', 'SEMI_AUTO', 4, 0, 1, "
                "'2026-09-01 00:00:00', '2026-09-01 00:00:00', 1)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO chapters (id, project_id, title, ordinal, status, "
                "created_at, updated_at, version) VALUES "
                "('c1', 'p1', '第一章', 1, 'IMPORTED', "
                "'2026-09-01 00:00:00', '2026-09-01 00:00:00', 1)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO scenes (id, chapter_id, ordinal, location, "
                "time_label, weather, purpose, emotional_arc, source_range, "
                "outfit_assignments, locked_fields, created_at, updated_at, version) "
                "VALUES ('s1', 'c1', 1, '老教学楼', '放学后', '晴', '', '', "
                "'{}', '{}', '[]', '2026-09-01 00:00:00', "
                "'2026-09-01 00:00:00', 1)"
            )
        )
    engine.dispose()

    command.upgrade(config, "head")

    engine = create_engine(database_url)
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT location FROM scenes WHERE id = 's1'")
        ).scalar_one() == "老教学楼"
        row = connection.execute(
            text("SELECT scene_asset_id, scene_asset_variant_id FROM scenes WHERE id = 's1'")
        ).one()
        assert row == (None, None)
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
    schema = inspect(engine)
    for table in (
        "scene_assets",
        "scene_asset_references",
        "scene_asset_variants",
        "scene_asset_variant_references",
    ):
        assert table in schema.get_table_names()
    scene_columns = {column["name"] for column in schema.get_columns("scene_assets")}
    assert {
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
    } == scene_columns
    reference_columns = {
        column["name"] for column in schema.get_columns("scene_asset_references")
    }
    assert {"id", "scene_asset_id", "asset_id", "role", "is_canonical", "created_at"} == (
        reference_columns
    )
    variant_columns = {
        column["name"] for column in schema.get_columns("scene_asset_variants")
    }
    assert {
        "id",
        "scene_asset_id",
        "name",
        "structured_overrides",
        "is_canonical",
        "deleted_at",
        "created_at",
        "updated_at",
        "version",
    } == variant_columns
    index_names = {index["name"] for index in schema.get_indexes("scene_assets")}
    assert "uq_scene_assets_project_active_name" in index_names
    assert "ix_scene_assets_project_deleted_created" in index_names
    assert "ix_scenes_scene_asset_id" in {
        index["name"] for index in schema.get_indexes("scenes")
    }
    engine.dispose()

    command.downgrade(config, "20260901_23")
    command.upgrade(config, "head")
    engine = create_engine(database_url)
    assert "scene_assets" in inspect(engine).get_table_names()
    engine.dispose()


def test_migration_downgrade_refuses_bindings_and_rows(tmp_path, monkeypatch):
    database_path = tmp_path / "scene-assets-downgrade.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    monkeypatch.setattr(get_settings(), "database_url", database_url)
    config = Config("apps/api/alembic.ini")
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO projects (id, name, language, reading_direction, "
                "page_ratio, default_resolution, draft_resolution, workflow_mode, "
                "default_concurrency, ocr_enabled, consistency_check_enabled, "
                "created_at, updated_at, version) VALUES "
                "('p1', '项目', 'zh-CN', 'rtl', 'b5_portrait', 'STANDARD_2K', "
                "'DRAFT_1K', 'SEMI_AUTO', 4, 0, 1, "
                "'2026-09-01 00:00:00', '2026-09-01 00:00:00', 1)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO chapters (id, project_id, title, ordinal, status, "
                "created_at, updated_at, version) VALUES "
                "('c1', 'p1', '第一章', 1, 'IMPORTED', "
                "'2026-09-01 00:00:00', '2026-09-01 00:00:00', 1)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO scenes (id, chapter_id, ordinal, location, "
                "time_label, weather, purpose, emotional_arc, source_range, "
                "outfit_assignments, locked_fields, scene_asset_id, "
                "scene_asset_variant_id, created_at, updated_at, version) "
                "VALUES ('s1', 'c1', 1, '老教学楼', '', '', '', '', "
                "'{}', '{}', '[]', NULL, NULL, "
                "'2026-09-01 00:00:00', '2026-09-01 00:00:00', 1)"
            )
        )
    engine.dispose()

    # A scene-assets row alone blocks the downgrade even without bindings.
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO scene_assets (id, project_id, name, normalized_name, "
                "description, location_hint, structured, status, locked_fields, "
                "deleted_at, created_at, updated_at, version) VALUES "
                "('sa1', 'p1', '教室', '教室', '', '', '{}', 'UPLOADED', '[]', NULL, "
                "'2026-09-01 00:00:00', '2026-09-01 00:00:00', 1)"
            )
        )
    engine.dispose()

    with pytest.raises(RuntimeError, match="refusing downgrade"):
        command.downgrade(config, "20260901_23")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM scene_assets"))
    engine.dispose()
    command.downgrade(config, "20260901_23")
    command.upgrade(config, "head")
    engine = create_engine(database_url)
    assert "scene_assets" in inspect(engine).get_table_names()
    engine.dispose()


def test_scene_asset_migration_fails_loudly_on_partial_table(tmp_path, monkeypatch):
    database_path = tmp_path / "partial-scene-assets.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    monkeypatch.setattr(get_settings(), "database_url", database_url)
    config = Config("apps/api/alembic.ini")
    command.upgrade(config, "20260901_23")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE scene_assets ("
                "id VARCHAR(36) PRIMARY KEY, "
                "project_id VARCHAR(36) NOT NULL, "
                "name VARCHAR(120) NOT NULL)"
            )
        )
    engine.dispose()

    with pytest.raises(RuntimeError, match="结构与本迁移不匹配"):
        command.upgrade(config, "head")


# --- S2: resolve_scene_background priority --------------------------------


def test_resolve_scene_background_priority(db_session):
    project = Project(name="解析测试")
    db_session.add(project)
    db_session.flush()
    chapter = Chapter(project_id=project.id, title="第一章", ordinal=1)
    db_session.add(chapter)
    db_session.flush()
    scene = Scene(chapter_id=chapter.id, ordinal=1, location="老教学楼")
    db_session.add(scene)
    db_session.flush()

    # Unbound falls back to the historical location text.
    assert resolve_scene_background(db_session, scene) == "老教学楼"

    structured_asset = SceneAsset(
        project_id=project.id,
        name="教室",
        normalized_name="教室",
        description="旧教学楼二层朝南的教室",
        structured={
            "place": "校园·教学楼",
            "interior": True,
            "time_of_day": "day",
            "weather": "clear",
            "lighting": "soft_diffuse",
            "fixed_props": ["讲台", "黑板"],
        },
    )
    db_session.add(structured_asset)
    db_session.flush()
    scene.scene_asset_id = structured_asset.id
    compiled = resolve_scene_background(db_session, scene)
    assert "校园·教学楼" in compiled
    assert "室内" in compiled
    assert "白天" in compiled
    assert "讲台" in compiled

    # Structured is empty -> description fallback.
    description_asset = SceneAsset(
        project_id=project.id,
        name="走廊",
        normalized_name="走廊",
        description="走廊尽头的告示板",
        structured={},
    )
    db_session.add(description_asset)
    db_session.flush()
    scene.scene_asset_id = description_asset.id
    assert resolve_scene_background(db_session, scene) == "走廊尽头的告示板"

    # Missing asset id resolves like an unbound scene.
    scene.scene_asset_id = "missing-id"
    assert resolve_scene_background(db_session, scene) == "老教学楼"

    # Soft-deleted asset behaves like an unbound scene; restore recovers it.
    scene.scene_asset_id = structured_asset.id
    structured_asset.deleted_at = utcnow()
    db_session.commit()
    assert resolve_scene_background(db_session, scene) == "老教学楼"
    structured_asset.deleted_at = None
    db_session.commit()
    assert resolve_scene_background(db_session, scene) == compiled


def test_resolve_scene_background_applies_variant_overrides(db_session):
    project = Project(name="变体测试")
    db_session.add(project)
    db_session.flush()
    chapter = Chapter(project_id=project.id, title="第一章", ordinal=1)
    db_session.add(chapter)
    db_session.flush()
    scene = Scene(chapter_id=chapter.id, ordinal=1, location="老教学楼")
    db_session.add(scene)
    db_session.flush()
    asset = SceneAsset(
        project_id=project.id,
        name="教室",
        normalized_name="教室",
        structured={"place": "校园·教学楼", "interior": True, "time_of_day": "day"},
    )
    db_session.add(asset)
    db_session.flush()
    scene.scene_asset_id = asset.id
    db_session.commit()

    plain = resolve_scene_background(db_session, scene)
    assert "白天" in plain

    variant = SceneAssetVariant(
        scene_asset_id=asset.id,
        name="雨后黄昏",
        structured_overrides={"time_of_day": "dusk", "weather": "rain"},
        is_canonical=True,
    )
    db_session.add(variant)
    db_session.flush()
    scene.scene_asset_variant_id = variant.id
    db_session.commit()
    overridden = resolve_scene_background(db_session, scene)
    assert "黄昏" in overridden
    assert "rain" in overridden
    assert "校园·教学楼" in overridden

    # Soft-deleted variant does not participate in compilation.
    variant.deleted_at = utcnow()
    db_session.commit()
    assert "白天" in resolve_scene_background(db_session, scene)


# --- S3: reference binding lifecycle ---------------------------------------


def test_scene_asset_reference_binding_unbinding(client, db_session):
    project = _project(client)
    scene_asset = _create_scene_asset(client, project["id"])
    reference_asset = _reference_asset(db_session, project["id"])

    bound = client.post(
        f"/api/v1/projects/{project['id']}/scene-assets/{scene_asset['id']}/references",
        json={"asset_id": reference_asset.id, "role": "main"},
    )
    assert bound.status_code == 201, bound.text
    assert bound.json()["role"] == "main"

    duplicate = client.post(
        f"/api/v1/projects/{project['id']}/scene-assets/{scene_asset['id']}/references",
        json={"asset_id": reference_asset.id, "role": "main"},
    )
    assert duplicate.status_code == 409

    other_role = client.post(
        f"/api/v1/projects/{project['id']}/scene-assets/{scene_asset['id']}/references",
        json={"asset_id": reference_asset.id, "role": "interior"},
    )
    assert other_role.status_code == 201

    detail = client.get(
        f"/api/v1/projects/{project['id']}/scene-assets/{scene_asset['id']}"
    ).json()
    assert len(detail["references"]) == 2

    other_project = _project(client, "另一个项目")
    foreign_asset = _reference_asset(db_session, other_project["id"])
    cross = client.post(
        f"/api/v1/projects/{project['id']}/scene-assets/{scene_asset['id']}/references",
        json={"asset_id": foreign_asset.id},
    )
    assert cross.status_code == 422

    outfit_asset = _reference_asset(db_session, project["id"], kind="CHARACTER_REFERENCE")
    wrong_kind = client.post(
        f"/api/v1/projects/{project['id']}/scene-assets/{scene_asset['id']}/references",
        json={"asset_id": outfit_asset.id},
    )
    assert wrong_kind.status_code == 409

    generated_asset = _reference_asset(
        db_session,
        project["id"],
        kind="page_candidate",
        source="AI_GENERATED",
        status="GENERATED",
    )
    adopted = client.post(
        f"/api/v1/projects/{project['id']}/scene-assets/{scene_asset['id']}/references",
        json={"asset_id": generated_asset.id},
    )
    assert adopted.status_code == 201

    unbound = client.delete(
        f"/api/v1/projects/{project['id']}/scene-assets/"
        f"{scene_asset['id']}/references/{reference_asset.id}"
    )
    assert unbound.status_code == 204
    assert db_session.get(Asset, reference_asset.id).deleted_at is None
    # Deleting by asset id removes every role binding for that asset.
    again = client.delete(
        f"/api/v1/projects/{project['id']}/scene-assets/"
        f"{scene_asset['id']}/references/{reference_asset.id}"
    )
    assert again.status_code == 204
    missing = client.delete(
        f"/api/v1/projects/{project['id']}/scene-assets/"
        f"{scene_asset['id']}/references/{foreign_asset.id}"
    )
    assert missing.status_code == 404


# --- S4: soft delete, restore and active job guard -------------------------


def test_scene_asset_soft_delete_restore_and_job_guard(client, db_session):
    from app.services.scene_assets import resolve_scene_background

    project = _project(client)
    chapter, plan, scene, page = _chapter_and_page(client, db_session, project["id"])
    scene_asset = _create_scene_asset(client, project["id"])
    reference_asset = _reference_asset(db_session, project["id"])

    bound = client.post(
        f"/api/v1/projects/{project['id']}/scene-assets/{scene_asset['id']}/references",
        json={"asset_id": reference_asset.id},
    )
    assert bound.status_code == 201
    bind = client.patch(
        f"/api/v1/scenes/{scene.id}/bind-asset",
        json={"scene_asset_id": scene_asset["id"]},
    )
    assert bind.status_code == 200

    deleted = client.delete(
        f"/api/v1/projects/{project['id']}/scene-assets/{scene_asset['id']}"
    )
    assert deleted.status_code == 204
    refreshed = db_session.get(Scene, scene.id)
    assert resolve_scene_background(db_session, refreshed) == "老教学楼"

    rebind = client.patch(
        f"/api/v1/scenes/{scene.id}/bind-asset",
        json={"scene_asset_id": scene_asset["id"]},
    )
    assert rebind.status_code == 422

    restored = client.post(
        f"/api/v1/projects/{project['id']}/scene-assets/{scene_asset['id']}/restore"
    )
    assert restored.status_code == 200
    refreshed = db_session.get(Scene, scene.id)
    assert "校园·教学楼" in resolve_scene_background(db_session, refreshed)

    job = GenerationJob(
        project_id=project["id"],
        target_type="PAGE_CANDIDATE",
        target_id="candidate-fake",
        job_type="PAGE_GENERATE",
        status=JobStatus.QUEUED,
    )
    db_session.add(job)
    db_session.flush()
    db_session.add(JobAssetReference(job_id=job.id, asset_id=reference_asset.id))
    db_session.commit()

    blocked = client.delete(
        f"/api/v1/projects/{project['id']}/scene-assets/{scene_asset['id']}"
    )
    assert blocked.status_code == 409
    blocked_asset = client.delete(f"/api/v1/assets/{reference_asset.id}")
    assert blocked_asset.status_code == 409

    job.status = JobStatus.CANCELLED
    db_session.commit()
    assert (
        client.delete(
            f"/api/v1/projects/{project['id']}/scene-assets/{scene_asset['id']}"
        ).status_code
        == 204
    )


# --- S5: scene binding and review marking ---------------------------------


def test_scene_bind_asset_marks_pages_for_review(client, db_session):
    project = _project(client)
    chapter, plan, scene, page = _chapter_and_page(client, db_session, project["id"])
    scene_asset = _create_scene_asset(client, project["id"])
    variant = SceneAssetVariant(
        scene_asset_id=scene_asset["id"],
        name="清晨",
        structured_overrides={"time_of_day": "dawn"},
    )
    db_session.add(variant)
    db_session.commit()

    bind = client.patch(
        f"/api/v1/scenes/{scene.id}/bind-asset",
        json={"scene_asset_id": scene_asset["id"], "scene_asset_variant_id": variant.id},
    )
    assert bind.status_code == 200
    assert bind.json()["scene_asset_id"] == scene_asset["id"]
    assert bind.json()["scene_asset_variant_id"] == variant.id
    assert bind.json()["location"] == "老教学楼"
    db_session.refresh(page)
    assert page.continuity_status == "NEEDS_REVIEW"

    other_asset = _create_scene_asset(client, project["id"], name="其他教室")
    mismatch = client.patch(
        f"/api/v1/scenes/{scene.id}/bind-asset",
        json={"scene_asset_id": other_asset["id"], "scene_asset_variant_id": variant.id},
    )
    assert mismatch.status_code == 422

    orphan = client.patch(
        f"/api/v1/scenes/{scene.id}/bind-asset",
        json={"scene_asset_variant_id": variant.id},
    )
    assert orphan.status_code == 422

    other_project = _project(client, "另一个项目")
    foreign_asset = _create_scene_asset(client, other_project["id"], name="外部教室")
    cross = client.patch(
        f"/api/v1/scenes/{scene.id}/bind-asset",
        json={"scene_asset_id": foreign_asset["id"]},
    )
    assert cross.status_code == 422

    unbound = client.patch(
        f"/api/v1/scenes/{scene.id}/bind-asset",
        json={"scene_asset_id": None, "scene_asset_variant_id": None},
    )
    assert unbound.status_code == 200
    assert unbound.json()["scene_asset_id"] is None
    refreshed = db_session.get(Scene, scene.id)
    assert resolve_scene_background(db_session, refreshed) == "老教学楼"


def test_scene_asset_edit_marks_pages_for_review(client, db_session):
    project = _project(client)
    chapter, plan, scene, page = _chapter_and_page(client, db_session, project["id"])
    scene_asset = _create_scene_asset(client, project["id"])
    client.patch(
        f"/api/v1/scenes/{scene.id}/bind-asset",
        json={"scene_asset_id": scene_asset["id"]},
    )
    db_session.refresh(page)
    page.continuity_status = "PASSED"
    db_session.commit()

    edited = client.patch(
        f"/api/v1/projects/{project['id']}/scene-assets/{scene_asset['id']}",
        json={"description": "更新后的描述", "version": scene_asset["version"]},
    )
    assert edited.status_code == 200
    assert edited.json()["version"] == scene_asset["version"] + 1
    db_session.refresh(page)
    assert page.continuity_status == "NEEDS_REVIEW"


# --- S6: candidate snapshot locks the scene asset version ------------------


def test_candidate_prompt_snapshot_locks_scene_version(
    client, db_session, monkeypatch
):

    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    _skip_page_readiness(monkeypatch)
    project = _project(client)
    chapter, plan, scene, page = _chapter_and_page(client, db_session, project["id"])
    scene_asset = _create_scene_asset(client, project["id"])
    variant = SceneAssetVariant(
        scene_asset_id=scene_asset["id"],
        name="清晨",
        structured_overrides={"time_of_day": "dawn"},
    )
    db_session.add(variant)
    db_session.commit()
    client.patch(
        f"/api/v1/scenes/{scene.id}/bind-asset",
        json={"scene_asset_id": scene_asset["id"], "scene_asset_variant_id": variant.id},
    )

    batch = client.post(f"/api/v1/pages/{page.id}/batches")
    assert batch.status_code == 201
    queued = client.post(
        f"/api/v1/batches/{batch.json()['id']}/candidates",
        json={
            "model_alias": "image.nano_banana_2",
            "resolution": "1K",
            "storyboard_version": page.storyboard_version,
        },
    )
    assert queued.status_code == 202, queued.text
    snapshot = queued.json()["candidate"]["prompt_snapshot"]["scene_asset"]
    assert snapshot["scene_asset_id"] == scene_asset["id"]
    assert snapshot["scene_asset_version"] == scene_asset["version"]
    assert snapshot["scene_asset_variant_id"] == variant.id
    assert snapshot["variant_structured_overrides"] == {"time_of_day": "dawn"}
    candidate_id = queued.json()["candidate"]["id"]

    edited = client.patch(
        f"/api/v1/projects/{project['id']}/scene-assets/{scene_asset['id']}",
        json={"description": "修订后的教室", "version": scene_asset["version"]},
    )
    assert edited.status_code == 200
    assert edited.json()["version"] == scene_asset["version"] + 1

    listed = client.get(f"/api/v1/batches/{batch.json()['id']}/candidates").json()
    frozen = next(item for item in listed if item["id"] == candidate_id)
    assert frozen["prompt_snapshot"]["scene_asset"]["scene_asset_version"] == (
        scene_asset["version"]
    )
    assert frozen["prompt_snapshot"]["scene_asset"]["scene_asset_id"] == (
        scene_asset["id"]
    )

    fresh = scene_asset_snapshot(db_session, page)
    assert fresh["scene_asset_version"] == scene_asset["version"] + 1


# --- S7: scene reference images enter the job lease -------------------------


def test_scene_reference_assets_are_leased_on_candidate_creation(
    client, db_session, monkeypatch
):

    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    _skip_page_readiness(monkeypatch)
    project = _project(client)
    chapter, plan, scene, page = _chapter_and_page(client, db_session, project["id"])
    scene_asset = _create_scene_asset(client, project["id"])
    main_asset = _reference_asset(db_session, project["id"])
    variant_asset = _reference_asset(db_session, project["id"])
    client.post(
        f"/api/v1/projects/{project['id']}/scene-assets/{scene_asset['id']}/references",
        json={"asset_id": main_asset.id, "role": "main"},
    )
    client.post(
        f"/api/v1/projects/{project['id']}/scene-assets/{scene_asset['id']}/references",
        json={"asset_id": variant_asset.id, "role": "subarea_1"},
    )
    variant = SceneAssetVariant(
        scene_asset_id=scene_asset["id"],
        name="雨夜",
        structured_overrides={"weather": "rain"},
    )
    db_session.add(variant)
    db_session.flush()
    db_session.add(
        SceneAssetVariantReference(
            variant_id=variant.id, asset_id=variant_asset.id, role="overview"
        )
    )
    db_session.commit()
    client.patch(
        f"/api/v1/scenes/{scene.id}/bind-asset",
        json={"scene_asset_id": scene_asset["id"], "scene_asset_variant_id": variant.id},
    )

    loaded = scene_reference_assets(db_session, page)
    assert {item.id for item in loaded} == {main_asset.id, variant_asset.id}

    batch = client.post(f"/api/v1/pages/{page.id}/batches")
    assert batch.status_code == 201
    queued = client.post(
        f"/api/v1/batches/{batch.json()['id']}/candidates",
        json={
            "model_alias": "image.nano_banana_2",
            "resolution": "1K",
            "storyboard_version": page.storyboard_version,
        },
    )
    assert queued.status_code == 202, queued.text
    job_id = queued.json()["job_id"]
    leased = set(
        db_session.scalars(
            select(JobAssetReference.asset_id).where(
                JobAssetReference.job_id == job_id
            )
        )
    )
    assert {main_asset.id, variant_asset.id} <= leased

    blocked = client.delete(f"/api/v1/assets/{main_asset.id}")
    assert blocked.status_code == 409
    blocked_variant = client.delete(f"/api/v1/assets/{variant_asset.id}")
    assert blocked_variant.status_code == 409


# --- S8: variant overrides and canonical uniqueness -----------------------


def test_variant_override_keys_and_canonical_uniqueness(client, db_session):
    project = _project(client)
    scene_asset = _create_scene_asset(client, project["id"])

    forbidden = client.post(
        f"/api/v1/projects/{project['id']}/scene-assets/{scene_asset['id']}/variants",
        json={"name": "不可变", "structured_overrides": {"place": "新地点"}},
    )
    assert forbidden.status_code == 422

    first = client.post(
        f"/api/v1/projects/{project['id']}/scene-assets/{scene_asset['id']}/variants",
        json={
            "name": "清晨",
            "structured_overrides": {"time_of_day": "dawn", "weather": "clear"},
            "is_canonical": True,
        },
    )
    assert first.status_code == 201, first.text
    second = client.post(
        f"/api/v1/projects/{project['id']}/scene-assets/{scene_asset['id']}/variants",
        json={
            "name": "雨夜",
            "structured_overrides": {"weather": "rain", "lighting": "dim"},
            "is_canonical": True,
        },
    )
    assert second.status_code == 201

    detail = client.get(
        f"/api/v1/projects/{project['id']}/scene-assets/{scene_asset['id']}"
    ).json()
    canonical_ids = [item["id"] for item in detail["variants"] if item["is_canonical"]]
    assert canonical_ids == [second.json()["id"]]

    stale = client.patch(
        f"/api/v1/projects/{project['id']}/scene-assets/"
        f"{scene_asset['id']}/variants/{first.json()['id']}",
        json={"name": "清晨（修订）", "version": first.json()["version"] + 2},
    )
    assert stale.status_code == 409

    updated = client.patch(
        f"/api/v1/projects/{project['id']}/scene-assets/"
        f"{scene_asset['id']}/variants/{first.json()['id']}",
        json={
            "name": "清晨（修订）",
            "structured_overrides": {"time_of_day": "dawn", "weather": "fog"},
            "version": first.json()["version"],
        },
    )
    assert updated.status_code == 200
    assert updated.json()["structured_overrides"]["weather"] == "fog"

    invalid = client.patch(
        f"/api/v1/projects/{project['id']}/scene-assets/"
        f"{scene_asset['id']}/variants/{first.json()['id']}",
        json={
            "structured_overrides": {"fixed_props": ["讲台"]},
            "version": updated.json()["version"],
        },
    )
    assert invalid.status_code == 422

    deleted = client.delete(
        f"/api/v1/projects/{project['id']}/scene-assets/"
        f"{scene_asset['id']}/variants/{second.json()['id']}"
    )
    assert deleted.status_code == 204
    detail = client.get(
        f"/api/v1/projects/{project['id']}/scene-assets/{scene_asset['id']}"
    ).json()
    deleted_variant = next(
        item for item in detail["variants"] if item["id"] == second.json()["id"]
    )
    assert deleted_variant["deleted_at"] is not None


# --- S9: API boundaries, filters and optimistic locking --------------------


def test_scene_asset_boundaries_and_filters(client, db_session):
    project = _project(client)
    unknown_key = client.post(
        f"/api/v1/projects/{project['id']}/scene-assets",
        json={"name": "非法资产", "structured": {"place": "教室", "bogus": "x"}},
    )
    assert unknown_key.status_code == 422

    invalid_time = client.post(
        f"/api/v1/projects/{project['id']}/scene-assets",
        json={"name": "非法时间", "structured": {"time_of_day": "midnight"}},
    )
    assert invalid_time.status_code == 422

    outside = _create_scene_asset(
        client,
        project["id"],
        name="校外街道",
        structured={
            "place": "校外·街道",
            "interior": False,
            "time_of_day": "night",
            "weather": "fog",
        },
    )
    inside = _create_scene_asset(
        client,
        project["id"],
        name="雨天教室",
        structured={
            "place": "校园·教学楼",
            "interior": True,
            "time_of_day": "night",
            "weather": "rain",
        },
    )

    listed = client.get(f"/api/v1/projects/{project['id']}/scene-assets").json()
    assert len(listed) == 2
    listed_ids = {item["id"] for item in listed}
    assert {outside["id"], inside["id"]} == listed_ids

    place_filtered = client.get(
        f"/api/v1/projects/{project['id']}/scene-assets",
        params={"place": "校园"},
    ).json()
    assert {item["id"] for item in place_filtered} == {inside["id"]}

    interior_filtered = client.get(
        f"/api/v1/projects/{project['id']}/scene-assets",
        params={"interior": True},
    ).json()
    assert {item["id"] for item in interior_filtered} == {inside["id"]}

    paged = client.get(
        f"/api/v1/projects/{project['id']}/scene-assets",
        params={"limit": 1, "offset": 1},
    ).json()
    assert len(paged) == 1

    duplicate = client.post(
        f"/api/v1/projects/{project['id']}/scene-assets",
        json={"name": inside["name"]},
    )
    assert duplicate.status_code == 409

    stale = client.patch(
        f"/api/v1/projects/{project['id']}/scene-assets/{inside['id']}",
        json={"description": "旧版本", "version": inside["version"] + 5},
    )
    assert stale.status_code == 409

    promoted = client.patch(
        f"/api/v1/projects/{project['id']}/scene-assets/{outside['id']}",
        json={"status": "CANONICAL", "version": outside["version"]},
    )
    assert promoted.status_code == 200
    assert promoted.json()["status"] == "CANONICAL"
    canonical = client.get(
        f"/api/v1/projects/{project['id']}/scene-assets",
        params={"status": "CANONICAL"},
    ).json()
    assert [item["id"] for item in canonical] == [outside["id"]]

    deleted = client.delete(
        f"/api/v1/projects/{project['id']}/scene-assets/{inside['id']}"
    )
    assert deleted.status_code == 204
    hidden = client.get(f"/api/v1/projects/{project['id']}/scene-assets").json()
    assert inside["id"] not in {item["id"] for item in hidden}
    visible = client.get(
        f"/api/v1/projects/{project['id']}/scene-assets",
        params={"include_deleted": True, "status": "UPLOADED"},
    ).json()
    assert inside["id"] in {item["id"] for item in visible}

    project_record = db_session.get(Project, project["id"])
    project_record.deleted_at = utcnow()
    db_session.commit()
    missing = client.get(f"/api/v1/projects/{project['id']}/scene-assets")
    assert missing.status_code == 404


def test_scene_asset_restore_refuses_active_name_conflict(client, db_session):
    project = _project(client)
    original = _create_scene_asset(client, project["id"], name="唯一教室")
    deleted = client.delete(
        f"/api/v1/projects/{project['id']}/scene-assets/{original['id']}"
    )
    assert deleted.status_code == 204
    twin = client.post(
        f"/api/v1/projects/{project['id']}/scene-assets",
        json={"name": "唯一教室"},
    )
    assert twin.status_code == 201
    conflicted = client.post(
        f"/api/v1/projects/{project['id']}/scene-assets/{original['id']}/restore"
    )
    assert conflicted.status_code == 409


# --- S10: upload kind and deduplication -----------------------------------


def test_scene_upload_kind_dedup_and_security_guards(
    client, db_session, monkeypatch, tmp_path
):
    monkeypatch.setattr(get_settings(), "upload_root", tmp_path / "uploads")
    monkeypatch.setattr(get_settings(), "storage_root", tmp_path / "storage")
    project = _project(client)

    uploaded = client.post(
        "/api/v1/assets/upload",
        data={"project_id": project["id"], "kind": "scene"},
        files={"file": ("ref.png", _png_bytes(), "image/png")},
    )
    assert uploaded.status_code == 201, uploaded.text
    assert uploaded.json()["kind"] == "SCENE_REFERENCE"

    duplicate = client.post(
        "/api/v1/assets/upload",
        data={"project_id": project["id"], "kind": "scene"},
        files={"file": ("ref-copy.png", _png_bytes(), "image/png")},
    )
    assert duplicate.status_code == 201
    assert duplicate.json()["id"] == uploaded.json()["id"]

    unknown_kind = client.post(
        "/api/v1/assets/upload",
        data={"project_id": project["id"], "kind": "unknown"},
        files={"file": ("ref.png", _png_bytes(), "image/png")},
    )
    assert unknown_kind.status_code == 422

    wrong_type = client.post(
        "/api/v1/assets/upload",
        data={"project_id": project["id"], "kind": "scene"},
        files={"file": ("ref.txt", b"plain text", "text/plain")},
    )
    assert wrong_type.status_code == 415


# --- S11: deleting an asset detaches scene bindings ------------------------


def test_delete_asset_detaches_scene_bindings_and_resets_status(
    client, db_session
):
    project = _project(client)
    scene_asset = _create_scene_asset(client, project["id"])
    main_asset = _reference_asset(db_session, project["id"])
    sub_asset = _reference_asset(db_session, project["id"])
    for asset, role in ((main_asset, "main"), (sub_asset, "overview")):
        bound = client.post(
            f"/api/v1/projects/{project['id']}/scene-assets/"
            f"{scene_asset['id']}/references",
            json={"asset_id": asset.id, "role": role},
        )
        assert bound.status_code == 201, bound.text

    deleted = client.delete(f"/api/v1/assets/{main_asset.id}")
    assert deleted.status_code == 204

    bindings = list(
        db_session.scalars(
            select(SceneAssetReference).where(
                SceneAssetReference.scene_asset_id == scene_asset["id"]
            )
        )
    )
    assert [binding.asset_id for binding in bindings] == [sub_asset.id]
    refreshed = db_session.get(SceneAsset, scene_asset["id"])
    assert refreshed.status.value == "NEEDS_CONFIRMATION"
    assert refreshed.version == scene_asset["version"] + 1

    detail = client.get(
        f"/api/v1/projects/{project['id']}/scene-assets/{scene_asset['id']}"
    ).json()
    assert main_asset.id not in {item["asset_id"] for item in detail["references"]}
    assert main_asset.deleted_at is not None


# --- Regression: storyboard rebuild uses the resolved background -----------


def test_storyboard_panel_background_uses_resolved_scene(client, db_session):
    from app.services.content_workflow import update_page_layout

    project = _project(client)
    chapter, plan, scene, page = _chapter_and_page(client, db_session, project["id"])
    scene_asset = _create_scene_asset(client, project["id"])
    client.patch(
        f"/api/v1/scenes/{scene.id}/bind-asset",
        json={"scene_asset_id": scene_asset["id"]},
    )
    rebuilt = update_page_layout(
        db_session, page, panel_count=page.panel_count, layout_mode="dynamic"
    )
    panels = list(db_session.scalars(select(Panel).where(Panel.page_id == rebuilt.id)))
    assert panels
    assert all("校园·教学楼" in panel.background for panel in panels)

    client.patch(
        f"/api/v1/scenes/{scene.id}/bind-asset",
        json={"scene_asset_id": None, "scene_asset_variant_id": None},
    )
    db_session.commit()
    page = db_session.get(MangaPage, page.id)
    updated = update_page_layout(
        db_session, page, panel_count=page.panel_count, layout_mode="dynamic"
    )
    panels = list(db_session.scalars(select(Panel).where(Panel.page_id == updated.id)))
    assert all(panel.background == "老教学楼" for panel in panels)
