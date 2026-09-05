"""V02-40 director command journal: contract E1–E7 and E9 (SQLite).

E8 (PostgreSQL concurrency) and E10 (real Worker late return) stay NOT RUN.
Real providers are never called.
"""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select

from app.models import (
    Chapter,
    Character,
    Dialogue,
    DirectorCommand,
    DirectorCommandGroup,
    GenerationJob,
    MangaPage,
    ModelCallAttempt,
    Outfit,
    PageCandidate,
    Panel,
    Scene,
)


def _uid() -> str:
    return str(uuid4())


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _setup(client, db_session):
    project = client.post("/api/v1/projects", json={"name": "导演命令"}).json()
    chapter = Chapter(project_id=project["id"], title="第一章", ordinal=1)
    character = Character(project_id=project["id"], primary_name="林澈", aliases=["阿澈"])
    db_session.add_all([chapter, character])
    db_session.flush()
    outfit = Outfit(project_id=project["id"], character_id=character.id, name="校服")
    scene = Scene(
        chapter_id=chapter.id,
        ordinal=1,
        location="客厅",
        weather="小雨",
        time_label="傍晚",
    )
    page = MangaPage(chapter_id=chapter.id, page_number=1, panel_count=3)
    db_session.add_all([outfit, scene, page])
    db_session.flush()
    page.scene_ids = [scene.id]
    panel = Panel(
        page_id=page.id,
        reading_order=1,
        shot_type="medium_close_up",
        camera_angle="eye_level",
        background="室内",
        characters=[character.id],
        character_presence={character.id: "VISIBLE"},
        outfits={character.id: outfit.id},
        actions={"source_text": "站着"},
    )
    db_session.add(panel)
    db_session.flush()
    dialogue = Dialogue(
        panel_id=panel.id,
        target_text="你好",
        reading_order=1,
        speaker_character_id=character.id,
    )
    db_session.add(dialogue)
    db_session.commit()
    db_session.refresh(page)
    db_session.refresh(panel)
    db_session.refresh(dialogue)
    db_session.refresh(scene)
    return {
        "project": project,
        "page": page,
        "panel": panel,
        "dialogue": dialogue,
        "scene": scene,
        "character": character,
        "outfit": outfit,
    }


def _envelope(ctx, operation, payload, *, command_id=None, group_id=None, version=None, extra=None):
    scope = {
        "update_page_layout": "page",
        "update_panel_layout": "panel",
        "update_panel_shot": "panel",
        "update_panel_cast": "panel",
        "update_scene_context": "scene",
        "update_dialogue": "panel",
        "move_dialogue": "panel",
        "regenerate_region": "storyboard",
    }[operation]
    entity = {
        "page": ctx["page"],
        "panel": ctx["panel"],
        "scene": ctx["scene"],
        "storyboard": ctx["page"],
    }[scope]
    value = version if version is not None else (
        entity.storyboard_version if scope == "storyboard" else entity.version
    )
    target = {"project_id": ctx["project"]["id"], "page_id": ctx["page"].id}
    if operation in {
        "update_panel_layout",
        "update_panel_shot",
        "update_panel_cast",
        "update_dialogue",
        "move_dialogue",
    }:
        target["panel_id"] = ctx["panel"].id
    if operation in {"update_dialogue", "move_dialogue"}:
        target["dialogue_id"] = ctx["dialogue"].id
    if operation == "update_scene_context":
        target["scene_id"] = ctx["scene"].id
    body = {
        "schema_version": 1,
        "command_id": command_id or _uid(),
        "command_group_id": group_id or _uid(),
        "created_at": _now(),
        "target": target,
        "expected_version": {"scope": scope, "value": value},
        "operation": operation,
        "payload": payload,
        "source": {"user_prompt": "把镜头拉远一点"},
    }
    if extra:
        body.update(extra)
    return body


def _propose(client, ctx, envelopes):
    group_id = envelopes[0]["command_group_id"]
    return client.post(
        f"/api/v1/projects/{ctx['project']['id']}/director/command-groups",
        json={"command_group_id": group_id, "commands": envelopes},
    )


def test_e1_envelope_rejects_unknown_fields_oversize_and_cross_project(client, db_session):
    ctx = _setup(client, db_session)
    group_id = _uid()
    missing = _envelope(ctx, "update_panel_shot", {"shot_type": "wide"}, group_id=group_id)
    missing.pop("command_id")
    response = _propose(client, ctx, [missing])
    assert response.status_code == 422, response.text

    extra = _envelope(ctx, "update_panel_shot", {"shot_type": "wide"}, group_id=_uid())
    extra["unexpected"] = True
    response = _propose(client, ctx, [extra])
    assert response.status_code == 422, response.text

    other = client.post("/api/v1/projects", json={"name": "其他"}).json()
    stolen = _envelope(ctx, "update_panel_shot", {"shot_type": "wide"}, group_id=_uid())
    stolen["target"]["project_id"] = other["id"]
    response = _propose(client, ctx, [stolen])
    assert response.status_code == 422, response.text

    huge = _envelope(
        ctx,
        "update_panel_shot",
        {"background": "雨" * 20000},
        group_id=_uid(),
    )
    response = _propose(client, ctx, [huge])
    assert response.status_code == 422, response.text


def test_e2_payload_whitelist_rejects_unknown_operation_fields(client, db_session):
    ctx = _setup(client, db_session)
    cases = [
        ("update_panel_shot", {"shot_type": "wide", "zoom": 2}),
        ("update_panel_cast", {"characters": [ctx["character"].id], "mood": "sad"}),
        ("update_panel_layout", {"bleed": True, "rotation": 15}),
        ("update_dialogue", {"target_text": "嗨", "font": "big"}),
        ("move_dialogue", {"reading_order": 1, "spin": 1}),
        ("update_scene_context", {"weather": "大雨", "season": "冬"}),
        ("update_page_layout", {"panel_count": 4, "layout_mode": "dynamic", "theme": "x"}),
        (
            "regenerate_region",
            {"instruction": "雨大一点", "path": "/tmp/mask.png"},
        ),
    ]
    for operation, payload in cases:
        response = _propose(
            client, ctx, [_envelope(ctx, operation, payload, group_id=_uid())]
        )
        assert response.status_code == 422, (operation, response.text)


def test_e3_panel_cast_reuses_existing_validation(client, db_session):
    ctx = _setup(client, db_session)
    foreign = Character(project_id=ctx["project"]["id"], primary_name="路人")
    db_session.add(foreign)
    db_session.commit()

    other_project = client.post("/api/v1/projects", json={"name": "外人"}).json()
    outsider = Character(project_id=other_project["id"], primary_name="外人")
    db_session.add(outsider)
    db_session.commit()

    cross = _propose(
        client,
        ctx,
        [
            _envelope(
                ctx,
                "update_panel_cast",
                {"characters": [outsider.id]},
                group_id=_uid(),
            )
        ],
    )
    assert cross.status_code == 200, cross.text
    assert cross.json()["commands"][0]["status"] == "REJECTED"

    outfit_mismatch = _propose(
        client,
        ctx,
        [
            _envelope(
                ctx,
                "update_panel_cast",
                {"outfits": {foreign.id: ctx["outfit"].id}},
                group_id=_uid(),
            )
        ],
    )
    assert outfit_mismatch.status_code == 200, outfit_mismatch.text
    assert outfit_mismatch.json()["commands"][0]["status"] == "REJECTED"

    ctx["panel"].locked_fields = ["shot_type"]
    db_session.commit()
    locked = _propose(
        client,
        ctx,
        [
            _envelope(
                ctx,
                "update_panel_shot",
                {"shot_type": "wide"},
                group_id=_uid(),
            )
        ],
    )
    assert locked.status_code == 200, locked.text
    assert locked.json()["commands"][0]["status"] == "REJECTED"


def test_e4_stale_version_and_command_id_replay(client, db_session):
    ctx = _setup(client, db_session)
    group_id = _uid()
    command_id = _uid()
    body = _envelope(
        ctx,
        "update_panel_shot",
        {"shot_type": "wide"},
        command_id=command_id,
        group_id=group_id,
    )
    first = _propose(client, ctx, [body])
    assert first.status_code == 200, first.text
    assert first.json()["idempotent_replay"] is False
    replay = _propose(client, ctx, [body])
    assert replay.status_code == 200, replay.text
    assert replay.json()["idempotent_replay"] is True
    assert replay.json()["commands"][0]["command_id"] == command_id

    patch = client.patch(
        f"/api/v1/panels/{ctx['panel'].id}",
        json={"shot_type": "close_up", "version": ctx["panel"].version},
    )
    assert patch.status_code == 200, patch.text
    accept = client.post(
        f"/api/v1/projects/{ctx['project']['id']}/director/commands/{command_id}/accept"
    )
    assert accept.status_code == 409, accept.text
    assert accept.json()["detail"]["current_version"] == patch.json()["version"]


def test_e5_partial_accept_and_reject_group_status(client, db_session):
    ctx = _setup(client, db_session)
    group_id = _uid()
    shot = _envelope(
        ctx, "update_panel_shot", {"shot_type": "wide"}, group_id=group_id
    )
    weather = _envelope(
        ctx, "update_scene_context", {"weather": "大雨"}, group_id=group_id
    )
    proposed = _propose(client, ctx, [shot, weather])
    assert proposed.status_code == 200, proposed.text
    assert proposed.json()["status"] == "PREVIEWED"
    project_id = ctx["project"]["id"]
    reject = client.post(
        f"/api/v1/projects/{project_id}/director/commands/{weather['command_id']}/reject"
    )
    assert reject.status_code == 200, reject.text
    assert reject.json()["status"] == "PREVIEWED"
    accept = client.post(
        f"/api/v1/projects/{project_id}/director/commands/{shot['command_id']}/accept"
    )
    assert accept.status_code == 200, accept.text
    assert accept.json()["status"] == "PARTIALLY_REJECTED"
    statuses = {item["command_id"]: item["status"] for item in accept.json()["commands"]}
    assert statuses[shot["command_id"]] == "EXECUTED"
    assert statuses[weather["command_id"]] == "REJECTED"


def test_e6_metrics_and_storyboard_version_cascade(client, db_session):
    ctx = _setup(client, db_session)
    before_storyboard = ctx["page"].storyboard_version
    before_ack = ctx["page"].selected_candidate_ack_version
    group_id = _uid()
    too_long = _envelope(
        ctx,
        "update_dialogue",
        {"target_text": "字" * 200},
        group_id=group_id,
    )
    proposed = _propose(client, ctx, [too_long])
    assert proposed.status_code == 200, proposed.text
    assert proposed.json()["commands"][0]["status"] == "REJECTED"
    db_session.refresh(ctx["page"])
    db_session.refresh(ctx["panel"])
    db_session.refresh(ctx["dialogue"])
    assert ctx["dialogue"].target_text == "你好"
    assert ctx["page"].storyboard_version == before_storyboard
    assert ctx["page"].selected_candidate_ack_version == before_ack

    ok = _envelope(
        ctx,
        "update_panel_shot",
        {"shot_type": "wide"},
        group_id=_uid(),
    )
    proposed_ok = _propose(client, ctx, [ok])
    assert proposed_ok.status_code == 200, proposed_ok.text
    accepted = client.post(
        f"/api/v1/projects/{ctx['project']['id']}/director/commands/{ok['command_id']}/accept"
    )
    assert accepted.status_code == 200, accepted.text
    db_session.refresh(ctx["page"])
    db_session.refresh(ctx["panel"])
    assert ctx["panel"].shot_type == "wide"
    assert ctx["page"].storyboard_version == before_storyboard + 1
    assert ctx["page"].selected_candidate_ack_version is None
    assert ctx["page"].continuity_status == "NEEDS_REVIEW"


def test_e7_undo_redo_and_superseded_on_concurrent_edit(client, db_session):
    ctx = _setup(client, db_session)
    original = ctx["panel"].shot_type
    shot = _envelope(ctx, "update_panel_shot", {"shot_type": "wide"}, group_id=_uid())
    proposed = _propose(client, ctx, [shot])
    assert proposed.status_code == 200, proposed.text
    project_id = ctx["project"]["id"]
    accepted = client.post(
        f"/api/v1/projects/{project_id}/director/commands/{shot['command_id']}/accept"
    )
    assert accepted.status_code == 200, accepted.text
    db_session.refresh(ctx["panel"])
    assert ctx["panel"].shot_type == "wide"

    undone = client.post(
        f"/api/v1/projects/{project_id}/director/commands/{shot['command_id']}/undo"
    )
    assert undone.status_code == 200, undone.text
    db_session.refresh(ctx["panel"])
    assert ctx["panel"].shot_type == original
    undo_row = next(
        item
        for item in undone.json()["commands"]
        if item["inverse_of_command_id"] == shot["command_id"]
    )
    redone = client.post(
        f"/api/v1/projects/{project_id}/director/commands/{undo_row['command_id']}/redo"
    )
    assert redone.status_code == 200, redone.text
    db_session.refresh(ctx["panel"])
    assert ctx["panel"].shot_type == "wide"

    db_session.refresh(ctx["panel"])
    fresh = _envelope(
        ctx,
        "update_panel_shot",
        {"shot_type": "close_up"},
        group_id=_uid(),
        version=ctx["panel"].version,
    )
    proposed_fresh = _propose(client, ctx, [fresh])
    assert proposed_fresh.status_code == 200, proposed_fresh.text
    accepted_fresh = client.post(
        f"/api/v1/projects/{project_id}/director/commands/{fresh['command_id']}/accept"
    )
    assert accepted_fresh.status_code == 200, accepted_fresh.text
    db_session.refresh(ctx["panel"])
    patch = client.patch(
        f"/api/v1/panels/{ctx['panel'].id}",
        json={"camera_angle": "high", "version": ctx["panel"].version},
    )
    assert patch.status_code == 200, patch.text
    superseded = client.post(
        f"/api/v1/projects/{project_id}/director/commands/{fresh['command_id']}/undo"
    )
    assert superseded.status_code == 409, superseded.text
    assert superseded.json()["detail"]["code"] == "SUPERSEDED"
    db_session.refresh(ctx["panel"])
    assert ctx["panel"].shot_type == "close_up"


def test_e7_cast_undo_restores_outfit_and_expression_side_effects(client, db_session):
    ctx = _setup(client, db_session)
    original_outfits = dict(ctx["panel"].outfits)
    original_expressions = dict(ctx["panel"].expressions)
    extra = Character(project_id=ctx["project"]["id"], primary_name="配角")
    db_session.add(extra)
    db_session.commit()
    ctx["panel"].expressions = {ctx["character"].id: "微笑"}
    db_session.commit()
    original_expressions = dict(ctx["panel"].expressions)
    db_session.refresh(ctx["panel"])
    cast = _envelope(
        ctx,
        "update_panel_cast",
        {"characters": [extra.id]},
        group_id=_uid(),
        version=ctx["panel"].version,
    )
    proposed = _propose(client, ctx, [cast])
    assert proposed.status_code == 200, proposed.text
    accepted = client.post(
        f"/api/v1/projects/{ctx['project']['id']}/director/commands/{cast['command_id']}/accept"
    )
    assert accepted.status_code == 200, accepted.text
    db_session.refresh(ctx["panel"])
    assert ctx["panel"].characters == [extra.id]
    assert ctx["panel"].outfits == {}
    undone = client.post(
        f"/api/v1/projects/{ctx['project']['id']}/director/commands/{cast['command_id']}/undo"
    )
    assert undone.status_code == 200, undone.text
    db_session.refresh(ctx["panel"])
    assert ctx["panel"].characters == [ctx["character"].id]
    assert ctx["panel"].outfits == original_outfits
    assert ctx["panel"].expressions == original_expressions


def test_e7_layout_undo_redo_does_not_empty_the_page(client, db_session):
    ctx = _setup(client, db_session)
    from app.models import DirectorCommand, DirectorCommandGroup
    from app.services.director_commands import _page_snapshot

    before = _page_snapshot(db_session, ctx["page"])
    ctx["panel"].shot_type = "wide"
    db_session.commit()
    after = _page_snapshot(db_session, ctx["page"])
    group = DirectorCommandGroup(
        project_id=ctx["project"]["id"],
        command_group_id=_uid(),
        page_id=ctx["page"].id,
        status="COMMITTED",
    )
    db_session.add(group)
    db_session.flush()
    command = DirectorCommand(
        project_id=ctx["project"]["id"],
        group_id=group.id,
        command_id=_uid(),
        command_group_id=group.command_group_id,
        operation="update_page_layout",
        status="EXECUTED",
        target={"project_id": ctx["project"]["id"], "page_id": ctx["page"].id},
        expected_version={"scope": "page", "value": 1},
        payload={"panel_count": 4, "layout_mode": "dynamic"},
        source={"user_prompt": "改格数"},
        before_snapshot=before,
        storyboard_version_after=ctx["page"].storyboard_version,
    )
    db_session.add(command)
    db_session.commit()
    undone = client.post(
        f"/api/v1/projects/{ctx['project']['id']}/director/commands/{command.command_id}/undo"
    )
    assert undone.status_code == 200, undone.text
    db_session.expire_all()
    restored = db_session.get(Panel, ctx["panel"].id)
    assert restored is not None
    assert restored.shot_type == before["panels"][0]["shot_type"]
    undo_id = next(
        item["command_id"]
        for item in undone.json()["commands"]
        if item["inverse_of_command_id"] == command.command_id
    )
    redone = client.post(
        f"/api/v1/projects/{ctx['project']['id']}/director/commands/{undo_id}/redo"
    )
    assert redone.status_code == 200, redone.text
    db_session.expire_all()
    panels = list(
        db_session.scalars(select(Panel).where(Panel.page_id == ctx["page"].id))
    )
    assert len(panels) == len(after["panels"])
    redone_panel = db_session.get(Panel, ctx["panel"].id)
    assert redone_panel.shot_type == "wide"


def _ensure_mask_capable_model(db_session):
    """Give the preset Vertex image model a declared explicit-mask capability.

    Real provider mask verification is V02-44 (NOT RUN); the offline suite only
    asserts the catalog gate, never a provider call.
    """
    from app.config import get_settings
    from app.models import AIModel
    from app.services.provider_presets import ensure_provider_presets

    ensure_provider_presets(db_session, get_settings(), auto_commit=False)
    db_session.commit()
    model = db_session.scalar(
        select(AIModel).where(AIModel.legacy_alias == "image.nano_banana_2")
    )
    model.capabilities = {
        **(model.capabilities or {}),
        "accepts_explicit_mask": True,
    }
    db_session.commit()
    return model


def test_e9_regenerate_region_fails_closed_without_paid_call(
    client, db_session, tmp_path, monkeypatch
):
    from app.config import get_settings
    from app.models import Asset, CandidateLineage

    monkeypatch.setattr(get_settings(), "storage_root", tmp_path)
    ctx = _setup(client, db_session)
    model = _ensure_mask_capable_model(db_session)

    missing_mask = _envelope(
        ctx,
        "regenerate_region",
        {"instruction": "雨再大一点"},
        group_id=_uid(),
    )
    proposed = _propose(client, ctx, [missing_mask])
    assert proposed.status_code == 200, proposed.text
    assert proposed.json()["commands"][0]["status"] == "REJECTED"
    assert "mask" in str(proposed.json()["commands"][0]["error"]).lower()

    from app.domain.states import Resolution
    from app.models import GenerationBatch

    batch = GenerationBatch(
        project_id=ctx["project"]["id"],
        page_id=ctx["page"].id,
        ordinal=1,
        generation_kind="PAGE",
    )
    db_session.add(batch)
    db_session.flush()
    parent_asset = Asset(
        project_id=ctx["project"]["id"],
        kind="page_candidate",
        original_name="parent.png",
        storage_key="generated/parent.png",
        mime_type="image/png",
        byte_size=10,
        sha256="e" * 64,
        source="VERTEX_GENERATED",
        status="GENERATED",
    )
    db_session.add(parent_asset)
    db_session.flush()
    candidate = PageCandidate(
        batch_id=batch.id,
        page_id=ctx["page"].id,
        ordinal=1,
        model_alias="image.fast",
        resolution=Resolution.DRAFT_1K,
        status="READY",
        asset_id=parent_asset.id,
        deleted_at=datetime.now(UTC),
    )
    db_session.add(candidate)
    ctx["page"].selected_candidate_id = candidate.id
    db_session.commit()
    deleted_parent = _envelope(
        ctx,
        "regenerate_region",
        {
            "instruction": "雨再大一点",
            "mask": [{"points": [[0.1, 0.1], [0.2, 0.1], [0.2, 0.2]]}],
        },
        group_id=_uid(),
    )
    proposed_deleted = _propose(client, ctx, [deleted_parent])
    assert proposed_deleted.status_code == 200, proposed_deleted.text
    assert proposed_deleted.json()["commands"][0]["status"] == "REJECTED"
    db_session.refresh(ctx["page"])
    assert ctx["page"].storyboard_version == 1
    jobs = list(
        db_session.scalars(
            select(GenerationJob).where(GenerationJob.project_id == ctx["project"]["id"])
        )
    )
    attempts = list(db_session.scalars(select(ModelCallAttempt)))
    assert jobs == []
    assert attempts == []

    ctx["page"].selected_candidate_id = None
    live = PageCandidate(
        batch_id=batch.id,
        page_id=ctx["page"].id,
        ordinal=2,
        model_alias="image.fast",
        resolution=Resolution.DRAFT_1K,
        status="READY",
        asset_id=parent_asset.id,
    )
    db_session.add(live)
    db_session.flush()
    ctx["page"].selected_candidate_id = live.id
    db_session.commit()
    parent_before = {
        "asset_id": live.asset_id,
        "prompt_snapshot": live.prompt_snapshot,
        "status": live.status,
        "batch_id": live.batch_id,
        "ordinal": live.ordinal,
        "is_selected": live.is_selected,
        "deleted_at": live.deleted_at,
    }
    no_mask = _envelope(
        ctx,
        "regenerate_region",
        {"instruction": "雨再大一点"},
        group_id=_uid(),
    )
    proposed_no_mask = _propose(client, ctx, [no_mask])
    assert proposed_no_mask.status_code == 200, proposed_no_mask.text
    assert proposed_no_mask.json()["commands"][0]["status"] == "REJECTED"
    assert "mask" in str(proposed_no_mask.json()["commands"][0]["error"]).lower()

    with_mask = _envelope(
        ctx,
        "regenerate_region",
        {
            "instruction": "雨再大一点",
            "mask": [{"points": [[0.1, 0.1], [0.2, 0.1], [0.2, 0.2]]}],
        },
        group_id=_uid(),
    )
    proposed_live = _propose(client, ctx, [with_mask])
    assert proposed_live.status_code == 200, proposed_live.text
    assert proposed_live.json()["commands"][0]["status"] == "PREVIEWED"
    accepted = client.post(
        f"/api/v1/projects/{ctx['project']['id']}/director/commands/"
        f"{with_mask['command_id']}/accept"
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["commands"][0]["status"] == "EXECUTED"

    db_session.refresh(ctx["page"])
    db_session.refresh(live)
    # Parent candidate zero-change and page adoption state unchanged (L2).
    for field, value in parent_before.items():
        assert getattr(live, field) == value, field
    assert ctx["page"].storyboard_version == 1
    assert ctx["page"].selected_candidate_id == live.id

    lineage = db_session.scalar(
        select(CandidateLineage).where(
            CandidateLineage.source_command_id == with_mask["command_id"]
        )
    )
    assert lineage is not None
    assert lineage.parent_candidate_id == live.id
    assert lineage.lineage_kind == "REGION_REGENERATED"
    child = db_session.get(PageCandidate, lineage.child_candidate_id)
    assert child is not None and child.id != live.id
    child_batch = db_session.get(GenerationBatch, child.batch_id)
    assert child_batch.generation_kind == "REGION_REGENERATED"
    assert child_batch.ordinal > batch.ordinal
    assert child.status == "QUEUED"
    mask_asset = db_session.get(Asset, lineage.mask_asset_id)
    assert mask_asset is not None
    assert mask_asset.kind == "region_mask"

    job = db_session.get(GenerationJob, child.job_id)
    assert job is not None
    assert job.job_type == "PAGE_REGION_REGENERATE"
    assert job.request_parameters["mask_asset_id"] == mask_asset.id
    assert job.request_parameters["original_candidate_id"] == live.id
    # The paid worker never runs in the offline suite: no attempt rows exist.
    attempts_after = list(db_session.scalars(select(ModelCallAttempt)))
    assert attempts_after == []
    assert model.capabilities["accepts_explicit_mask"] is True


def test_propose_replays_frozen_first_result_and_duplicate_command_id(client, db_session):
    ctx = _setup(client, db_session)
    group_id = _uid()
    command_id = _uid()
    body = _envelope(
        ctx,
        "update_panel_shot",
        {"shot_type": "wide"},
        command_id=command_id,
        group_id=group_id,
    )
    first = _propose(client, ctx, [body])
    assert first.status_code == 200, first.text
    accepted = client.post(
        f"/api/v1/projects/{ctx['project']['id']}/director/commands/{command_id}/accept"
    )
    assert accepted.status_code == 200, accepted.text
    replay = _propose(client, ctx, [body])
    assert replay.status_code == 200, replay.text
    assert replay.json()["idempotent_replay"] is True
    assert replay.json()["commands"][0]["status"] == "PREVIEWED"

    other_group = _envelope(
        ctx,
        "update_panel_shot",
        {"shot_type": "close_up"},
        command_id=command_id,
        group_id=_uid(),
    )
    other = _propose(client, ctx, [other_group])
    assert other.status_code == 200, other.text
    assert other.json()["idempotent_replay"] is True
    assert other.json()["command_group_id"] == group_id


def test_patch_dialogue_blank_speaker_normalizes_to_none(client, db_session):
    ctx = _setup(client, db_session)
    response = client.patch(
        f"/api/v1/dialogues/{ctx['dialogue'].id}",
        json={"panel_version": ctx["panel"].version, "speaker_character_id": ""},
    )
    assert response.status_code == 200, response.text
    assert response.json()["speaker_character_id"] is None


def test_discard_group_marks_previewed_rows_and_group_discarded(client, db_session):
    ctx = _setup(client, db_session)
    shot = _envelope(ctx, "update_panel_shot", {"shot_type": "wide"}, group_id=_uid())
    proposed = _propose(client, ctx, [shot])
    assert proposed.status_code == 200, proposed.text
    response = client.post(
        f"/api/v1/projects/{ctx['project']['id']}/director/command-groups/"
        f"{shot['command_group_id']}/discard"
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "DISCARDED"
    statuses = {item["command_id"]: item["status"] for item in response.json()["commands"]}
    assert statuses[shot["command_id"]] == "DISCARDED"
    db_session.expire_all()
    row = db_session.scalar(
        select(DirectorCommand).where(DirectorCommand.command_id == shot["command_id"])
    )
    group = db_session.scalar(
        select(DirectorCommandGroup).where(
            DirectorCommandGroup.command_group_id == shot["command_group_id"]
        )
    )
    assert row.status == "DISCARDED"
    assert group.status == "DISCARDED"


def test_undo_claim_blocks_second_undo_running_on_stale_reads(client, db_session):
    """Two concurrent undos of one executed row: the conditional claim decides.

    undo/redo used to be check-then-write with no claim and no lock: both
    concurrent undos passed the bare EXECUTED + sbv gates, then both restored
    the snapshot and inserted an undo row. db_session keeps stale identity-map
    values (expire_on_commit=False); they stand in for the losing undo's reads
    that happened before the winning undo committed, while its writes hit the
    current database exactly like the losing racer would.
    """
    from sqlalchemy.orm import sessionmaker

    from app.services.director_commands import undo_command

    ctx = _setup(client, db_session)
    original = ctx["panel"].shot_type
    shot = _envelope(ctx, "update_panel_shot", {"shot_type": "wide"}, group_id=_uid())
    proposed = _propose(client, ctx, [shot])
    assert proposed.status_code == 200, proposed.text
    accepted = client.post(
        f"/api/v1/projects/{ctx['project']['id']}/director/commands/{shot['command_id']}/accept"
    )
    assert accepted.status_code == 200, accepted.text

    # Reads for the "losing" undo happen now; the competing undo commits its
    # claim and restore before the loser reaches its write.
    stale_row = db_session.scalar(
        select(DirectorCommand).where(DirectorCommand.command_id == shot["command_id"])
    )
    stale_page = db_session.get(MangaPage, ctx["page"].id)
    assert stale_row.status == "EXECUTED"
    sbv_after_accept = stale_page.storyboard_version
    assert stale_row.storyboard_version_after == sbv_after_accept

    ConcurrentSession = sessionmaker(
        bind=db_session.get_bind(), autoflush=False, expire_on_commit=False
    )
    with ConcurrentSession() as other:
        first = undo_command(other, ctx["project"]["id"], shot["command_id"])
    first_undo = next(
        item
        for item in first["commands"]
        if item["inverse_of_command_id"] == shot["command_id"]
    )
    assert first_undo["status"] == "EXECUTED"

    second = client.post(
        f"/api/v1/projects/{ctx['project']['id']}/director/commands/{shot['command_id']}/undo"
    )
    assert second.status_code == 409, second.text
    assert second.json()["detail"]["code"] == "SUPERSEDED"

    db_session.expire_all()
    row = db_session.scalar(
        select(DirectorCommand).where(DirectorCommand.command_id == shot["command_id"])
    )
    assert row.status == "SUPERSEDED"
    undos = list(
        db_session.scalars(
            select(DirectorCommand).where(
                DirectorCommand.inverse_of_command_id == shot["command_id"]
            )
        )
    )
    assert len(undos) == 1
    page = db_session.get(MangaPage, ctx["page"].id)
    assert page.storyboard_version == sbv_after_accept + 1
    panel = db_session.get(Panel, ctx["panel"].id)
    assert panel.shot_type == original


def test_redo_claims_undo_row_and_blocks_second_redo(client, db_session):
    """Redo delegates to undo_command, so it inherits the row claim.

    After a redo the undo row must no longer be EXECUTED (it is claimed to
    SUPERSEDED inside the redo transaction); a second redo of the same undo
    row must be refused instead of re-applying the change twice.
    """
    ctx = _setup(client, db_session)
    shot = _envelope(ctx, "update_panel_shot", {"shot_type": "wide"}, group_id=_uid())
    proposed = _propose(client, ctx, [shot])
    assert proposed.status_code == 200, proposed.text
    project_id = ctx["project"]["id"]
    accepted = client.post(
        f"/api/v1/projects/{project_id}/director/commands/{shot['command_id']}/accept"
    )
    assert accepted.status_code == 200, accepted.text
    undone = client.post(
        f"/api/v1/projects/{project_id}/director/commands/{shot['command_id']}/undo"
    )
    assert undone.status_code == 200, undone.text
    undo_id = next(
        item["command_id"]
        for item in undone.json()["commands"]
        if item["inverse_of_command_id"] == shot["command_id"]
    )
    redone = client.post(
        f"/api/v1/projects/{project_id}/director/commands/{undo_id}/redo"
    )
    assert redone.status_code == 200, redone.text
    db_session.refresh(ctx["panel"])
    assert ctx["panel"].shot_type == "wide"

    db_session.expire_all()
    undo_row = db_session.scalar(
        select(DirectorCommand).where(DirectorCommand.command_id == undo_id)
    )
    assert undo_row.status == "SUPERSEDED"
    sbv_after_redo = db_session.get(MangaPage, ctx["page"].id).storyboard_version

    second = client.post(
        f"/api/v1/projects/{project_id}/director/commands/{undo_id}/redo"
    )
    assert second.status_code == 409, second.text
    redos = list(
        db_session.scalars(
            select(DirectorCommand).where(
                DirectorCommand.inverse_of_command_id == undo_id
            )
        )
    )
    assert len(redos) == 1
    db_session.refresh(ctx["panel"])
    assert ctx["panel"].shot_type == "wide"
    assert db_session.get(MangaPage, ctx["page"].id).storyboard_version == sbv_after_redo


def test_superseded_undo_keeps_concurrent_patch_values(client, db_session):
    """Preservation guard: a stale undo (sbv moved by an intervening PATCH)
    still 409s SUPERSEDED and leaves the concurrent PATCH's values, the
    executed command's values and the storyboard counter untouched."""
    ctx = _setup(client, db_session)
    shot = _envelope(ctx, "update_panel_shot", {"shot_type": "wide"}, group_id=_uid())
    proposed = _propose(client, ctx, [shot])
    assert proposed.status_code == 200, proposed.text
    project_id = ctx["project"]["id"]
    accepted = client.post(
        f"/api/v1/projects/{project_id}/director/commands/{shot['command_id']}/accept"
    )
    assert accepted.status_code == 200, accepted.text
    db_session.refresh(ctx["panel"])

    patch = client.patch(
        f"/api/v1/panels/{ctx['panel'].id}",
        json={"camera_angle": "high", "version": ctx["panel"].version},
    )
    assert patch.status_code == 200, patch.text
    db_session.refresh(ctx["page"])
    sbv_after_patch = ctx["page"].storyboard_version

    superseded = client.post(
        f"/api/v1/projects/{project_id}/director/commands/{shot['command_id']}/undo"
    )
    assert superseded.status_code == 409, superseded.text
    assert superseded.json()["detail"]["code"] == "SUPERSEDED"
    db_session.refresh(ctx["panel"])
    assert ctx["panel"].camera_angle == "high"
    assert ctx["panel"].shot_type == "wide"
    db_session.refresh(ctx["page"])
    assert ctx["page"].storyboard_version == sbv_after_patch
    db_session.expire_all()
    row = db_session.scalar(
        select(DirectorCommand).where(DirectorCommand.command_id == shot["command_id"])
    )
    assert row.status == "SUPERSEDED"


def test_undo_restore_failure_rolls_back_row_claim(client, db_session):
    """A restore failure after a won claim must leave no trace: the claim
    (SUPERSEDED flip), the undo row and any restore writes roll back together
    so the executed row stays undoable."""
    from sqlalchemy.orm import sessionmaker

    ctx = _setup(client, db_session)
    group = DirectorCommandGroup(
        project_id=ctx["project"]["id"],
        command_group_id=_uid(),
        page_id=ctx["page"].id,
        status="COMMITTED",
    )
    db_session.add(group)
    db_session.flush()
    command = DirectorCommand(
        project_id=ctx["project"]["id"],
        group_id=group.id,
        command_id=_uid(),
        command_group_id=group.command_group_id,
        operation="update_page_layout",
        status="EXECUTED",
        target={"project_id": ctx["project"]["id"], "page_id": ctx["page"].id},
        expected_version={"scope": "page", "value": 1},
        payload={"panel_count": 4, "layout_mode": "dynamic"},
        source={"user_prompt": "改格数"},
        before_snapshot=None,
        storyboard_version_after=ctx["page"].storyboard_version,
    )
    db_session.add(command)
    db_session.commit()
    db_session.refresh(ctx["page"])
    sbv_before = ctx["page"].storyboard_version

    response = client.post(
        f"/api/v1/projects/{ctx['project']['id']}/director/commands/"
        f"{command.command_id}/undo"
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "缺少布局快照，无法撤销"
    db_session.rollback()

    ConcurrentSession = sessionmaker(
        bind=db_session.get_bind(), autoflush=False, expire_on_commit=False
    )
    with ConcurrentSession() as other:
        row = other.scalar(
            select(DirectorCommand).where(
                DirectorCommand.command_id == command.command_id
            )
        )
        assert row.status == "EXECUTED"
        stray = other.scalar(
            select(DirectorCommand).where(
                DirectorCommand.inverse_of_command_id == command.command_id
            )
        )
        assert stray is None
        page = other.get(MangaPage, ctx["page"].id)
        assert page.storyboard_version == sbv_before


def test_accept_scene_context_rechecks_panel_background(client, db_session):
    """§6.3 between-preview-and-execution re-check must cover what execution
    writes: update_scene_context is a compound write (scene fields plus
    panel.background), but a panel background PATCH between propose and accept
    moves only panel.version/storyboard_version, not scene.version, so the
    scene.version gate alone silently overwrote the concurrent background."""
    ctx = _setup(client, db_session)
    conflicting = _envelope(
        ctx,
        "update_scene_context",
        {"weather": "大雨", "background": "海边"},
        group_id=_uid(),
    )
    conflicting["target"]["panel_id"] = ctx["panel"].id
    proposed = _propose(client, ctx, [conflicting])
    assert proposed.status_code == 200, proposed.text
    assert proposed.json()["commands"][0]["status"] == "PREVIEWED"

    db_session.refresh(ctx["panel"])
    patch = client.patch(
        f"/api/v1/panels/{ctx['panel'].id}",
        json={"background": "山间", "version": ctx["panel"].version},
    )
    assert patch.status_code == 200, patch.text

    accepted = client.post(
        f"/api/v1/projects/{ctx['project']['id']}/director/commands/"
        f"{conflicting['command_id']}/accept"
    )
    assert accepted.status_code == 409, accepted.text
    assert accepted.json()["detail"]["code"] == "VERSION_CONFLICT"
    assert accepted.json()["detail"]["scope"] == "panel"
    db_session.refresh(ctx["panel"])
    assert ctx["panel"].background == "山间"

    # Control: without an intervening PATCH the same command shape accepts and
    # applies the compound write (scene fields and panel background).
    db_session.refresh(ctx["scene"])
    control = _envelope(
        ctx,
        "update_scene_context",
        {"weather": "大雨", "background": "海滩"},
        group_id=_uid(),
    )
    control["target"]["panel_id"] = ctx["panel"].id
    control_proposed = _propose(client, ctx, [control])
    assert control_proposed.status_code == 200, control_proposed.text
    control_accepted = client.post(
        f"/api/v1/projects/{ctx['project']['id']}/director/commands/"
        f"{control['command_id']}/accept"
    )
    assert control_accepted.status_code == 200, control_accepted.text
    db_session.refresh(ctx["panel"])
    db_session.refresh(ctx["scene"])
    assert ctx["panel"].background == "海滩"
    assert ctx["scene"].weather == "大雨"


def test_discard_group_keeps_concurrently_executed_row_undoable(client, db_session):
    """discard must not overwrite a row that accept claimed after discard's read.

    accept_command claims its row with a conditional UPDATE (and 409s when a
    discard landed first), but discard wrote DISCARDED via a bare ORM setattr,
    so under READ COMMITTED a concurrent accept (PREVIEWED -> ACCEPTED ->
    EXECUTED) between discard's read and commit was overwritten to DISCARDED,
    making undo_command permanently impossible. The stale PREVIEWED snapshot
    that db_session keeps (expire_on_commit=False) stands in for discard's
    read that happens before the concurrent accept commits.
    """
    from sqlalchemy.orm import sessionmaker

    from app.services.director_commands import accept_command

    ctx = _setup(client, db_session)
    shot = _envelope(ctx, "update_panel_shot", {"shot_type": "wide"}, group_id=_uid())
    proposed = _propose(client, ctx, [shot])
    assert proposed.status_code == 200, proposed.text

    stale = db_session.scalar(
        select(DirectorCommand).where(DirectorCommand.command_id == shot["command_id"])
    )
    assert stale.status == "PREVIEWED"

    # Concurrent transaction: another session claims and executes the command
    # exactly the way accept_command does (real panel write + storyboard bump).
    ConcurrentSession = sessionmaker(
        bind=db_session.get_bind(), autoflush=False, expire_on_commit=False
    )
    with ConcurrentSession() as other:
        accept_command(other, ctx["project"]["id"], shot["command_id"])

    response = client.post(
        f"/api/v1/projects/{ctx['project']['id']}/director/command-groups/"
        f"{shot['command_group_id']}/discard"
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "DISCARDED"
    statuses = {item["command_id"]: item["status"] for item in response.json()["commands"]}
    assert statuses[shot["command_id"]] == "EXECUTED"

    db_session.expire_all()
    row = db_session.scalar(
        select(DirectorCommand).where(DirectorCommand.command_id == shot["command_id"])
    )
    assert row.status == "EXECUTED"

    undone = client.post(
        f"/api/v1/projects/{ctx['project']['id']}/director/commands/"
        f"{shot['command_id']}/undo"
    )
    assert undone.status_code == 200, undone.text
    db_session.refresh(ctx["panel"])
    assert ctx["panel"].shot_type == "medium_close_up"
    assert any(
        item["inverse_of_command_id"] == shot["command_id"]
        for item in undone.json()["commands"]
    )
