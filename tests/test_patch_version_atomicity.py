"""Entity PATCH routes must claim their row atomically (read-version race).

Every route that guards a PATCH/approval on a client-supplied ``version``
used to do read -> in-memory compare -> unconditional write. Two concurrent
writers carrying the same version both passed the compare and both wrote: the
second silently overwrote the first and the version counter collapsed. The fix
turns each guard into a conditional claim UPDATE (``WHERE id AND version ==
expected``) whose rowcount decides between write and 409 — the same pattern as
``_claim_panel_version`` (storyboard) and the scene-asset PATCH.

These tests reproduce the lost update in-process: the shared ``db_session``
keeps a stale identity-map copy of the row (``expire_on_commit=False``) after
a concurrent session has bumped the row and committed, so the route's
``db.get`` observes exactly what the losing racer read before the winner
committed, while its writes land on the current database.
"""

from sqlalchemy.orm import sessionmaker

from app.domain.states import Resolution
from app.models import (
    Asset,
    AssetCandidate,
    Beat,
    Chapter,
    Character,
    GenerationBatch,
    Outfit,
    Project,
    Scene,
    StyleProfile,
    WorkflowDefinition,
    WorkflowVersion,
)


def _project(client, name):
    return client.post("/api/v1/projects", json={"name": name}).json()


def _concurrent_bump(db_session, model, entity_id, **values):
    """Commit a concurrent writer's version bump and return the new version.

    Runs on a separate Session bound to the same engine: the winner commits
    its field write together with ``version = old + 1`` before the route under
    test reaches its own write.
    """
    factory = sessionmaker(
        bind=db_session.get_bind(), autoflush=False, expire_on_commit=False
    )
    with factory() as other:
        row = other.get(model, entity_id)
        assert row is not None
        for key, value in values.items():
            setattr(row, key, value)
        row.version += 1
        other.commit()
        return row.version


def _assert_lost_update(db_session, model, entity_id, winner_version, winner_values):
    db_session.expire_all()
    row = db_session.get(model, entity_id)
    for key, value in winner_values.items():
        assert getattr(row, key) == value
    assert row.version == winner_version


def test_scene_patch_lost_update_returns_409(client, db_session):
    """PATCH /scenes/{id}: a stale racer must 409 instead of overwriting."""
    project = _project(client, "并发场景")
    chapter = Chapter(project_id=project["id"], ordinal=1, title="第一章")
    db_session.add(chapter)
    db_session.flush()
    scene = Scene(chapter_id=chapter.id, ordinal=1, location="初始地点")
    db_session.add(scene)
    db_session.commit()

    winner_version = _concurrent_bump(db_session, Scene, scene.id, location="并发修改")

    response = client.patch(
        f"/api/v1/scenes/{scene.id}",
        json={"version": 1, "location": "本次修改"},
    )

    assert response.status_code == 409, response.text
    _assert_lost_update(
        db_session, Scene, scene.id, winner_version, {"location": "并发修改"}
    )


def test_beat_patch_lost_update_returns_409(client, db_session):
    """PATCH /beats/{id}: a stale racer must 409 instead of overwriting."""
    project = _project(client, "并发情节拍")
    chapter = Chapter(project_id=project["id"], ordinal=1, title="第一章")
    db_session.add(chapter)
    db_session.flush()
    scene = Scene(chapter_id=chapter.id, ordinal=1)
    db_session.add(scene)
    db_session.flush()
    beat = Beat(scene_id=scene.id, ordinal=1, dialogue="初始台词")
    db_session.add(beat)
    db_session.commit()

    winner_version = _concurrent_bump(db_session, Beat, beat.id, dialogue="并发台词")

    response = client.patch(
        f"/api/v1/beats/{beat.id}",
        json={"version": 1, "dialogue": "本次台词"},
    )

    assert response.status_code == 409, response.text
    _assert_lost_update(
        db_session, Beat, beat.id, winner_version, {"dialogue": "并发台词"}
    )


def test_character_patch_lost_update_returns_409(client, db_session):
    """PATCH /characters/{id}: a stale racer must 409 instead of overwriting."""
    project = _project(client, "并发角色")
    character = Character(
        project_id=project["id"],
        primary_name="张三",
        canonical_description="初始设定",
    )
    db_session.add(character)
    db_session.commit()

    winner_version = _concurrent_bump(
        db_session, Character, character.id, canonical_description="并发设定"
    )

    response = client.patch(
        f"/api/v1/characters/{character.id}",
        json={"version": 1, "canonical_description": "本次设定"},
    )

    assert response.status_code == 409, response.text
    _assert_lost_update(
        db_session,
        Character,
        character.id,
        winner_version,
        {"canonical_description": "并发设定"},
    )


def test_outfit_patch_lost_update_returns_409(client, db_session):
    """PATCH /outfits/{id}: a stale racer must 409 instead of overwriting."""
    project = _project(client, "并发服装")
    character = Character(project_id=project["id"], primary_name="李四")
    db_session.add(character)
    db_session.flush()
    outfit = Outfit(
        project_id=project["id"],
        character_id=character.id,
        name="初始服装",
    )
    db_session.add(outfit)
    db_session.commit()

    winner_version = _concurrent_bump(db_session, Outfit, outfit.id, name="并发服装")

    response = client.patch(
        f"/api/v1/outfits/{outfit.id}",
        json={"version": 1, "name": "本次服装"},
    )

    assert response.status_code == 409, response.text
    _assert_lost_update(
        db_session, Outfit, outfit.id, winner_version, {"name": "并发服装"}
    )


def test_style_patch_lost_update_returns_409(client, db_session):
    """PATCH /styles/{id}: a stale racer must 409 instead of overwriting."""
    project = _project(client, "并发风格")
    style = StyleProfile(project_id=project["id"], name="初始风格")
    db_session.add(style)
    db_session.commit()

    winner_version = _concurrent_bump(db_session, StyleProfile, style.id, name="并发风格")

    response = client.patch(
        f"/api/v1/styles/{style.id}",
        json={"version": 1, "name": "本次风格"},
    )

    assert response.status_code == 409, response.text
    _assert_lost_update(
        db_session, StyleProfile, style.id, winner_version, {"name": "并发风格"}
    )


def test_style_palette_approve_lost_update_returns_409(client, db_session):
    """POST /styles/{id}/palette-approve: the winner's profile must survive."""
    project = _project(client, "并发色板")
    style = StyleProfile(project_id=project["id"], name="彩色风格", color_mode="color")
    db_session.add(style)
    db_session.commit()

    winner_profile = {"owner": "concurrent", "palette_confirmed": True}
    winner_version = _concurrent_bump(
        db_session, StyleProfile, style.id, profile=winner_profile
    )

    response = client.post(
        f"/api/v1/styles/{style.id}/palette-approve",
        json={"palette": {"rows": ["#111111"]}, "version": 1},
    )

    assert response.status_code == 409, response.text
    _assert_lost_update(
        db_session, StyleProfile, style.id, winner_version, {"profile": winner_profile}
    )


def test_style_test_approve_lost_update_returns_409(client, db_session):
    """POST /styles/{id}/style-test-approve: the winner's profile must survive."""
    project = _project(client, "并发风格测试图")
    style = StyleProfile(project_id=project["id"], name="测试风格", color_mode="color")
    asset = Asset(
        project_id=project["id"],
        kind="STYLE_REFERENCE",
        original_name="style.png",
        storage_key="patch-version-atomicity/style.png",
        mime_type="image/png",
        byte_size=1,
        sha256="patch-version-atomicity-style-test",
    )
    db_session.add_all([style, asset])
    db_session.flush()
    batch = GenerationBatch(
        project_id=project["id"],
        ordinal=1,
        target_type="STYLE",
        target_id=style.id,
        generation_kind="STYLE_TEST",
    )
    db_session.add(batch)
    db_session.flush()
    candidate = AssetCandidate(
        batch_id=batch.id,
        ordinal=1,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        variant="STYLE_TEST",
        status="READY",
        asset_id=asset.id,
        prompt_snapshot={},
    )
    db_session.add(candidate)
    db_session.commit()

    winner_profile = {"owner": "concurrent"}
    winner_version = _concurrent_bump(
        db_session, StyleProfile, style.id, profile=winner_profile
    )

    response = client.post(
        f"/api/v1/styles/{style.id}/style-test-approve",
        json={"candidate_id": candidate.id, "approved": True, "version": 1},
    )

    assert response.status_code == 409, response.text
    _assert_lost_update(
        db_session, StyleProfile, style.id, winner_version, {"profile": winner_profile}
    )


def test_project_patch_lost_update_returns_409(client, db_session):
    """PATCH /projects/{id}: a stale racer must 409 instead of overwriting."""
    project = _project(client, "初始项目")
    # Keep the row pinned in the (weak) identity map so the route's db.get
    # below observes the stale pre-bump read, exactly like a losing racer
    # that read before the concurrent writer committed.
    pinned = db_session.get(Project, project["id"])
    assert pinned is not None and pinned.version == 1

    winner_version = _concurrent_bump(
        db_session, Project, project["id"], name="并发项目名"
    )

    response = client.patch(
        f"/api/v1/projects/{project['id']}",
        json={"version": 1, "name": "本次项目名"},
    )

    assert response.status_code == 409, response.text
    _assert_lost_update(
        db_session, Project, project["id"], winner_version, {"name": "并发项目名"}
    )


def test_workflow_patch_lost_update_returns_409(client, db_session):
    """PATCH /workflows/{id}: a stale racer must 409 instead of overwriting."""
    project = _project(client, "并发工作流")
    workflow = client.post(
        f"/api/v1/projects/{project['id']}/workflows",
        json={"name": "初始工作流", "template": "blank"},
    ).json()
    # Pin the row in the (weak) identity map so the route observes the stale
    # pre-bump read, exactly like a losing racer that read before the
    # concurrent writer committed.
    pinned = db_session.get(WorkflowDefinition, workflow["id"])
    assert pinned is not None and pinned.version == 1

    winner_version = _concurrent_bump(
        db_session, WorkflowDefinition, workflow["id"], name="并发工作流名"
    )

    response = client.patch(
        f"/api/v1/workflows/{workflow['id']}",
        json={"version": 1, "name": "本次工作流名"},
    )

    assert response.status_code == 409, response.text
    _assert_lost_update(
        db_session,
        WorkflowDefinition,
        workflow["id"],
        winner_version,
        {"name": "并发工作流名"},
    )


def test_workflow_restore_lost_update_returns_409(client, db_session):
    """POST /workflow-versions/{id}/restore: must not clobber a newer draft.

    The restore replays an OLD graph, so its claim predicate on the workflow
    version is what stops a concurrent edit (or another restore) from being
    silently overwritten by the stale snapshot.
    """
    project = _project(client, "并发恢复")
    workflow = client.post(
        f"/api/v1/projects/{project['id']}/workflows",
        json={"name": "待恢复工作流", "template": "blank"},
    ).json()
    # Pin the workflow row in the (weak) identity map so the restore route
    # observes the stale pre-bump read, exactly like a losing racer that read
    # before the concurrent writer committed.
    pinned = db_session.get(WorkflowDefinition, workflow["id"])
    assert pinned is not None and pinned.version == 1
    version_row = WorkflowVersion(
        workflow_id=workflow["id"],
        revision=1,
        graph={"marker": "published-snapshot"},
        graph_checksum="patch-version-atomicity",
        validation_report={},
    )
    db_session.add(version_row)
    db_session.commit()

    winner_graph = {"marker": "并发草稿"}
    winner_version = _concurrent_bump(
        db_session, WorkflowDefinition, workflow["id"], draft_graph=winner_graph
    )

    response = client.post(
        f"/api/v1/workflow-versions/{version_row.id}/restore",
        json={"version": 1},
    )

    assert response.status_code == 409, response.text
    _assert_lost_update(
        db_session,
        WorkflowDefinition,
        workflow["id"],
        winner_version,
        {"draft_graph": winner_graph},
    )
