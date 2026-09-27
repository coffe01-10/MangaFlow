"""DIR-01B natural-language → director command parse (contract A1–A9).

Isolated SQLite; the text model is a FakeAdapter behind the
``worker_tasks._adapter`` seam — no real provider is ever called.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.config import get_settings
from app.domain.states import JobStatus
from app.models import (
    Chapter,
    Character,
    Dialogue,
    DirectorCommand,
    DirectorCommandGroup,
    GenerationJob,
    MangaPage,
    Outfit,
    Panel,
    Scene,
)
from app.services.ai_schemas import DirectorParseOutput
from app.services.worker_handlers.director_parse import _run_director_parse
from sqlalchemy import select


def _uid() -> str:
    return str(uuid4())


def _setup(client, db_session, *, dialogue_count: int = 1):
    project = client.post("/api/v1/projects", json={"name": "导演解析"}).json()
    chapter = Chapter(project_id=project["id"], title="第一章", ordinal=1)
    character = Character(project_id=project["id"], primary_name="林澈", aliases=["阿澈"])
    db_session.add_all([chapter, character])
    db_session.flush()
    outfit = Outfit(project_id=project["id"], character_id=character.id, name="校服")
    scene = Scene(
        chapter_id=chapter.id, ordinal=1, location="客厅", weather="小雨",
        time_label="傍晚",
    )
    page = MangaPage(chapter_id=chapter.id, page_number=1, panel_count=3)
    db_session.add_all([outfit, scene, page])
    db_session.flush()
    page.scene_ids = [scene.id]
    panels = []
    for index in range(1, 4):
        panel = Panel(
            page_id=page.id,
            reading_order=index,
            shot_type="medium_close_up",
            camera_angle="eye_level",
        )
        db_session.add(panel)
        panels.append(panel)
    db_session.flush()
    dialogues = []
    for index in range(dialogue_count):
        dialogue = Dialogue(
            panel_id=panels[0].id,
            target_text=f"台词{index + 1}",
            reading_order=index + 1,
            speaker_character_id=character.id,
        )
        db_session.add(dialogue)
        dialogues.append(dialogue)
    db_session.commit()
    for row in (page, *panels, *dialogues, scene, character, outfit):
        db_session.refresh(row)
    return {
        "project": project,
        "page": page,
        "panels": panels,
        "dialogues": dialogues,
        "scene": scene,
        "character": character,
        "outfit": outfit,
    }


def _output(commands=None, clarifications=None, unsupported=None):
    return DirectorParseOutput(
        commands=commands or [],
        clarifications=clarifications or [],
        unsupported=unsupported or [],
    )


def _install_adapter(monkeypatch, output=None, error=None):
    class FakeAdapter:
        def generate_structured(self, request, schema):
            if error is not None:
                raise error
            return output

    monkeypatch.setattr("app.worker_tasks._adapter", lambda alias: FakeAdapter())


def _post_utterance(client, ctx, utterance="把第二格改成特写", **overrides):
    body = {
        "utterance": utterance,
        "page_id": ctx["page"].id,
        "storyboard_version": ctx["page"].storyboard_version,
        "client_request_id": _uid(),
    }
    body.update(overrides)
    return client.post(
        f"/api/v1/projects/{ctx['project']['id']}/director/utterances",
        json=body,
    )


def _run_group(db_session, project_id, command_group_id):
    group = db_session.scalar(
        select(DirectorCommandGroup).where(
            DirectorCommandGroup.command_group_id == command_group_id
        )
    )
    job = db_session.scalar(
        select(GenerationJob).where(
            GenerationJob.target_id == group.id,
            GenerationJob.job_type == "DIRECTOR_PARSE",
        )
    )
    # Mirror execute_job's claim seam: db.info keys + claimed in-flight row
    # (attempt_count >= 1 is a ModelCallAttempt CHECK constraint).
    job.status = JobStatus.GENERATING
    job.lease_owner = "test"
    job.lease_expires_at = datetime.now(UTC) + timedelta(minutes=5)
    job.attempt_count = 1
    db_session.flush()
    db_session.info["job_id"] = job.id
    db_session.info["job_lease_owner"] = "test"
    _run_director_parse(db_session, job)
    db_session.commit()
    db_session.refresh(group)
    return group


def test_a1_unique_anchor_previews_command(client, db_session, monkeypatch):
    """A1: '把第二格改成特写' → PREVIEWED update_panel_shot with panel version."""
    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    ctx = _setup(client, db_session)
    _install_adapter(
        monkeypatch,
        _output(
            commands=[
                {
                    "op_ref": "c1",
                    "operation": "update_panel_shot",
                    "target_hint": {"panel": 2},
                    "payload": {"shot_type": "close_up"},
                }
            ]
        ),
    )
    response = _post_utterance(client, ctx)
    assert response.status_code == 202, response.text
    command_group_id = response.json()["command_group_id"]

    group = _run_group(db_session, ctx["project"]["id"], command_group_id)
    assert group.status == "PREVIEWED", group.first_result
    rows = list(
        db_session.scalars(
            select(DirectorCommand).where(DirectorCommand.group_id == group.id)
        )
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.status == "PREVIEWED"
    assert row.operation == "update_panel_shot"
    assert row.target["panel_id"] == ctx["panels"][1].id
    assert row.expected_version == {
        "scope": "panel",
        "value": ctx["panels"][1].version,
    }
    assert row.payload == {"shot_type": "close_up"}
    assert row.diff is not None
    assert group.first_result["kind"] == "ready"
    assert group.first_result["model_call_attempt_id"]


def test_a2_ambiguous_dialogue_needs_clarification(client, db_session, monkeypatch):
    """A2: dialogue edit with no anchor + multiple dialogues → clarify."""
    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    ctx = _setup(client, db_session, dialogue_count=2)
    _install_adapter(
        monkeypatch,
        _output(
            commands=[
                {
                    "op_ref": "c1",
                    "operation": "update_dialogue",
                    "target_hint": {},
                    "payload": {"target_text": "新台词"},
                }
            ]
        ),
    )
    response = _post_utterance(client, ctx, "把台词改成：快点走")
    assert response.status_code == 202, response.text
    group = _run_group(
        db_session, ctx["project"]["id"], response.json()["command_group_id"]
    )
    assert group.status == "NEEDS_CLARIFICATION"
    options = group.first_result["clarify_options"]
    assert {o["kind"] for o in options} == {"panel"} or "dialogue" in {
        o["kind"] for o in options
    }
    assert (
        db_session.scalar(
            select(DirectorCommand).where(DirectorCommand.group_id == group.id)
        )
        is None
    )


def test_a3_bad_rows_land_rejected_others_preview(client, db_session, monkeypatch):
    """A3: schema-invalid + unknown-target rows are REJECTED; valid one previews."""
    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    ctx = _setup(client, db_session)
    _install_adapter(
        monkeypatch,
        _output(
            commands=[
                {
                    "op_ref": "c1",
                    "operation": "update_panel_shot",
                    "target_hint": {"panel": 1},
                    "payload": {"shot_type": "close_up"},
                },
                {
                    "op_ref": "c2",
                    "operation": "update_panel_shot",
                    "target_hint": {"panel": 99},
                    "payload": {"shot_type": "close_up"},
                },
                {
                    "op_ref": "c3",
                    "operation": "update_panel_layout",
                    "target_hint": {"panel": 3},
                    "payload": {"bogus_field": True},
                },
            ]
        ),
    )
    response = _post_utterance(client, ctx)
    group = _run_group(
        db_session, ctx["project"]["id"], response.json()["command_group_id"]
    )
    rows = list(
        db_session.scalars(
            select(DirectorCommand)
            .where(DirectorCommand.group_id == group.id)
            .order_by(DirectorCommand.created_at)
        )
    )
    assert len(rows) == 3
    assert rows[0].status == "PREVIEWED"
    assert rows[1].status == "REJECTED"
    assert rows[1].error["code"] == "TARGET_UNRESOLVED"
    assert rows[2].status == "REJECTED"
    assert rows[2].error["code"] == "SCHEMA"
    assert group.first_result["commands_failed"] == 2


def test_a4_stale_storyboard_version(client, db_session, monkeypatch):
    """A4: request anchor behind live storyboard_version → STALE, zero rows."""
    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    ctx = _setup(client, db_session)
    _install_adapter(monkeypatch, _output(commands=[]))
    response = _post_utterance(
        client, ctx, storyboard_version=ctx["page"].storyboard_version + 5
    )
    group = _run_group(
        db_session, ctx["project"]["id"], response.json()["command_group_id"]
    )
    assert group.status == "STALE"
    assert group.first_result["current_version"] == ctx["page"].storyboard_version


def test_a5_idempotent_replay(client, db_session, monkeypatch):
    """A5: same client_request_id returns the same group, one job only."""
    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    ctx = _setup(client, db_session)
    request_id = _uid()
    first = _post_utterance(client, ctx, client_request_id=request_id)
    second = _post_utterance(client, ctx, client_request_id=request_id)
    assert first.status_code == 202 and second.status_code == 202
    assert first.json()["command_group_id"] == second.json()["command_group_id"]
    assert second.json()["idempotent_replay"] is True
    jobs = list(
        db_session.scalars(
            select(GenerationJob).where(GenerationJob.job_type == "DIRECTOR_PARSE")
        )
    )
    assert len(jobs) == 1


def test_a6_same_page_parse_in_flight(client, db_session, monkeypatch):
    """A6: second parse for the same page while one is active → 409."""
    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    ctx = _setup(client, db_session)
    first = _post_utterance(client, ctx)
    assert first.status_code == 202
    second = _post_utterance(client, ctx, "换一个指令")
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "PARSE_IN_FLIGHT"


def test_a7_model_failure_marks_parse_failed(client, db_session, monkeypatch):
    """A7: adapter error → PARSE_FAILED with reason, no command rows."""
    from app.model_adapters.base import ProviderAdapterError

    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    ctx = _setup(client, db_session)
    _install_adapter(
        monkeypatch,
        error=ProviderAdapterError(
            "TIMEOUT", "模型调用超时", retryable=True, retry_after_seconds=30
        ),
    )
    response = _post_utterance(client, ctx)
    group = _run_group(
        db_session, ctx["project"]["id"], response.json()["command_group_id"]
    )
    assert group.status == "PARSE_FAILED"
    assert group.first_result["kind"] == "error"
    assert group.first_result["error"]["code"] == "TIMEOUT"
    assert (
        db_session.scalar(
            select(DirectorCommand).where(DirectorCommand.group_id == group.id)
        )
        is None
    )


def test_a8_regenerate_request_is_unsupported(client, db_session, monkeypatch):
    """A8: '重画这一格' never mints a paid derived job — clarify outcome."""
    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    ctx = _setup(client, db_session)
    _install_adapter(
        monkeypatch,
        _output(
            commands=[
                {
                    "op_ref": "c1",
                    "operation": "regenerate_region",
                    "target_hint": {"panel": 1},
                    "payload": {"instruction": "重画"},
                }
            ],
            unsupported=[{"reason": "局部重绘需走修复流程", "excerpt": "重画这一格"}],
        ),
    )
    response = _post_utterance(client, ctx, "把第一格重画一下")
    group = _run_group(
        db_session, ctx["project"]["id"], response.json()["command_group_id"]
    )
    assert group.status == "NEEDS_CLARIFICATION"
    assert "修复" in group.first_result["reason"] or any(
        "修复" in (o.get("label") or "")
        for o in group.first_result["clarify_options"]
    )
    # No command rows, and no derived-generation jobs were created.
    assert (
        db_session.scalar(
            select(DirectorCommand).where(DirectorCommand.group_id == group.id)
        )
        is None
    )
    assert (
        db_session.scalar(
            select(GenerationJob).where(
                GenerationJob.job_type != "DIRECTOR_PARSE"
            )
        )
        is None
    )


def test_a9_parsed_commands_execute_via_existing_accept(
    client, db_session, monkeypatch
):
    """A9: an NL-previewed command accepts through the unchanged accept path."""
    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    ctx = _setup(client, db_session)
    _install_adapter(
        monkeypatch,
        _output(
            commands=[
                {
                    "op_ref": "c1",
                    "operation": "update_panel_shot",
                    "target_hint": {"panel": 2},
                    "payload": {"shot_type": "close_up", "camera_angle": "low_angle"},
                }
            ]
        ),
    )
    response = _post_utterance(client, ctx)
    group = _run_group(
        db_session, ctx["project"]["id"], response.json()["command_group_id"]
    )
    row = db_session.scalar(
        select(DirectorCommand).where(DirectorCommand.group_id == group.id)
    )
    accepted = client.post(
        f"/api/v1/projects/{ctx['project']['id']}/director/commands/"
        f"{row.command_id}/accept"
    )
    assert accepted.status_code == 200, accepted.text
    db_session.refresh(ctx["panels"][1])
    assert ctx["panels"][1].shot_type == "close_up"
    assert ctx["panels"][1].camera_angle == "low_angle"


def test_payload_character_names_resolve(client, db_session, monkeypatch):
    """update_panel_cast payload names → character/outfit ids before validate."""
    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    ctx = _setup(client, db_session)
    _install_adapter(
        monkeypatch,
        _output(
            commands=[
                {
                    "op_ref": "c1",
                    "operation": "update_panel_cast",
                    "target_hint": {"panel": 1},
                    "payload": {
                        "characters": ["阿澈"],
                        "outfits": {"林澈": "校服"},
                    },
                }
            ]
        ),
    )
    response = _post_utterance(client, ctx)
    group = _run_group(
        db_session, ctx["project"]["id"], response.json()["command_group_id"]
    )
    row = db_session.scalar(
        select(DirectorCommand).where(DirectorCommand.group_id == group.id)
    )
    assert row.status == "PREVIEWED", row.error
    assert row.payload["characters"] == [ctx["character"].id]
    assert row.payload["outfits"] == {ctx["character"].id: ctx["outfit"].id}


def test_utterance_validation(client, db_session, monkeypatch):
    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    ctx = _setup(client, db_session)
    missing = client.post(
        f"/api/v1/projects/{ctx['project']['id']}/director/utterances",
        json={"utterance": "x", "page_id": ctx["page"].id},
    )
    assert missing.status_code == 422
    bad_page = _post_utterance(client, ctx, page_id=_uid())
    assert bad_page.status_code == 422
    other_project = client.post("/api/v1/projects", json={"name": "越权"}).json()
    cross = client.post(
        f"/api/v1/projects/{other_project['id']}/director/utterances",
        json={
            "utterance": "改格",
            "page_id": ctx["page"].id,
            "storyboard_version": 1,
            "client_request_id": _uid(),
        },
    )
    assert cross.status_code == 422
