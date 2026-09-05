"""ISSUE #154 input-validation hardening regressions.

Explicit nulls, oversized version tokens, lone surrogates, oversized/deeply
nested JSON bodies and declared-but-unimplemented provider test types must
fail with a 4xx — never a 500, a silent wipe, or a silent downgrade to a
cheaper operation. Sub-items 2/3/5/6/7/8/9/10 of the issue are pinned here.
"""

import json

import pytest
from pydantic import ValidationError

from app.api.helpers import reject_required_nulls, sanitize_surrogates
from app.api.routes.providers import _connection_test_operation
from app.config import get_settings
from app.models import AppSetting, Dialogue, Project, SceneAsset
from app.provider_schemas import ConnectionTestRequest, ProviderUpdate
from app.request_limits import JsonDepthExceeded, _JsonDepthTracker
from app.request_limits import max_json_body_bytes as json_limit_for_path
from app.schemas import (
    CharacterModelPackageUpdate,
    DialogueUpdate,
    PanelUpdate,
    ProjectUpdate,
    StylePaletteApproval,
)
from app.settings_schemas import RuntimeSettingsUpdate
from app.workflow_schemas import WorkflowRestoreRequest, WorkflowUpdate

INT32_MAX = 2_147_483_647


def _project(client, name="输入校验加固") -> dict:
    response = client.post("/api/v1/projects", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------- sub-item 2


def test_runtime_settings_patch_explicit_null_is_422(client):
    response = client.patch(
        "/api/v1/settings/runtime",
        json={"version": 1, "job_timeout_seconds": None},
    )
    assert response.status_code == 422
    assert "null" in response.json()["detail"][0]["msg"]
    # The endpoint stays readable and a well-formed PATCH still applies.
    assert client.get("/api/v1/settings/runtime").status_code == 200
    patched = client.patch(
        "/api/v1/settings/runtime", json={"version": 1, "job_timeout_seconds": 120}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["job_timeout_seconds"] == 120


@pytest.mark.parametrize(
    "field", ["queue_mode", "max_auto_repairs", "default_concurrency", "workflow_autosave_ms"]
)
def test_runtime_settings_update_rejects_every_explicit_null(field):
    payload = {field: None, "version": 1}
    with pytest.raises(ValidationError):
        RuntimeSettingsUpdate.model_validate(payload)
    # Omission (the legal "no change") keeps validating.
    assert RuntimeSettingsUpdate(version=1).model_fields_set == {"version"}


def test_poisoned_runtime_row_no_longer_breaks_reads(client, db_session):
    # Simulate a row persisted before the schema rejected explicit nulls.
    db_session.add(
        AppSetting(
            key="runtime",
            value={"job_timeout_seconds": None, "queue_mode": None},
            version=3,
        )
    )
    db_session.commit()
    response = client.get("/api/v1/settings/runtime")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["queue_mode"] == "AUTO"  # None dropped, default applies
    assert isinstance(body["job_timeout_seconds"], int)


# ---------------------------------------------------------------- sub-item 3


def test_scene_asset_patch_null_name_is_422(client, db_session):
    project = _project(client, "场景资产null")
    created = client.post(
        f"/api/v1/projects/{project['id']}/scene-assets",
        json={"name": "仓库", "description": "存放旧物"},
    ).json()
    response = client.patch(
        f"/api/v1/projects/{project['id']}/scene-assets/{created['id']}",
        json={"version": created["version"], "name": None},
    )
    assert response.status_code == 422
    assert "name" in response.json()["detail"]
    # The optimistic-lock claim made before the guard rolls back with the
    # aborted transaction: name and version survive unchanged.
    db_session.rollback()
    row = db_session.get(SceneAsset, created["id"])
    assert row.name == "仓库"
    assert row.version == created["version"]


def test_scene_asset_variant_patch_null_name_is_422(client):
    project = _project(client, "场景变体null")
    asset = client.post(
        f"/api/v1/projects/{project['id']}/scene-assets", json={"name": "仓库"}
    ).json()
    variant = client.post(
        f"/api/v1/projects/{project['id']}/scene-assets/{asset['id']}/variants",
        json={"name": "夜晚"},
    ).json()
    response = client.patch(
        f"/api/v1/projects/{project['id']}/scene-assets/{asset['id']}"
        f"/variants/{variant['id']}",
        json={"version": variant["version"], "name": None},
    )
    assert response.status_code == 422
    assert "name" in response.json()["detail"]


# ---------------------------------------------------------------- sub-item 5


def test_workflow_patch_and_create_null_draft_graph_is_422(client):
    project = _project(client, "工作流null")
    workflow = client.post(
        f"/api/v1/projects/{project['id']}/workflows", json={"name": "主线"}
    ).json()

    response = client.patch(
        f"/api/v1/workflows/{workflow['id']}",
        json={"version": workflow["version"], "draft_graph": None},
    )
    assert response.status_code == 422
    assert "null" in response.text

    # A stray null draft_graph on create is rejected too (extra="forbid").
    created = client.post(
        f"/api/v1/projects/{project['id']}/workflows",
        json={"name": "坏请求", "draft_graph": None},
    )
    assert created.status_code == 422

    # The import endpoint already required a real graph; keep it pinned.
    imported = client.post(
        f"/api/v1/projects/{project['id']}/workflows/import",
        json={"name": "导入", "graph": None},
    )
    assert imported.status_code == 422

    # Omission keeps the stored graph (省略 = 不修改).
    renamed = client.patch(
        f"/api/v1/workflows/{workflow['id']}",
        json={"version": workflow["version"], "name": "改名"},
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["draft_graph"] == workflow["draft_graph"]


@pytest.mark.parametrize("field", ["name", "description", "is_active"])
def test_workflow_update_rejects_explicit_null_fields(field):
    with pytest.raises(ValidationError):
        WorkflowUpdate.model_validate({field: None, "version": 1})


# ---------------------------------------------------------------- sub-item 6


def test_project_patch_oversized_version_is_422(client, db_session):
    project = Project(name="版本上界")
    db_session.add(project)
    db_session.commit()
    response = client.patch(
        f"/api/v1/projects/{project.id}",
        json={"version": 10**30, "name": "改名"},
    )
    assert response.status_code == 422
    db_session.expire_all()
    assert db_session.get(Project, project.id).name == "版本上界"


@pytest.mark.parametrize(
    "model,payload",
    [
        (ProjectUpdate, {"version": 10**30}),
        (PanelUpdate, {"version": 10**30}),
        (DialogueUpdate, {"panel_version": 10**30}),
        (StylePaletteApproval, {"version": 10**30, "palette": {}}),
        (CharacterModelPackageUpdate, {"version": 10**30}),
        (RuntimeSettingsUpdate, {"version": 10**30}),
        (WorkflowUpdate, {"version": 10**30}),
        (WorkflowRestoreRequest, {"version": 10**30}),
        (ProviderUpdate, {"version": 10**30, "name": "x"}),
    ],
)
def test_version_tokens_are_bounded_at_int32(model, payload):
    # 32-bit Integer columns: anything larger overflowed SQLite bindings
    # (OverflowError) / raised DataError on PostgreSQL before this bound.
    with pytest.raises(ValidationError):
        model.model_validate(payload)
    payload = dict(payload, version=INT32_MAX) if "version" in payload else payload
    if "panel_version" in payload:
        payload["panel_version"] = INT32_MAX
    model.model_validate(payload)  # boundary value itself is accepted


# ---------------------------------------------------------------- sub-item 7


def test_sanitize_surrogates_replaces_lone_surrogates_recursively():
    value = {
        "text": "a\ud800b",
        "nested": ["x\udfffy", {"deep": "尾\udcff注"}],
        "count": 5,
    }
    cleaned = sanitize_surrogates(value)
    assert cleaned["text"] == "a\ufffdb"
    assert cleaned["nested"][0] == "x\ufffdy"
    assert cleaned["nested"][1]["deep"] == "尾\ufffd注"
    assert cleaned["count"] == 5


def test_sanitize_surrogates_copies_nested_models():
    from app.schemas import SceneAssetStructured

    model = SceneAssetStructured(palette={"mood": "a\ud800b"}, weather="clear")
    cleaned = sanitize_surrogates(model)
    assert isinstance(cleaned, SceneAssetStructured)
    assert cleaned.palette["mood"] == "a\ufffdb"
    assert cleaned.weather == "clear"
    # The original instance is never mutated in place.
    assert model.palette["mood"] == "a\ud800b"


def test_sanitize_surrogates_keeps_legal_text_untouched():
    text = "中文 émoji 🎨 tab\t quote\" backslash\\"
    assert sanitize_surrogates(text) == text


def test_reject_required_nulls_sanitizes_strings_in_place():
    changes = {"text_model_alias": "a\ud800b", "description": None}
    reject_required_nulls(Project, changes)  # description is nullable: kept
    assert changes["text_model_alias"] == "a\ufffdb"
    assert changes["description"] is None


def _json_with_surrogate(payload: dict) -> tuple[bytes, dict[str, str]]:
    """Wire-encode a body carrying a lone surrogate escape.

    httpx refuses to UTF-8-encode lone surrogates via ``json=``, so tests send
    the raw ASCII JSON — exactly what a hostile client posts (``\\ud800``).
    """

    return (
        json.dumps(payload).encode("ascii"),
        {"content-type": "application/json"},
    )


def test_scene_asset_patch_surrogate_in_str_field_is_sanitized(client, db_session):
    # The middleware scrubs lone surrogate escapes at the wire level, so even
    # the Pydantic error path never sees them (its 422 used to echo the bad
    # input and crash response encoding with a 500).
    project = _project(client, "孤立代理")
    created = client.post(
        f"/api/v1/projects/{project['id']}/scene-assets",
        json={"name": "仓库", "description": "旧描述"},
    ).json()
    content, headers = _json_with_surrogate(
        {"version": created["version"], "description": "a\ud800b"}
    )
    response = client.patch(
        f"/api/v1/projects/{project['id']}/scene-assets/{created['id']}",
        content=content,
        headers=headers,
    )
    assert response.status_code == 200, response.text
    db_session.expire_all()
    assert db_session.get(SceneAsset, created["id"]).description == "a\ufffdb"


def test_scene_asset_patch_surrogate_in_dict_field_is_sanitized(client, db_session):
    # Untyped dict payloads (structured.palette) pass Pydantic validation;
    # the shared PATCH guard must scrub them before they poison the row.
    project = _project(client, "孤立代理字典")
    created = client.post(
        f"/api/v1/projects/{project['id']}/scene-assets",
        json={"name": "仓库", "description": "旧描述"},
    ).json()
    content, headers = _json_with_surrogate(
        {
            "version": created["version"],
            "structured": {"palette": {"mood": "a\ud800b"}},
        }
    )
    response = client.patch(
        f"/api/v1/projects/{project['id']}/scene-assets/{created['id']}",
        content=content,
        headers=headers,
    )
    assert response.status_code == 200, response.text
    db_session.expire_all()
    row = db_session.get(SceneAsset, created["id"])
    assert row.structured["palette"]["mood"] == "a\ufffdb"


def test_dialogue_patch_surrogate_is_stored_sanitized(client, db_session):
    from sqlalchemy import func, select

    from app.models import Beat, Chapter, MangaPage, Scene, ScriptRevision, SourceSegment

    project = _project(client, "对白代理")
    imported = client.post(
        f"/api/v1/projects/{project['id']}/sources/import",
        json={"title": "第一章", "text": "她推开门。\n\n轻声问道。"},
    )
    assert imported.status_code == 201
    chapter = imported.json()["chapters"][0]
    segments = list(
        db_session.scalars(
            select(SourceSegment).where(
                SourceSegment.source_revision_id
                == chapter["current_source_revision_id"]
            )
        )
    )
    segment_ids = [item.id for item in segments]
    scene = Scene(
        chapter_id=chapter["id"],
        ordinal=1,
        location="门口",
        source_range={"segment_ids": segment_ids},
    )
    db_session.add(scene)
    db_session.flush()
    db_session.add(
        Beat(
            scene_id=scene.id,
            ordinal=1,
            action="问话",
            source_range={"segment_ids": segment_ids[:1]},
        )
    )
    db_session.add(
        ScriptRevision(
            chapter_id=chapter["id"],
            source_revision_id=chapter["current_source_revision_id"],
            revision_no=1,
            status="READY",
            coverage={"expected": 1, "covered": 1, "ratio": 1, "missing_segment_ids": []},
        )
    )
    db_session.get(Chapter, chapter["id"]).status = "SCRIPT_READY"
    db_session.commit()
    planned = client.post(
        f"/api/v1/chapters/{chapter['id']}/plan", json={"replace_existing": True}
    )
    assert planned.status_code == 200, planned.text
    page = db_session.get(MangaPage, planned.json()["pages"][0]["id"])
    storyboard = client.get(f"/api/v1/pages/{page.id}/storyboard").json()
    panel = storyboard["panels"][0]
    next_order = (
        db_session.scalar(
            select(func.max(Dialogue.reading_order)).where(Dialogue.panel_id == panel["id"])
        )
        or 0
    ) + 1
    dialogue = Dialogue(
        panel_id=panel["id"],
        target_text="台词",
        reading_order=next_order,
        region={},
    )
    db_session.add(dialogue)
    db_session.commit()

    content, headers = _json_with_surrogate(
        {"panel_version": panel["version"], "region": {"note": "a\ud800b"}}
    )
    response = client.patch(f"/api/v1/dialogues/{dialogue.id}", content=content, headers=headers)
    assert response.status_code == 200, response.text
    db_session.expire_all()
    assert db_session.get(Dialogue, dialogue.id).region["note"] == "a\ufffdb"


# ---------------------------------------------------------------- sub-item 8


def test_json_depth_tracker_bounds_nesting():
    tracker = _JsonDepthTracker(max_depth=100)
    tracker.feed(b'{"a": ' * 99 + b"1" + b"}" * 99)
    assert tracker.depth == 0  # balanced 99-deep object passes
    deep = _JsonDepthTracker(max_depth=100)
    with pytest.raises(JsonDepthExceeded):
        deep.feed(b"[" * 101)


def test_json_depth_tracker_ignores_brackets_inside_strings():
    tracker = _JsonDepthTracker(max_depth=4)
    tracker.feed(b'{"text": "a[b]{c}\\"quote\\[" , "n": [1,2]}')
    assert tracker.depth == 0


def test_deeply_nested_json_body_is_rejected_not_500(client):
    body = b"[" * 10_000 + b"]" * 10_000
    response = client.post(
        "/api/v1/projects",
        content=body,
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 422, response.text
    assert "深度" in response.json()["detail"]


def test_oversized_json_body_is_413(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_json_body_bytes", 256)
    # Declared content-length path: rejected before the body is read.
    response = client.post(
        "/api/v1/projects",
        content=b'{"name": "' + b"x" * 4096 + b'"}',
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413
    # Streaming (chunked, no content-length) path: counted while receiving.
    def chunks():
        yield b'{"name": "'
        yield b"x" * 4096
        yield b'"}'

    streamed = client.post(
        "/api/v1/projects",
        content=chunks(),
        headers={"content-type": "application/json"},
    )
    assert streamed.status_code == 413


def test_large_text_endpoints_are_exempt_from_generic_json_budget(
    client, monkeypatch
):
    monkeypatch.setattr(get_settings(), "max_json_body_bytes", 256)
    project = _project(client, "大文本豁免")
    text = "雨停了，她推开门。" * 80  # ~2KB of JSON, above the tiny budget
    response = client.post(
        f"/api/v1/projects/{project['id']}/sources/import",
        json={"title": "第一章", "text": text},
    )
    assert response.status_code == 201, response.text


def test_json_body_limit_path_classification(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "max_json_body_bytes", 256)
    assert json_limit_for_path("/api/v1/projects") == 256
    assert json_limit_for_path("/api/v1/workflows/wf") == 256
    # Exempt large-text endpoints fall back to the upload budget.
    assert json_limit_for_path("/api/v1/projects/p/sources/import") == (
        settings.max_upload_bytes + settings.upload_form_overhead_bytes
    )
    assert json_limit_for_path("/api/v1/chapters/c/revisions") == (
        settings.max_upload_bytes + settings.upload_form_overhead_bytes
    )


def test_normal_json_requests_are_unaffected(client):
    project = _project(client, "正常请求")
    assert project["name"] == "正常请求"
    assert client.get("/api/v1/projects").status_code == 200


# ---------------------------------------------------------------- sub-item 9


def test_package_patch_null_spec_blocks_are_422_and_omission_keeps(client):
    project = _project(client, "角色包null")
    character = client.post(
        f"/api/v1/projects/{project['id']}/characters",
        json={"primary_name": "林澈", "aliases": []},
    ).json()
    package = client.post(
        f"/api/v1/projects/{project['id']}/characters/{character['id']}/package",
        json={"identity_spec": {"gender": "女"}, "negative_constraints": ["眼镜"]},
    ).json()

    for field in ("identity_spec", "visual_spec", "negative_constraints"):
        response = client.patch(
            f"/api/v1/projects/{project['id']}/characters/{character['id']}/package",
            json={"version": package["version"], field: None},
        )
        assert response.status_code == 422, response.text

    # Omission keeps the stored blocks (no wipe, no version bump).
    untouched = client.patch(
        f"/api/v1/projects/{project['id']}/characters/{character['id']}/package",
        json={"version": package["version"]},
    )
    assert untouched.status_code == 200, untouched.text
    body = untouched.json()
    assert body["identity_spec"] == {"gender": "女"}
    assert body["negative_constraints"] == ["眼镜"]

    # An explicit empty object remains the legal "clear" operation.
    cleared = client.patch(
        f"/api/v1/projects/{project['id']}/characters/{character['id']}/package",
        json={"version": body["version"], "negative_constraints": []},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["negative_constraints"] == []


# --------------------------------------------------------------- sub-item 10


def test_connection_test_type_benchmark_is_422_not_silent_downgrade(client):
    response = client.post(
        "/api/v1/providers/connections/nonexistent/test",
        json={
            "test_type": "BENCHMARK",
            "model_id": "m",
            "acknowledge_cost": True,
        },
    )
    assert response.status_code == 422
    assert "BENCHMARK" in response.json()["detail"]


def test_connection_test_type_rejects_undeclared_values(client):
    response = client.post(
        "/api/v1/providers/connections/nonexistent/test",
        json={"test_type": "image"},
    )
    assert response.status_code == 422


def test_connection_test_type_operation_mapping():
    # IMAGE keeps its paid image-generation semantics instead of falling
    # through dict.get() to the model's default (free TEXT) probe.
    assert _connection_test_operation("TEXT") == "structured_text"
    assert _connection_test_operation("VISION") == "multimodal_analysis"
    assert _connection_test_operation("IMAGE") == "image_generate"
    assert _connection_test_operation("CREDENTIALS") is None
    with pytest.raises(Exception) as exc_info:
        _connection_test_operation("BENCHMARK")
    assert getattr(exc_info.value, "status_code", None) == 422


def test_connection_test_request_literal_pins_declared_types():
    with pytest.raises(ValidationError):
        ConnectionTestRequest(test_type="SOMETHING_ELSE")
    # IMAGE/BENCHMARK stay declared shapes: the cost acknowledgement and
    # model-id requirements still apply to them.
    request = ConnectionTestRequest(
        test_type="IMAGE", model_id="m", acknowledge_cost=True
    )
    assert request.test_type == "IMAGE"
