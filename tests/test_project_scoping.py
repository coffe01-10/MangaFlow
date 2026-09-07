"""Project-scoping regressions for issue #143.

Two projects each own a full object set (chapter/page/batch/candidates, jobs,
outfit, style, asset, export bundle, workflow + version + run, character
reference, model-call attempts). Every object-id endpoint wired to the
``ensure_project_scope`` helper must:

- 404 with the shared 「不属于当前项目」 message when the caller names a
  foreign project (query parameter — none of these routes carry a project
  path segment and the web client never sends one);
- keep working for the owning project;
- keep the historical unscoped behavior when the parameter is omitted, so
  the existing frontend and scripts are unaffected.

Destructive cross-project calls must leave the target rows intact.
"""

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.domain.states import JobStatus, PageStatus, Resolution
from app.models import (
    Asset,
    AssetCandidate,
    Beat,
    Chapter,
    Character,
    CharacterReference,
    Dialogue,
    ExportBundle,
    GenerationBatch,
    GenerationJob,
    InspectionResult,
    MangaPage,
    ModelCallAttempt,
    Outfit,
    PageCandidate,
    Panel,
    RepairPlan,
    Scene,
    ScriptRevision,
    SourceRevision,
    SourceSegment,
    StyleProfile,
    WorkflowDefinition,
    WorkflowRun,
)


def _orm(db, obj):
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


@pytest.fixture
def scoped_world(client, db_session, tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "storage_root", tmp_path / "storage")
    monkeypatch.setattr(settings, "upload_root", tmp_path / "uploads")

    def build(suffix: str, digest: str, generated_digest: str) -> dict:
        project = client.post("/api/v1/projects", json={"name": f"隔离项目{suffix}"}).json()
        project_id = project["id"]
        character = client.post(
            f"/api/v1/projects/{project_id}/characters",
            json={"primary_name": f"角色{suffix}", "aliases": []},
        ).json()
        chapter = _orm(db_session, Chapter(project_id=project_id, title=f"章节{suffix}", ordinal=1))
        # A chapter with no pages so the paid SOURCE_PARSE route reaches its
        # job-creation path instead of the 已有分页 guard.
        bare_chapter = _orm(
            db_session, Chapter(project_id=project_id, title=f"空章节{suffix}", ordinal=2)
        )
        page = _orm(db_session, MangaPage(chapter_id=chapter.id, page_number=1))
        panel = _orm(db_session, Panel(page_id=page.id, reading_order=1))
        dialogue = _orm(
            db_session, Dialogue(panel_id=panel.id, target_text=f"隔离对白{suffix}", reading_order=1)
        )
        scene = _orm(db_session, Scene(chapter_id=chapter.id, ordinal=1, location=f"场地{suffix}"))
        beat = _orm(db_session, Beat(scene_id=scene.id, ordinal=1, action=f"情节拍{suffix}"))
        # A committed source revision (with segment + script rows) so chapter
        # segment/script reads reach their data paths and the destructive
        # guards have a script tree whose survival can be asserted.
        source_revision = _orm(
            db_session,
            SourceRevision(
                chapter_id=chapter.id,
                revision=1,
                source_type="TXT",
                original_text=f"正文{suffix}第一段。",
                sha256=suffix.lower() * 64,
                character_count=8,
            ),
        )
        segment = _orm(
            db_session,
            SourceSegment(
                source_revision_id=source_revision.id,
                ordinal=1,
                text=f"正文{suffix}第一段。",
                start_offset=0,
                end_offset=9,
                sha256=suffix.lower() * 64,
            ),
        )
        chapter.current_source_revision_id = source_revision.id
        script_revision = _orm(
            db_session,
            ScriptRevision(
                chapter_id=chapter.id,
                source_revision_id=source_revision.id,
                revision_no=1,
                status="DRAFT",
                coverage={},
            ),
        )
        batch = _orm(
            db_session,
            GenerationBatch(
                project_id=project_id,
                ordinal=1,
                generation_kind="PAGE",
                page_id=page.id,
                status="OPEN",
            ),
        )
        page_candidate = _orm(
            db_session,
            PageCandidate(
                batch_id=batch.id,
                page_id=page.id,
                ordinal=1,
                model_alias="test-model",
                resolution=Resolution.DRAFT_1K,
                status="READY",
                based_on_storyboard_version=1,
            ),
        )
        asset_candidate = _orm(
            db_session,
            AssetCandidate(
                batch_id=batch.id,
                ordinal=1,
                model_alias="test-model",
                resolution=Resolution.DRAFT_1K,
                variant="STYLE_TEST",
                status="READY",
            ),
        )
        completed_job = _orm(
            db_session,
            GenerationJob(
                project_id=project_id,
                target_type="PAGE_CANDIDATE",
                target_id=page_candidate.id,
                job_type="PAGE_GENERATE",
                status=JobStatus.COMPLETED,
            ),
        )
        failed_job = _orm(
            db_session,
            GenerationJob(
                project_id=project_id,
                target_type="PAGE_CANDIDATE",
                target_id=page_candidate.id,
                job_type="PAGE_GENERATE",
                status=JobStatus.FAILED,
            ),
        )
        outfit = _orm(
            db_session,
            Outfit(project_id=project_id, character_id=character["id"], name=f"服装{suffix}"),
        )
        style = _orm(
            db_session,
            StyleProfile(
                project_id=project_id,
                name=f"风格{suffix}",
                color_mode="monochrome",
                profile={},
                status="DRAFT",
            ),
        )
        asset = _orm(
            db_session,
            Asset(
                project_id=project_id,
                kind="CHARACTER_REFERENCE",
                original_name=f"{suffix}.png",
                storage_key=f"uploads/{project_id}/{suffix}.png",
                mime_type="image/png",
                byte_size=8,
                sha256=digest,
                source="USER_UPLOAD",
            ),
        )
        # adopt-reference only accepts generated assets, so the world needs one.
        generated_asset = _orm(
            db_session,
            Asset(
                project_id=project_id,
                kind="CHARACTER_REFERENCE",
                original_name=f"generated-{suffix}.png",
                storage_key=f"generated/{project_id}/{suffix}.png",
                mime_type="image/png",
                byte_size=8,
                sha256=generated_digest,
                source="VERTEX_GENERATED",
            ),
        )
        export_key = f"exports/{project_id}/{chapter.id}/bundle.zip"
        export_bundle = _orm(
            db_session,
            ExportBundle(
                project_id=project_id,
                chapter_id=chapter.id,
                export_type="PNG",
                storage_key=export_key,
                byte_size=8,
                # String(64): keep exactly 64 chars while staying unique per
                # project (the raw ``digest + "export"`` was 70 chars and
                # would overflow on PostgreSQL).
                sha256=digest[:58] + "export",
            ),
        )
        export_file = tmp_path / "storage" / export_key
        export_file.parent.mkdir(parents=True, exist_ok=True)
        export_file.write_bytes(b"PK-scoping")
        workflow = client.post(
            f"/api/v1/projects/{project_id}/workflows",
            json={"name": f"工作流{suffix}", "template": "manga_default"},
        ).json()
        workflow_version = client.post(
            f"/api/v1/workflows/{workflow['id']}/publish"
        ).json()
        run = _orm(
            db_session,
            WorkflowRun(
                workflow_id=workflow["id"],
                workflow_version_id=workflow_version["id"],
                project_id=project_id,
                scope_type="PROJECT",
            ),
        )
        reference = _orm(
            db_session,
            CharacterReference(
                character_id=character["id"],
                asset_id=asset.id,
                angle="front",
                is_canonical=True,
            ),
        )
        attempt_direct = _orm(
            db_session,
            ModelCallAttempt(
                project_id=project_id,
                job_attempt=1,
                dispatch_no=1,
                provider="scoping-provider",
                model_id="scoping-model",
            ),
        )
        attempt_via_job = _orm(
            db_session,
            ModelCallAttempt(
                job_id=completed_job.id,
                job_attempt=2,
                dispatch_no=1,
                provider="scoping-provider",
                model_id="scoping-model",
            ),
        )
        return {
            "project_id": project_id,
            "character_id": character["id"],
            "chapter_id": chapter.id,
            "bare_chapter_id": bare_chapter.id,
            "page_id": page.id,
            "panel_id": panel.id,
            "dialogue_id": dialogue.id,
            "scene_id": scene.id,
            "beat_id": beat.id,
            "source_revision_id": source_revision.id,
            "segment_id": segment.id,
            "script_revision_id": script_revision.id,
            "batch_id": batch.id,
            "page_candidate_id": page_candidate.id,
            "asset_candidate_id": asset_candidate.id,
            "completed_job_id": completed_job.id,
            "failed_job_id": failed_job.id,
            "outfit_id": outfit.id,
            "style_id": style.id,
            "asset_id": asset.id,
            "generated_asset_id": generated_asset.id,
            "export_id": export_bundle.id,
            "workflow_id": workflow["id"],
            "workflow_version_id": workflow_version["id"],
            "run_id": run.id,
            "reference_id": reference.id,
            "attempt_direct_id": attempt_direct.id,
            "attempt_via_job_id": attempt_via_job.id,
        }

    world = {"a": build("A", "a" * 64, "c" * 64), "b": build("B", "b" * 64, "d" * 64)}
    context = {}
    for suffix, objects in world.items():
        for key, value in objects.items():
            context[f"{key}_{suffix}"] = value
    world["context"] = context
    return world


# (method, url template over project B objects, optional JSON body). Every case
# is called with project A's id in the project_id query parameter and must 404.
CROSS_PROJECT_CASES = [
    pytest.param("GET", "/api/v1/jobs/{completed_job_id_b}", None, id="job-get"),
    pytest.param("POST", "/api/v1/jobs/{completed_job_id_b}/cancel", None, id="job-cancel"),
    pytest.param("POST", "/api/v1/jobs/{failed_job_id_b}/retry", None, id="job-retry"),
    pytest.param("POST", "/api/v1/jobs/{completed_job_id_b}/archive", None, id="job-archive"),
    pytest.param("POST", "/api/v1/jobs/{completed_job_id_b}/restore", None, id="job-restore"),
    pytest.param("DELETE", "/api/v1/jobs/{failed_job_id_b}", None, id="job-delete"),
    pytest.param(
        "GET",
        "/api/v1/jobs/{completed_job_id_b}/model-call-attempts",
        None,
        id="job-model-call-attempts",
    ),
    pytest.param(
        "POST",
        "/api/v1/batches/{batch_id_b}/candidates",
        {"model_alias": "test-model", "resolution": "1K", "storyboard_version": 1},
        id="batch-create-candidate",
    ),
    pytest.param(
        "GET", "/api/v1/batches/{batch_id_b}/candidates", None, id="batch-list-candidates"
    ),
    pytest.param(
        "PATCH",
        "/api/v1/candidates/{page_candidate_id_b}/favorite",
        {"is_favorite": True},
        id="page-candidate-favorite",
    ),
    pytest.param(
        "DELETE", "/api/v1/candidates/{page_candidate_id_b}", None, id="page-candidate-delete"
    ),
    pytest.param(
        "DELETE", "/api/v1/candidates/{asset_candidate_id_b}", None, id="asset-candidate-delete"
    ),
    pytest.param(
        "PATCH",
        "/api/v1/outfits/{outfit_id_b}",
        {"name": "越权修改", "version": 1},
        id="outfit-patch",
    ),
    pytest.param("DELETE", "/api/v1/outfits/{outfit_id_b}", None, id="outfit-delete"),
    pytest.param(
        "PATCH",
        "/api/v1/styles/{style_id_b}",
        {"name": "越权修改", "version": 1},
        id="style-patch",
    ),
    pytest.param("POST", "/api/v1/styles/{style_id_b}/analyze", None, id="style-analyze"),
    pytest.param(
        "POST",
        "/api/v1/styles/{style_id_b}/palette-draft",
        {"atmosphere": "冷色调"},
        id="style-palette-draft",
    ),
    pytest.param(
        "POST",
        "/api/v1/styles/{style_id_b}/palette-approve",
        {"palette": {"ink": "#000000"}, "version": 1},
        id="style-palette-approve",
    ),
    pytest.param(
        "POST",
        "/api/v1/styles/{style_id_b}/style-test-approve",
        {"candidate_id": "00000000-0000-0000-0000-000000000000", "version": 1},
        id="style-test-approve",
    ),
    pytest.param(
        "PATCH",
        "/api/v1/assets/{asset_id_b}",
        {"display_name": "越权修改"},
        id="asset-patch",
    ),
    pytest.param("DELETE", "/api/v1/assets/{asset_id_b}", None, id="asset-delete"),
    pytest.param("GET", "/api/v1/assets/{asset_id_b}/content", None, id="asset-content"),
    pytest.param(
        "GET", "/api/v1/assets/{asset_id_b}/thumbnail/320", None, id="asset-thumbnail"
    ),
    pytest.param(
        "GET",
        "/api/v1/usage/attempts/{attempt_direct_id_b}",
        None,
        id="usage-attempt-detail-direct",
    ),
    pytest.param(
        "GET",
        "/api/v1/usage/attempts/{attempt_via_job_id_b}",
        None,
        id="usage-attempt-detail-via-job",
    ),
    pytest.param(
        "GET", "/api/v1/exports/{export_id_b}/download", None, id="export-download"
    ),
    pytest.param("GET", "/api/v1/workflows/{workflow_id_b}", None, id="workflow-get"),
    pytest.param(
        "PATCH",
        "/api/v1/workflows/{workflow_id_b}",
        {"name": "越权修改", "version": 1},
        id="workflow-patch",
    ),
    pytest.param("DELETE", "/api/v1/workflows/{workflow_id_b}", None, id="workflow-delete"),
    pytest.param(
        "GET", "/api/v1/workflows/{workflow_id_b}/export", None, id="workflow-export"
    ),
    pytest.param(
        "POST", "/api/v1/workflows/{workflow_id_b}/validate", None, id="workflow-validate"
    ),
    pytest.param(
        "POST", "/api/v1/workflows/{workflow_id_b}/publish", None, id="workflow-publish"
    ),
    pytest.param(
        "GET", "/api/v1/workflows/{workflow_id_b}/versions", None, id="workflow-versions"
    ),
    pytest.param(
        "GET", "/api/v1/workflows/{workflow_id_b}/runs", None, id="workflow-runs-list"
    ),
    pytest.param(
        "POST", "/api/v1/workflows/{workflow_id_b}/runs", {}, id="workflow-run-start"
    ),
    pytest.param(
        "POST",
        "/api/v1/workflow-versions/{workflow_version_id_b}/restore",
        {"version": 1},
        id="workflow-version-restore",
    ),
    pytest.param("GET", "/api/v1/workflow-runs/{run_id_b}", None, id="workflow-run-read"),
    pytest.param(
        "POST", "/api/v1/workflow-runs/{run_id_b}/cancel", None, id="workflow-run-cancel"
    ),
    pytest.param(
        "POST", "/api/v1/workflow-runs/{run_id_b}/retry", None, id="workflow-run-retry"
    ),
    pytest.param(
        "POST",
        "/api/v1/workflow-runs/{run_id_b}/nodes/node-1/approve",
        {},
        id="workflow-run-node-approve",
    ),
    pytest.param(
        "DELETE",
        "/api/v1/character-references/{reference_id_b}",
        None,
        id="character-reference-unbind",
    ),
    pytest.param(
        "PATCH",
        "/api/v1/characters/{character_id_b}",
        {"primary_name": "越权修改", "version": 1},
        id="character-patch",
    ),
    pytest.param(
        "POST",
        "/api/v1/characters/{character_id_b}/references",
        {"asset_id": "00000000-0000-0000-0000-000000000000", "is_canonical": False},
        id="character-bind-reference",
    ),
    pytest.param("GET", "/api/v1/pages/{page_id_b}/export.png", None, id="page-export-png"),
    pytest.param(
        "POST",
        "/api/v1/assets/{generated_asset_id_b}/adopt-reference",
        None,
        id="asset-adopt-reference",
    ),
    pytest.param("POST", "/api/v1/chapters/{chapter_id_b}/parse", None, id="chapter-parse"),
    pytest.param("GET", "/api/v1/chapters/{chapter_id_b}", None, id="chapter-get"),
    pytest.param("GET", "/api/v1/chapters/{chapter_id_b}/segments", None, id="chapter-segments"),
    pytest.param("GET", "/api/v1/chapters/{chapter_id_b}/revisions", None, id="chapter-revisions"),
    pytest.param("GET", "/api/v1/chapters/{chapter_id_b}/script", None, id="chapter-script-get"),
    pytest.param("POST", "/api/v1/chapters/{chapter_id_b}/plan", {}, id="chapter-plan"),
    pytest.param(
        "POST",
        "/api/v1/chapters/{chapter_id_b}/revisions",
        {"text": "越权修订正文", "source_type": "PASTE"},
        id="chapter-revise-source",
    ),
    pytest.param("DELETE", "/api/v1/chapters/{chapter_id_b}", None, id="chapter-delete"),
    pytest.param("POST", "/api/v1/chapters/{chapter_id_b}/restore", None, id="chapter-restore"),
    pytest.param(
        "DELETE", "/api/v1/chapters/{chapter_id_b}/script", None, id="chapter-script-delete"
    ),
    pytest.param(
        "POST",
        "/api/v1/chapters/{chapter_id_b}/exports",
        {"export_type": "PNG"},
        id="chapter-export-create",
    ),
    pytest.param(
        "PATCH",
        "/api/v1/scenes/{scene_id_b}",
        {"location": "越权场地", "version": 1},
        id="scene-patch",
    ),
    pytest.param(
        "PATCH",
        "/api/v1/beats/{beat_id_b}",
        {"action": "越权节拍", "version": 1},
        id="beat-patch",
    ),
    pytest.param(
        "PATCH",
        "/api/v1/scenes/{scene_id_b}/outfits",
        {"assignments": {}},
        id="scene-outfits-assign",
    ),
    pytest.param(
        "POST",
        "/api/v1/characters/{character_id_b}/complete-sheet",
        {"model_alias": "test-model", "generation_mode": "CONCEPT"},
        id="character-complete-sheet",
    ),
    pytest.param(
        "GET", "/api/v1/chapters/{chapter_id_b}/pages", None, id="chapter-pages-list"
    ),
    pytest.param("GET", "/api/v1/pages/{page_id_b}", None, id="page-get"),
    pytest.param("GET", "/api/v1/pages/{page_id_b}/readiness", None, id="page-readiness"),
    pytest.param(
        "GET",
        "/api/v1/chapters/{chapter_id_b}/production-readiness",
        None,
        id="chapter-production-readiness",
    ),
    pytest.param(
        "GET",
        "/api/v1/pages/{page_id_b}/production-readiness",
        None,
        id="page-production-readiness",
    ),
    pytest.param(
        "GET", "/api/v1/pages/{page_id_b}/generation-workbench", None, id="page-workbench"
    ),
    pytest.param("GET", "/api/v1/pages/{page_id_b}/storyboard", None, id="page-storyboard-get"),
    pytest.param(
        "PATCH", "/api/v1/pages/{page_id_b}/layout", {"panel_count": 5}, id="page-layout-patch"
    ),
    pytest.param(
        "POST", "/api/v1/pages/{page_id_b}/batches", None, id="page-batches-start"
    ),
    pytest.param(
        "DELETE", "/api/v1/pages/{page_id_b}/selected-candidate", None, id="selected-candidate-retract"
    ),
    pytest.param("POST", "/api/v1/pages/{page_id_b}/next", None, id="page-next"),
    pytest.param(
        "GET",
        "/api/v1/candidates/{page_candidate_id_b}/inspections",
        None,
        id="candidate-inspections-list",
    ),
    pytest.param(
        "PATCH", "/api/v1/panels/{panel_id_b}", {"version": 1}, id="panel-patch"
    ),
    pytest.param(
        "POST",
        "/api/v1/panels/{panel_id_b}/dialogues",
        {"panel_version": 1, "target_text": "越权对白"},
        id="panel-dialogue-create",
    ),
    pytest.param(
        "PATCH", "/api/v1/dialogues/{dialogue_id_b}", {"panel_version": 1}, id="dialogue-patch"
    ),
    pytest.param(
        "DELETE", "/api/v1/dialogues/{dialogue_id_b}", {"panel_version": 1}, id="dialogue-delete"
    ),
    pytest.param(
        "PATCH", "/api/v1/scenes/{scene_id_b}/bind-asset", {}, id="scene-bind-asset"
    ),
]


@pytest.mark.parametrize(("method", "url_template", "body"), CROSS_PROJECT_CASES)
def test_object_endpoint_rejects_foreign_project(
    client, scoped_world, method, url_template, body
):
    context = scoped_world["context"]
    url = f"{url_template.format(**context)}?project_id={context['project_id_a']}"
    response = client.request(method, url, json=body)
    assert response.status_code == 404, response.text
    assert "不属于当前项目" in response.json()["detail"]


# (method, url template over project A objects, JSON body, expected status).
# Each case runs against A's own project_id and must keep succeeding.
SAME_PROJECT_CASES = [
    pytest.param("GET", "/api/v1/jobs/{completed_job_id_a}", None, 200, id="job-get"),
    pytest.param("POST", "/api/v1/jobs/{failed_job_id_a}/retry", None, 200, id="job-retry"),
    pytest.param(
        "GET",
        "/api/v1/jobs/{completed_job_id_a}/model-call-attempts",
        None,
        200,
        id="job-model-call-attempts",
    ),
    pytest.param(
        "GET", "/api/v1/batches/{batch_id_a}/candidates", None, 200, id="batch-list-candidates"
    ),
    pytest.param(
        "PATCH",
        "/api/v1/candidates/{page_candidate_id_a}/favorite",
        {"is_favorite": True},
        200,
        id="page-candidate-favorite",
    ),
    pytest.param(
        "PATCH",
        "/api/v1/outfits/{outfit_id_a}",
        {"name": "本项目管理员修改", "version": 1},
        200,
        id="outfit-patch",
    ),
    pytest.param(
        "PATCH",
        "/api/v1/styles/{style_id_a}",
        {"name": "本项目管理员修改", "version": 1},
        200,
        id="style-patch",
    ),
    pytest.param(
        "PATCH",
        "/api/v1/assets/{asset_id_a}",
        {"display_name": "本项目管理员修改"},
        200,
        id="asset-patch",
    ),
    pytest.param(
        "GET",
        "/api/v1/usage/attempts/{attempt_direct_id_a}",
        None,
        200,
        id="usage-attempt-detail-direct",
    ),
    pytest.param(
        "GET",
        "/api/v1/usage/attempts/{attempt_via_job_id_a}",
        None,
        200,
        id="usage-attempt-detail-via-job",
    ),
    pytest.param(
        "GET", "/api/v1/exports/{export_id_a}/download", None, 200, id="export-download"
    ),
    pytest.param("GET", "/api/v1/workflows/{workflow_id_a}", None, 200, id="workflow-get"),
    pytest.param(
        "GET", "/api/v1/workflows/{workflow_id_a}/versions", None, 200, id="workflow-versions"
    ),
    pytest.param(
        "POST", "/api/v1/workflow-runs/{run_id_a}/cancel", None, 200, id="workflow-run-cancel"
    ),
    pytest.param(
        "DELETE",
        "/api/v1/character-references/{reference_id_a}",
        None,
        204,
        id="character-reference-unbind",
    ),
    pytest.param(
        "PATCH",
        "/api/v1/characters/{character_id_a}",
        {"primary_name": "本项目管理员修改", "version": 1},
        200,
        id="character-patch",
    ),
    pytest.param(
        "POST",
        "/api/v1/assets/{generated_asset_id_a}/adopt-reference",
        None,
        200,
        id="asset-adopt-reference",
    ),
    pytest.param("GET", "/api/v1/chapters/{chapter_id_a}/pages", None, 200, id="chapter-pages-list"),
    pytest.param("GET", "/api/v1/pages/{page_id_a}", None, 200, id="page-get"),
    pytest.param("GET", "/api/v1/pages/{page_id_a}/readiness", None, 200, id="page-readiness"),
    pytest.param(
        "GET", "/api/v1/pages/{page_id_a}/storyboard", None, 200, id="page-storyboard-get"
    ),
    pytest.param(
        "GET",
        "/api/v1/pages/{page_id_a}/generation-workbench",
        None,
        200,
        id="page-workbench",
    ),
    pytest.param(
        "GET",
        "/api/v1/candidates/{page_candidate_id_a}/inspections",
        None,
        200,
        id="candidate-inspections-list",
    ),
    pytest.param(
        "PATCH",
        "/api/v1/panels/{panel_id_a}",
        {"version": 1, "shot_type": "本项目景别"},
        200,
        id="panel-patch",
    ),
    pytest.param(
        "PATCH",
        "/api/v1/dialogues/{dialogue_id_a}",
        {"panel_version": 1, "target_text": "本项目对白"},
        200,
        id="dialogue-patch",
    ),
    pytest.param(
        "PATCH", "/api/v1/scenes/{scene_id_a}/bind-asset", {}, 200, id="scene-bind-asset"
    ),
]


@pytest.mark.parametrize(("method", "url_template", "body", "expected"), SAME_PROJECT_CASES)
def test_object_endpoint_accepts_owning_project(
    client, scoped_world, method, url_template, body, expected
):
    context = scoped_world["context"]
    url = f"{url_template.format(**context)}?project_id={context['project_id_a']}"
    response = client.request(method, url, json=body)
    assert response.status_code == expected, response.text


def test_object_endpoints_without_project_param_keep_legacy_behavior(
    client, scoped_world
):
    """The web client never sends project_id on object routes (#143 sweep)."""

    context = scoped_world["context"]
    assert client.get(f"/api/v1/jobs/{context['completed_job_id_b']}").status_code == 200
    assert (
        client.get(f"/api/v1/batches/{context['batch_id_b']}/candidates").status_code == 200
    )
    assert (
        client.get(f"/api/v1/usage/attempts/{context['attempt_direct_id_b']}").status_code
        == 200
    )
    legacy = client.patch(
        f"/api/v1/candidates/{context['page_candidate_id_b']}/favorite",
        json={"is_favorite": True},
    )
    assert legacy.status_code == 200, legacy.text
    # #143 workflow/storyboard/pages/scene_assets sweep: object routes that
    # carry no project segment keep their unscoped behavior without the param.
    assert client.get(f"/api/v1/pages/{context['page_id_b']}").status_code == 200
    assert client.get(f"/api/v1/pages/{context['page_id_b']}/storyboard").status_code == 200
    assert (
        client.get(
            f"/api/v1/candidates/{context['page_candidate_id_b']}/inspections"
        ).status_code
        == 200
    )
    assert (
        client.get(
            "/api/v1/asset-generation-batches",
            params={"target_type": "CHARACTER", "target_id": context["character_id_b"]},
        ).status_code
        == 200
    )
    # The fixture page carries no script/source traceability, so the layout
    # PATCH keeps its historical 409 guard — what matters is that the route
    # ran past the (omitted) scope gate instead of returning the scope 404.
    legacy_layout = client.patch(
        f"/api/v1/pages/{context['page_id_b']}/layout", json={"panel_count": 5}
    )
    assert legacy_layout.status_code == 409, legacy_layout.text
    assert legacy_layout.json()["detail"] == "当前页缺少剧本或原文追溯，不能调整格数"
    legacy_bind = client.patch(
        f"/api/v1/scenes/{context['scene_id_b']}/bind-asset", json={}
    )
    assert legacy_bind.status_code == 200, legacy_bind.text


def test_cross_project_destructive_calls_leave_target_rows_intact(
    client, scoped_world, db_session
):
    context = scoped_world["context"]
    foreign = {"project_id": context["project_id_a"]}
    calls = [
        ("DELETE", f"/api/v1/candidates/{context['page_candidate_id_b']}"),
        ("DELETE", f"/api/v1/candidates/{context['asset_candidate_id_b']}"),
        ("DELETE", f"/api/v1/outfits/{context['outfit_id_b']}"),
        ("DELETE", f"/api/v1/assets/{context['asset_id_b']}"),
        ("DELETE", f"/api/v1/jobs/{context['failed_job_id_b']}"),
        ("DELETE", f"/api/v1/workflows/{context['workflow_id_b']}"),
        ("DELETE", f"/api/v1/character-references/{context['reference_id_b']}"),
    ]
    for method, url in calls:
        response = client.request(method, url, params=foreign)
        assert response.status_code == 404, f"{method} {url}: {response.text}"
    db_session.expire_all()
    assert db_session.get(PageCandidate, context["page_candidate_id_b"]).deleted_at is None
    assert db_session.get(AssetCandidate, context["asset_candidate_id_b"]).deleted_at is None
    assert db_session.get(Outfit, context["outfit_id_b"]) is not None
    assert db_session.get(Asset, context["asset_id_b"]).deleted_at is None
    assert db_session.get(GenerationJob, context["failed_job_id_b"]) is not None
    assert db_session.get(WorkflowDefinition, context["workflow_id_b"]).deleted_at is None
    assert db_session.get(CharacterReference, context["reference_id_b"]) is not None


def test_usage_attempt_list_filters_by_optional_project(client, scoped_world):
    context = scoped_world["context"]
    response = client.get(
        "/api/v1/usage/attempts", params={"project_id": context["project_id_a"]}
    )
    assert response.status_code == 200, response.text
    returned = {item["id"] for item in response.json()["items"]}
    assert context["attempt_direct_id_a"] in returned
    assert context["attempt_direct_id_b"] not in returned


def test_new_object_endpoints_accept_owning_project_and_legacy_calls(
    client, scoped_world, monkeypatch
):
    """The follow-up sweep (character PATCH/bind, page export.png, generated
    asset adopt, chapter parse) keeps working for the owning project and for
    legacy callers that never send project_id."""

    context = scoped_world["context"]
    owned_patch = client.patch(
        f"/api/v1/characters/{context['character_id_a']}",
        params={"project_id": context["project_id_a"]},
        json={"primary_name": "本项目合法修改", "version": 1},
    )
    assert owned_patch.status_code == 200, owned_patch.text
    owned_bind = client.post(
        f"/api/v1/characters/{context['character_id_a']}/references",
        params={"project_id": context["project_id_a"]},
        json={"asset_id": context["asset_id_a"], "is_canonical": True},
    )
    assert owned_bind.status_code == 201, owned_bind.text
    owned_adopt = client.post(
        f"/api/v1/assets/{context['generated_asset_id_a']}/adopt-reference",
        params={"project_id": context["project_id_a"]},
    )
    assert owned_adopt.status_code == 200, owned_adopt.text
    # The paid SOURCE_PARSE job mints without a live worker (queue off).
    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    owned_parse = client.post(
        f"/api/v1/chapters/{context['bare_chapter_id_a']}/parse",
        params={"project_id": context["project_id_a"]},
    )
    assert owned_parse.status_code == 202, owned_parse.text

    legacy_patch = client.patch(
        f"/api/v1/characters/{context['character_id_b']}",
        json={"primary_name": "旧客户端直连修改", "version": 1},
    )
    assert legacy_patch.status_code == 200, legacy_patch.text
    legacy_bind = client.post(
        f"/api/v1/characters/{context['character_id_b']}/references",
        json={"asset_id": context["asset_id_b"], "is_canonical": False},
    )
    assert legacy_bind.status_code == 201, legacy_bind.text
    legacy_adopt = client.post(
        f"/api/v1/assets/{context['generated_asset_id_b']}/adopt-reference"
    )
    assert legacy_adopt.status_code == 200, legacy_adopt.text
    legacy_parse = client.post(f"/api/v1/chapters/{context['bare_chapter_id_b']}/parse")
    assert legacy_parse.status_code == 202, legacy_parse.text


def test_cross_project_calls_on_new_endpoints_leave_targets_intact(
    client, scoped_world, db_session, monkeypatch
):
    context = scoped_world["context"]
    foreign = {"project_id": context["project_id_a"]}
    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    responses = [
        client.patch(
            f"/api/v1/characters/{context['character_id_b']}",
            params=foreign,
            json={"primary_name": "越权修改", "version": 1},
        ),
        client.post(
            f"/api/v1/characters/{context['character_id_b']}/references",
            params=foreign,
            json={"asset_id": context["asset_id_a"]},
        ),
        # Paid endpoint: a wrong-project parse must never mint a SOURCE_PARSE.
        client.post(
            f"/api/v1/chapters/{context['bare_chapter_id_b']}/parse", params=foreign
        ),
    ]
    assert [response.status_code for response in responses] == [404, 404, 404]
    db_session.expire_all()
    character = db_session.get(Character, context["character_id_b"])
    assert character.primary_name != "越权修改"
    assert character.version == 1
    reference_asset_ids = list(
        db_session.scalars(
            select(CharacterReference.asset_id).where(
                CharacterReference.character_id == context["character_id_b"]
            )
        )
    )
    assert reference_asset_ids == [context["asset_id_b"]]
    parse_jobs = list(
        db_session.scalars(
            select(GenerationJob.id).where(
                GenerationJob.job_type == "SOURCE_PARSE",
                GenerationJob.target_id == context["bare_chapter_id_b"],
            )
        )
    )
    assert parse_jobs == []


def _make_selected_page_production_ready(db_session, context, suffix: str):
    """Drive the fixture page of project ``suffix`` through selection and
    inspection so the export routes reach their minting/serving path."""

    project_id = context[f"project_id_{suffix}"]
    page_asset = _orm(
        db_session,
        Asset(
            project_id=project_id,
            kind="PAGE",
            original_name=f"page-{suffix}.png",
            storage_key=f"generated/{project_id}/page-{suffix}.png",
            mime_type="image/png",
            byte_size=8,
            # Distinct from the fixture's per-project asset digests: assets
            # carry a (project_id, sha256) unique constraint.
            sha256=(suffix.lower() + "e") * 32,
            source="VERTEX_GENERATED",
        ),
    )
    page_file = get_settings().storage_root / page_asset.storage_key
    page_file.parent.mkdir(parents=True, exist_ok=True)
    page_file.write_bytes(f"PNG-SCOPE-{suffix.upper()}".encode())
    candidate = db_session.get(PageCandidate, context[f"page_candidate_id_{suffix}"])
    candidate.asset_id = page_asset.id
    candidate.is_selected = True
    candidate.status = "INSPECTED"
    page = db_session.get(MangaPage, context[f"page_id_{suffix}"])
    page.selected_candidate_id = candidate.id
    page.selected_candidate_ack_version = page.storyboard_version
    page.continuity_status = "PASSED"
    for category in ("SPEAKER", "CHARACTER", "OUTFIT", "PROP", "CONTINUITY"):
        db_session.add(
            InspectionResult(
                candidate_id=candidate.id,
                storyboard_version=page.storyboard_version,
                category=category,
                outcome="PASS",
                score=0.99,
            )
        )
    db_session.commit()
    return page, page_file


def test_page_export_png_scoping_with_production_ready_page(client, scoped_world, db_session):
    """GET /pages/{id}/export.png serves the owning project and legacy callers,
    but hides a foreign project's selected page file behind the shared 404."""

    context = scoped_world["context"]
    page, page_file = _make_selected_page_production_ready(db_session, context, "b")

    foreign = client.get(
        f"/api/v1/pages/{page.id}/export.png",
        params={"project_id": context["project_id_a"]},
    )
    assert foreign.status_code == 404, foreign.text
    assert "不属于当前项目" in foreign.json()["detail"]
    assert page_file.read_bytes() == b"PNG-SCOPE-B"

    owned = client.get(
        f"/api/v1/pages/{page.id}/export.png", params={"project_id": context["project_id_b"]}
    )
    assert owned.status_code == 200, owned.text
    assert owned.content == b"PNG-SCOPE-B"

    legacy = client.get(f"/api/v1/pages/{page.id}/export.png")
    assert legacy.status_code == 200, legacy.text
    assert legacy.content == b"PNG-SCOPE-B"


def test_chapter_export_scoping_with_production_ready_page(client, scoped_world, db_session):
    """POST /chapters/{id}/exports mints bundles for the owning project and
    legacy callers, but a foreign project gets the shared 404 and leaves no
    bundle row or artifact file behind."""

    context = scoped_world["context"]
    _make_selected_page_production_ready(db_session, context, "b")

    foreign = client.post(
        f"/api/v1/chapters/{context['chapter_id_b']}/exports",
        params={"project_id": context["project_id_a"]},
        json={"export_type": "PNG"},
    )
    assert foreign.status_code == 404, foreign.text
    assert "不属于当前项目" in foreign.json()["detail"]
    bundles = list(
        db_session.scalars(
            select(ExportBundle).where(ExportBundle.chapter_id == context["chapter_id_b"])
        )
    )
    assert [bundle.id for bundle in bundles] == [context["export_id_b"]]
    exports_dir = (
        get_settings().storage_root / "exports" / context["project_id_b"] / context["chapter_id_b"]
    )
    assert list(exports_dir.iterdir()) == [exports_dir / "bundle.zip"]

    owned = client.post(
        f"/api/v1/chapters/{context['chapter_id_b']}/exports",
        params={"project_id": context["project_id_b"]},
        json={"export_type": "PNG"},
    )
    assert owned.status_code == 201, owned.text
    legacy = client.post(
        f"/api/v1/chapters/{context['chapter_id_b']}/exports",
        json={"export_type": "PNG"},
    )
    assert legacy.status_code == 201, legacy.text


def _make_chapter_plannable(db_session, chapter_id: str) -> None:
    """Mark a revised chapter as parsed with full beat coverage so POST /plan
    reaches its success path (same recipe as test_mvp_workflow)."""

    chapter = db_session.get(Chapter, chapter_id)
    segments = list(
        db_session.scalars(
            select(SourceSegment)
            .where(SourceSegment.source_revision_id == chapter.current_source_revision_id)
            .order_by(SourceSegment.ordinal)
        )
    )
    scene = Scene(
        chapter_id=chapter.id,
        ordinal=1,
        location="解析完成",
        source_range={"segment_ids": [segment.id for segment in segments]},
    )
    db_session.add(scene)
    db_session.flush()
    for ordinal, segment in enumerate(segments, 1):
        db_session.add(
            Beat(
                scene_id=scene.id,
                ordinal=ordinal,
                action=segment.text,
                source_range={"segment_ids": [segment.id]},
            )
        )
    db_session.add(
        ScriptRevision(
            chapter_id=chapter.id,
            source_revision_id=chapter.current_source_revision_id,
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
    chapter.status = "SCRIPT_READY"
    db_session.commit()


def test_source_object_endpoints_accept_owning_project_and_legacy(client, scoped_world, db_session):
    """The sources.py sweep (chapter reads, plan/revise, script delete, chapter
    delete/restore, scene/beat PATCH) keeps working for the owning project and
    for legacy callers that never send project_id."""

    context = scoped_world["context"]
    owned = {"project_id": context["project_id_a"]}
    reads = [
        client.get(f"/api/v1/chapters/{context['chapter_id_a']}", params=owned),
        client.get(f"/api/v1/chapters/{context['chapter_id_a']}/segments", params=owned),
        client.get(f"/api/v1/chapters/{context['chapter_id_a']}/revisions", params=owned),
        client.get(f"/api/v1/chapters/{context['chapter_id_a']}/script", params=owned),
    ]
    assert [response.status_code for response in reads] == [200, 200, 200, 200]
    owned_scene = client.patch(
        f"/api/v1/scenes/{context['scene_id_a']}",
        params=owned,
        json={"location": "本项目场地", "version": 1},
    )
    assert owned_scene.status_code == 200, owned_scene.text
    owned_beat = client.patch(
        f"/api/v1/beats/{context['beat_id_a']}",
        params=owned,
        json={"action": "本项目节拍", "version": 1},
    )
    assert owned_beat.status_code == 200, owned_beat.text
    # The bare chapter has no pages, so revise reaches its write path directly.
    owned_revise = client.post(
        f"/api/v1/chapters/{context['bare_chapter_id_a']}/revisions",
        params=owned,
        json={"title": "本项目修订", "text": "第一段。\n第二段。", "source_type": "PASTE"},
    )
    assert owned_revise.status_code == 201, owned_revise.text
    _make_chapter_plannable(db_session, context["bare_chapter_id_a"])
    owned_plan = client.post(
        f"/api/v1/chapters/{context['bare_chapter_id_a']}/plan", params=owned, json={}
    )
    assert owned_plan.status_code == 200, owned_plan.text
    owned_script_delete = client.delete(
        f"/api/v1/chapters/{context['chapter_id_a']}/script", params=owned
    )
    assert owned_script_delete.status_code == 204, owned_script_delete.text
    owned_delete = client.delete(f"/api/v1/chapters/{context['bare_chapter_id_a']}", params=owned)
    assert owned_delete.status_code == 204, owned_delete.text
    owned_restore = client.post(
        f"/api/v1/chapters/{context['bare_chapter_id_a']}/restore", params=owned
    )
    assert owned_restore.status_code == 200, owned_restore.text

    legacy_reads = [
        client.get(f"/api/v1/chapters/{context['chapter_id_b']}"),
        client.get(f"/api/v1/chapters/{context['chapter_id_b']}/segments"),
        client.get(f"/api/v1/chapters/{context['chapter_id_b']}/revisions"),
        client.get(f"/api/v1/chapters/{context['chapter_id_b']}/script"),
    ]
    assert [response.status_code for response in legacy_reads] == [200, 200, 200, 200]
    legacy_scene = client.patch(
        f"/api/v1/scenes/{context['scene_id_b']}",
        json={"location": "旧客户端场地", "version": 1},
    )
    assert legacy_scene.status_code == 200, legacy_scene.text
    legacy_beat = client.patch(
        f"/api/v1/beats/{context['beat_id_b']}",
        json={"action": "旧客户端节拍", "version": 1},
    )
    assert legacy_beat.status_code == 200, legacy_beat.text
    legacy_revise = client.post(
        f"/api/v1/chapters/{context['bare_chapter_id_b']}/revisions",
        json={"text": "旧客户端正文。", "source_type": "PASTE"},
    )
    assert legacy_revise.status_code == 201, legacy_revise.text
    _make_chapter_plannable(db_session, context["bare_chapter_id_b"])
    legacy_plan = client.post(f"/api/v1/chapters/{context['bare_chapter_id_b']}/plan", json={})
    assert legacy_plan.status_code == 200, legacy_plan.text
    legacy_delete = client.delete(f"/api/v1/chapters/{context['bare_chapter_id_b']}")
    assert legacy_delete.status_code == 204, legacy_delete.text
    legacy_restore = client.post(f"/api/v1/chapters/{context['bare_chapter_id_b']}/restore")
    assert legacy_restore.status_code == 200, legacy_restore.text
    legacy_script_delete = client.delete(f"/api/v1/chapters/{context['chapter_id_b']}/script")
    assert legacy_script_delete.status_code == 204, legacy_script_delete.text


def test_cross_project_source_and_export_calls_leave_targets_intact(
    client, scoped_world, db_session
):
    """Every destructive sources.py/exports.py route must fail closed before
    mutating: the foreign chapter keeps its rows, script tree and export
    artifacts untouched."""

    context = scoped_world["context"]
    foreign = {"project_id": context["project_id_a"]}
    responses = [
        client.post(f"/api/v1/chapters/{context['chapter_id_b']}/plan", params=foreign, json={}),
        client.post(
            f"/api/v1/chapters/{context['chapter_id_b']}/revisions",
            params=foreign,
            json={"title": "越权标题", "text": "越权修订正文", "source_type": "PASTE"},
        ),
        client.delete(f"/api/v1/chapters/{context['chapter_id_b']}", params=foreign),
        client.post(f"/api/v1/chapters/{context['chapter_id_b']}/restore", params=foreign),
        client.delete(f"/api/v1/chapters/{context['chapter_id_b']}/script", params=foreign),
        client.patch(
            f"/api/v1/scenes/{context['scene_id_b']}",
            params=foreign,
            json={"location": "越权场地", "version": 1},
        ),
        client.patch(
            f"/api/v1/beats/{context['beat_id_b']}",
            params=foreign,
            json={"action": "越权节拍", "version": 1},
        ),
        client.post(
            f"/api/v1/chapters/{context['chapter_id_b']}/exports",
            params=foreign,
            json={"export_type": "PNG"},
        ),
    ]
    assert [response.status_code for response in responses] == [404] * 8
    db_session.expire_all()
    chapter = db_session.get(Chapter, context["chapter_id_b"])
    assert chapter.deleted_at is None
    assert chapter.version == 1
    assert chapter.title == "章节B"
    assert chapter.current_source_revision_id == context["source_revision_id_b"]
    scene = db_session.get(Scene, context["scene_id_b"])
    assert scene is not None
    assert scene.location == "场地B"
    assert scene.version == 1
    beat = db_session.get(Beat, context["beat_id_b"])
    assert beat is not None
    assert beat.action == "情节拍B"
    assert beat.version == 1
    assert db_session.get(ScriptRevision, context["script_revision_id_b"]) is not None
    assert db_session.get(SourceRevision, context["source_revision_id_b"]) is not None
    assert db_session.get(SourceSegment, context["segment_id_b"]) is not None
    assert db_session.get(MangaPage, context["page_id_b"]) is not None
    assert db_session.get(PageCandidate, context["page_candidate_id_b"]) is not None
    # The foreign export call minted no bundle row and no artifact file.
    bundles = list(
        db_session.scalars(
            select(ExportBundle).where(ExportBundle.chapter_id == context["chapter_id_b"])
        )
    )
    assert [bundle.id for bundle in bundles] == [context["export_id_b"]]
    exports_dir = (
        get_settings().storage_root / "exports" / context["project_id_b"] / context["chapter_id_b"]
    )
    assert list(exports_dir.iterdir()) == [exports_dir / "bundle.zip"]


def test_generation_selection_routes_scoping(client, scoped_world, db_session):
    """POST batches / select-candidate / keep / retract / next hide a foreign
    page behind the shared 404 and never touch its rows; without the param the
    historical readiness/state guards still fire in their original order."""

    context = scoped_world["context"]
    foreign = {"project_id": context["project_id_a"]}
    page_url = f"/api/v1/pages/{context['page_id_b']}"
    candidate_id = context["page_candidate_id_b"]
    keep_body = {
        "candidate_id": candidate_id,
        "storyboard_version": 1,
        "manual_text_confirmed": True,
    }
    responses = [
        client.post(f"{page_url}/batches", params=foreign),
        client.post(
            f"{page_url}/select-candidate",
            params=foreign,
            json={"candidate_id": candidate_id, "manual_text_confirmed": True},
        ),
        client.post(f"{page_url}/selected-candidate/keep", params=foreign, json=keep_body),
        client.delete(f"{page_url}/selected-candidate", params=foreign),
        client.post(f"{page_url}/next", params=foreign),
    ]
    assert [response.status_code for response in responses] == [404] * 5
    for response in responses:
        assert "不属于当前项目" in response.json()["detail"]
    db_session.expire_all()
    page = db_session.get(MangaPage, context["page_id_b"])
    assert page.version == 1
    assert page.status == PageStatus.PLANNED
    assert page.selected_candidate_id is None
    assert page.continuity_status == "NOT_CHECKED"
    batches = list(
        db_session.scalars(
            select(GenerationBatch).where(GenerationBatch.page_id == context["page_id_b"])
        )
    )
    assert [batch.id for batch in batches] == [context["batch_id_b"]]

    legacy = [
        client.post(f"{page_url}/batches"),
        client.post(
            f"{page_url}/select-candidate",
            json={"candidate_id": candidate_id, "manual_text_confirmed": True},
        ),
        client.post(f"{page_url}/selected-candidate/keep", json=keep_body),
        client.delete(f"{page_url}/selected-candidate"),
        client.post(f"{page_url}/next"),
    ]
    assert [response.status_code for response in legacy] == [409] * 5
    assert legacy[0].json()["detail"]["code"] == "PAGE_NOT_READY"

    owned = {"project_id": context["project_id_b"]}
    owned_batch = client.post(f"{page_url}/batches", params=owned)
    assert owned_batch.status_code == 409
    assert owned_batch.json()["detail"]["code"] == "PAGE_NOT_READY"
    owned_select = client.post(
        f"{page_url}/select-candidate",
        params=owned,
        json={"candidate_id": candidate_id, "manual_text_confirmed": True},
    )
    assert owned_select.status_code == 409
    assert owned_select.json()["detail"] == "该候选尚不能采用"


def test_paid_inspection_routes_scoping(client, scoped_world, db_session, monkeypatch):
    """inspect / repairs / upscale (paid PAGE_INSPECT/PAGE_REPAIR/PAGE_UPSCALE)
    fail closed for a foreign project before minting any job, batch or repair
    plan; the owning project and legacy callers keep the guarded behavior."""

    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    context = scoped_world["context"]
    candidate = db_session.get(PageCandidate, context["page_candidate_id_b"])
    candidate.asset_id = context["asset_id_b"]
    candidate.resolution = Resolution.STANDARD_2K
    db_session.commit()
    foreign = {"project_id": context["project_id_a"]}
    url = f"/api/v1/candidates/{candidate.id}"
    repair_body = {
        "inspection_result_id": "00000000-0000-0000-0000-000000000000",
        "repair_type": "PANEL",
        "model_alias": "test-model",
        "resolution": "2K",
    }
    responses = [
        client.post(f"{url}/inspect", params=foreign, json={}),
        client.post(f"{url}/repairs", params=foreign, json=repair_body),
        client.post(
            f"{url}/upscale", params=foreign, json={"model_alias": "test-model", "resolution": "4K"}
        ),
    ]
    assert [response.status_code for response in responses] == [404] * 3
    for response in responses:
        assert "不属于当前项目" in response.json()["detail"]
    db_session.expire_all()
    paid_jobs = list(
        db_session.scalars(
            select(GenerationJob).where(
                GenerationJob.target_id == candidate.id,
                GenerationJob.job_type.in_(["PAGE_INSPECT", "PAGE_REPAIR", "PAGE_UPSCALE"]),
            )
        )
    )
    assert paid_jobs == []
    batches = list(
        db_session.scalars(
            select(GenerationBatch).where(GenerationBatch.page_id == context["page_id_b"])
        )
    )
    assert [batch.id for batch in batches] == [context["batch_id_b"]]
    assert list(db_session.scalars(select(RepairPlan))) == []

    owned = {"project_id": context["project_id_b"]}
    inspected = client.post(f"{url}/inspect", params=owned, json={})
    assert inspected.status_code == 202, inspected.text
    repaired = client.post(f"{url}/repairs", params=owned, json=repair_body)
    assert repaired.status_code == 409
    assert repaired.json()["detail"] == "检查结果与候选不匹配"
    upscaled = client.post(
        f"{url}/upscale", params=owned, json={"model_alias": "test-model", "resolution": "2K"}
    )
    assert upscaled.status_code == 409
    assert upscaled.json()["detail"] == "升清目标必须高于当前候选清晰度"

    legacy_inspect = client.post(f"{url}/inspect", json={})
    assert legacy_inspect.status_code == 409
    assert "已有进行中的质检任务" in legacy_inspect.json()["detail"]
    legacy_upscale = client.post(
        f"{url}/upscale", json={"model_alias": "test-model", "resolution": "2K"}
    )
    assert legacy_upscale.status_code == 409


def _panel_version(db_session, panel_id: str) -> int:
    db_session.expire_all()
    return db_session.get(Panel, panel_id).version


def test_storyboard_panel_and_dialogue_routes_scoping(client, scoped_world, db_session):
    """Panel PATCH and dialogue create/patch/delete hide foreign storyboard
    objects behind the shared 404 and leave the rows intact; the owning
    project and legacy callers keep editing normally."""

    context = scoped_world["context"]
    foreign = {"project_id": context["project_id_a"]}
    owned = {"project_id": context["project_id_b"]}
    panel_id = context["panel_id_b"]
    dialogue_id = context["dialogue_id_b"]

    foreign_calls = [
        client.patch(
            f"/api/v1/panels/{panel_id}", params=foreign, json={"version": 1, "shot_type": "越权景别"}
        ),
        client.post(
            f"/api/v1/panels/{panel_id}/dialogues",
            params=foreign,
            json={"panel_version": 1, "target_text": "越权对白"},
        ),
        client.patch(
            f"/api/v1/dialogues/{dialogue_id}",
            params=foreign,
            json={"panel_version": 1, "target_text": "越权文本"},
        ),
        client.request(
            "DELETE",
            f"/api/v1/dialogues/{dialogue_id}",
            params=foreign,
            json={"panel_version": 1},
        ),
        client.patch(
            f"/api/v1/pages/{context['page_id_b']}/reading-order",
            params=foreign,
            json={"order": [panel_id]},
        ),
        client.put(
            f"/api/v1/pages/{context['page_id_b']}/storyboard-geometry",
            params=foreign,
            json={
                "request_id": "scope-foreign",
                "storyboard_version": 1,
                "panels": [
                    {
                        "panel_id": panel_id,
                        "bounds": {"x": 0.1, "y": 0.1, "width": 0.5, "height": 0.4},
                        "reading_order": 1,
                    }
                ],
            },
        ),
    ]
    assert [response.status_code for response in foreign_calls] == [404] * 6
    for response in foreign_calls:
        assert "不属于当前项目" in response.json()["detail"]
    db_session.expire_all()
    panel = db_session.get(Panel, panel_id)
    assert panel.version == 1
    assert panel.shot_type == "medium_close_up"
    dialogue_rows = list(
        db_session.scalars(select(Dialogue).where(Dialogue.panel_id == panel_id))
    )
    assert [row.id for row in dialogue_rows] == [dialogue_id]
    assert dialogue_rows[0].target_text == "隔离对白B"
    page = db_session.get(MangaPage, context["page_id_b"])
    assert page.storyboard_version == 1
    assert page.version == 1

    # Owning project: the layout PATCH runs past the scope gate and keeps its
    # historical script-traceability guard (the fixture page has no script).
    owned_layout = client.patch(
        f"/api/v1/pages/{context['page_id_b']}/layout",
        params=owned,
        json={"panel_count": 5},
    )
    assert owned_layout.status_code == 409
    assert owned_layout.json()["detail"] == "当前页缺少剧本或原文追溯，不能调整格数"

    owned_panel = client.patch(
        f"/api/v1/panels/{panel_id}",
        params=owned,
        json={"version": _panel_version(db_session, panel_id), "shot_type": "合法景别"},
    )
    assert owned_panel.status_code == 200, owned_panel.text
    legacy_create = client.post(
        f"/api/v1/panels/{panel_id}/dialogues",
        json={"panel_version": _panel_version(db_session, panel_id), "target_text": "旧客户端对白"},
    )
    assert legacy_create.status_code == 201, legacy_create.text
    new_dialogue_id = legacy_create.json()["id"]
    owned_patch = client.patch(
        f"/api/v1/dialogues/{dialogue_id}",
        params=owned,
        json={
            "panel_version": _panel_version(db_session, panel_id),
            "target_text": "合法文本",
        },
    )
    assert owned_patch.status_code == 200, owned_patch.text
    owned_delete = client.request(
        "DELETE",
        f"/api/v1/dialogues/{new_dialogue_id}",
        params=owned,
        json={"panel_version": _panel_version(db_session, panel_id)},
    )
    assert owned_delete.status_code == 204, owned_delete.text
    db_session.expire_all()
    assert db_session.get(Dialogue, new_dialogue_id) is None
    assert db_session.get(Dialogue, dialogue_id).target_text == "合法文本"


def test_scene_bind_asset_route_scoping(client, scoped_world, db_session):
    """PATCH /scenes/{id}/bind-asset hides a foreign scene and never bumps its
    version; owning project and legacy callers keep the unbind flow."""

    context = scoped_world["context"]
    foreign_bind = client.patch(
        f"/api/v1/scenes/{context['scene_id_b']}/bind-asset",
        params={"project_id": context["project_id_a"]},
        json={},
    )
    assert foreign_bind.status_code == 404, foreign_bind.text
    assert "不属于当前项目" in foreign_bind.json()["detail"]
    db_session.expire_all()
    scene = db_session.get(Scene, context["scene_id_b"])
    assert scene.version == 1
    assert scene.scene_asset_id is None
    assert scene.scene_asset_variant_id is None

    owned_bind = client.patch(
        f"/api/v1/scenes/{context['scene_id_b']}/bind-asset",
        params={"project_id": context["project_id_b"]},
        json={},
    )
    assert owned_bind.status_code == 200, owned_bind.text
    legacy_bind = client.patch(f"/api/v1/scenes/{context['scene_id_b']}/bind-asset", json={})
    assert legacy_bind.status_code == 200, legacy_bind.text


def test_asset_generation_batch_start_routes_scoping(client, scoped_world, db_session):
    """POST/GET /asset-generation-batches resolve the target row and hide a
    foreign target behind the shared 404 before minting any batch."""

    context = scoped_world["context"]
    foreign_start = client.post(
        "/api/v1/asset-generation-batches",
        params={"project_id": context["project_id_a"]},
        json={
            "target_type": "CHARACTER",
            "target_id": context["character_id_b"],
            "generation_kind": "CHARACTER",
        },
    )
    assert foreign_start.status_code == 404, foreign_start.text
    assert "不属于当前项目" in foreign_start.json()["detail"]
    foreign_list = client.get(
        "/api/v1/asset-generation-batches",
        params={
            "project_id": context["project_id_a"],
            "target_type": "CHARACTER",
            "target_id": context["character_id_b"],
        },
    )
    assert foreign_list.status_code == 404, foreign_list.text
    assert "不属于当前项目" in foreign_list.json()["detail"]
    foreign_batches = list(
        db_session.scalars(
            select(GenerationBatch).where(
                GenerationBatch.project_id == context["project_id_b"],
                GenerationBatch.target_type == "CHARACTER",
            )
        )
    )
    assert foreign_batches == []

    owned_start = client.post(
        "/api/v1/asset-generation-batches",
        params={"project_id": context["project_id_a"]},
        json={
            "target_type": "CHARACTER",
            "target_id": context["character_id_a"],
            "generation_kind": "CHARACTER",
        },
    )
    assert owned_start.status_code == 201, owned_start.text
    owned_list = client.get(
        "/api/v1/asset-generation-batches",
        params={
            "project_id": context["project_id_a"],
            "target_type": "CHARACTER",
            "target_id": context["character_id_a"],
        },
    )
    assert owned_list.status_code == 200, owned_list.text
    assert [batch["id"] for batch in owned_list.json()] == [owned_start.json()["id"]]
    legacy_list = client.get(
        "/api/v1/asset-generation-batches",
        params={"target_type": "CHARACTER", "target_id": context["character_id_b"]},
    )
    assert legacy_list.status_code == 200, legacy_list.text
