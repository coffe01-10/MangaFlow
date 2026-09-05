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

from app.config import get_settings
from app.domain.states import JobStatus, Resolution
from app.models import (
    Asset,
    AssetCandidate,
    Chapter,
    CharacterReference,
    ExportBundle,
    GenerationBatch,
    GenerationJob,
    MangaPage,
    ModelCallAttempt,
    Outfit,
    PageCandidate,
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

    def build(suffix: str, digest: str) -> dict:
        project = client.post("/api/v1/projects", json={"name": f"隔离项目{suffix}"}).json()
        project_id = project["id"]
        character = client.post(
            f"/api/v1/projects/{project_id}/characters",
            json={"primary_name": f"角色{suffix}", "aliases": []},
        ).json()
        chapter = _orm(db_session, Chapter(project_id=project_id, title=f"章节{suffix}", ordinal=1))
        page = _orm(db_session, MangaPage(chapter_id=chapter.id, page_number=1))
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
        export_key = f"exports/{project_id}/{chapter.id}/bundle.zip"
        export_bundle = _orm(
            db_session,
            ExportBundle(
                project_id=project_id,
                chapter_id=chapter.id,
                export_type="PNG",
                storage_key=export_key,
                byte_size=8,
                sha256=f"{digest}export",
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
            "chapter_id": chapter.id,
            "page_id": page.id,
            "batch_id": batch.id,
            "page_candidate_id": page_candidate.id,
            "asset_candidate_id": asset_candidate.id,
            "completed_job_id": completed_job.id,
            "failed_job_id": failed_job.id,
            "outfit_id": outfit.id,
            "style_id": style.id,
            "asset_id": asset.id,
            "export_id": export_bundle.id,
            "workflow_id": workflow["id"],
            "workflow_version_id": workflow_version["id"],
            "run_id": run.id,
            "reference_id": reference.id,
            "attempt_direct_id": attempt_direct.id,
            "attempt_via_job_id": attempt_via_job.id,
        }

    world = {"a": build("A", "a" * 64), "b": build("B", "b" * 64)}
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
