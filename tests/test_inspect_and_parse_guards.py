"""Guards: inspect failure must not own the inspected candidate; re-parse must not wipe live pages."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.domain.states import JobStatus, Resolution
from app.model_adapters.base import ProviderAdapterError
from app.models import (
    Asset,
    Chapter,
    GenerationBatch,
    GenerationJob,
    MangaPage,
    PageCandidate,
    Project,
    Scene,
    ScriptRevision,
    WorkflowDefinition,
    WorkflowNodeRun,
    WorkflowRun,
    WorkflowVersion,
    utcnow,
)
from app.services import job_service
from app.services.job_service import mark_job_failed
from app.services.worker_handlers.story_parse import _run_story_parse
from app.services.workflow_engine.reconciliation import _create_inspection_job
from app.worker_tasks import _mark_worker_failure
from app.workflow_schemas import WorkflowGraph, WorkflowNodeDefinition


def _own_lease(db, job, owner="owner-inspect-guard"):
    db.info["job_id"] = job.id
    db.info["job_lease_owner"] = owner
    job.lease_owner = owner
    job.lease_expires_at = utcnow() + timedelta(minutes=5)
    job.attempt_count = max(job.attempt_count or 0, 1)
    db.commit()
    return owner


def _ready_candidate(db, *, candidate_status="READY"):
    project = Project(name="质检失败所有权")
    db.add(project)
    db.flush()
    chapter = Chapter(project_id=project.id, title="第一章", ordinal=1)
    db.add(chapter)
    db.flush()
    page = MangaPage(chapter_id=chapter.id, page_number=1, storyboard_version=1)
    db.add(page)
    db.flush()
    batch = GenerationBatch(
        project_id=project.id, chapter_id=chapter.id, page_id=page.id, ordinal=1
    )
    asset = Asset(
        project_id=project.id,
        kind="page_candidate",
        original_name="ready.png",
        storage_key="generated/ready.png",
        mime_type="image/png",
        byte_size=10,
        sha256="c" * 64,
        source="VERTEX_GENERATED",
        status="GENERATED",
    )
    db.add_all([batch, asset])
    db.flush()
    generate_job = GenerationJob(
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id="pending",
        job_type="PAGE_GENERATE",
        status=JobStatus.COMPLETED,
    )
    db.add(generate_job)
    db.flush()
    candidate = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=1,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        status=candidate_status,
        asset_id=asset.id,
        job_id=generate_job.id,
        is_selected=True,
    )
    db.add(candidate)
    db.flush()
    generate_job.target_id = candidate.id
    page.selected_candidate_id = candidate.id
    db.commit()
    return project, page, candidate, generate_job


def test_inspect_failure_does_not_mark_ready_candidate_failed(db_session):
    project, _page, candidate, generate_job = _ready_candidate(db_session)
    inspect_job = GenerationJob(
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_INSPECT",
        status=JobStatus.CONSISTENCY_CHECKING,
    )
    db_session.add(inspect_job)
    db_session.commit()
    owner = _own_lease(db_session, inspect_job)

    marked, _, is_final = _mark_worker_failure(
        db_session,
        inspect_job.id,
        owner,
        "STALE_STORYBOARD_VERSION",
        "分镜版本已变化",
        candidate_status="STALE",
        retryable=False,
    )
    assert marked is True and is_final is True
    db_session.expire_all()
    assert db_session.get(GenerationJob, inspect_job.id).status == JobStatus.FAILED
    assert db_session.get(PageCandidate, candidate.id).status == "READY"
    assert db_session.get(PageCandidate, candidate.id).job_id == generate_job.id


def test_page_generate_failure_still_marks_owned_candidate_failed(db_session):
    project, _page, candidate, generate_job = _ready_candidate(
        db_session, candidate_status="QUEUED"
    )
    generate_job.status = JobStatus.GENERATING
    db_session.commit()
    owner = _own_lease(db_session, generate_job)

    marked, _, is_final = _mark_worker_failure(
        db_session,
        generate_job.id,
        owner,
        "WORKER_ERROR",
        "生成失败",
        retryable=False,
    )
    assert marked is True and is_final is True
    db_session.expire_all()
    assert db_session.get(PageCandidate, candidate.id).status == "FAILED"


def test_mark_job_failed_does_not_clobber_inspected_target(db_session):
    project, _page, candidate, _generate_job = _ready_candidate(db_session)
    inspect_job = GenerationJob(
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_INSPECT",
        status=JobStatus.CONSISTENCY_CHECKING,
    )
    db_session.add(inspect_job)
    db_session.commit()

    mark_job_failed(db_session, inspect_job, "WORKER_ERROR", "质检失败")
    db_session.commit()
    db_session.expire_all()
    assert db_session.get(GenerationJob, inspect_job.id).status == JobStatus.FAILED
    assert db_session.get(PageCandidate, candidate.id).status == "READY"


def test_create_job_retries_after_failed_inspect_idempotency(db_session):
    project, _page, candidate, _generate_job = _ready_candidate(db_session)
    key = f"inspect:{candidate.id}:{candidate.version}"
    failed = job_service.create_job(
        db_session,
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_INSPECT",
        idempotency_key=key,
    )
    failed.status = JobStatus.FAILED
    failed.error_code = "STALE_STORYBOARD_VERSION"
    db_session.commit()

    retried = job_service.create_job(
        db_session,
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_INSPECT",
        idempotency_key=key,
    )
    assert retried.id != failed.id
    assert retried.status == JobStatus.WAITING
    assert retried.idempotency_key == key
    db_session.refresh(failed)
    assert failed.idempotency_key == f"closed:{failed.id}"


def test_failed_inspect_http_retry_enqueues_new_job(client, db_session, monkeypatch):
    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    project = client.post("/api/v1/projects", json={"name": "质检失败可重试"}).json()
    chapter = Chapter(project_id=project["id"], title="第一章", ordinal=1)
    db_session.add(chapter)
    db_session.flush()
    page = MangaPage(chapter_id=chapter.id, page_number=1)
    db_session.add(page)
    db_session.flush()
    batch = GenerationBatch(
        project_id=project["id"], chapter_id=chapter.id, page_id=page.id, ordinal=1
    )
    asset = Asset(
        project_id=project["id"],
        kind="page_candidate",
        original_name="retry.png",
        storage_key="generated/retry.png",
        mime_type="image/png",
        byte_size=10,
        sha256="d" * 64,
        source="VERTEX_GENERATED",
        status="GENERATED",
    )
    db_session.add_all([batch, asset])
    db_session.flush()
    candidate = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=1,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        status="READY",
        asset_id=asset.id,
    )
    db_session.add(candidate)
    db_session.flush()
    failed = GenerationJob(
        project_id=project["id"],
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_INSPECT",
        status=JobStatus.FAILED,
        idempotency_key=f"inspect:{candidate.id}:{candidate.version}",
    )
    db_session.add(failed)
    db_session.commit()

    retried = client.post(
        f"/api/v1/candidates/{candidate.id}/inspect",
        json={"categories": ["CONTINUITY"]},
    )
    assert retried.status_code == 202, retried.json()
    assert retried.json()["id"] != failed.id
    assert retried.json()["job_type"] == "PAGE_INSPECT"


def test_workflow_inspect_job_does_not_auto_commit(db_session, monkeypatch):
    project, page, candidate, _generate_job = _ready_candidate(db_session)
    graph = WorkflowGraph(
        nodes=[
            WorkflowNodeDefinition(id="inspect", type="quality.inspect", name="质量检查")
        ]
    )
    workflow = WorkflowDefinition(
        project_id=project.id, name="质检事务", draft_graph=graph.model_dump(mode="json")
    )
    db_session.add(workflow)
    db_session.flush()
    version = WorkflowVersion(
        workflow_id=workflow.id,
        revision=1,
        graph=graph.model_dump(mode="json"),
        graph_checksum="inspect-txn",
    )
    db_session.add(version)
    db_session.flush()
    run = WorkflowRun(
        workflow_id=workflow.id,
        workflow_version_id=version.id,
        project_id=project.id,
        scope_type="PAGE",
        scope_id=page.id,
        status="RUNNING",
    )
    db_session.add(run)
    db_session.flush()
    node_run = WorkflowNodeRun(
        workflow_run_id=run.id,
        node_id="inspect",
        node_type="quality.inspect",
        status="WAITING",
        output_refs={"candidate_id": candidate.id},
    )
    db_session.add(node_run)
    db_session.commit()

    captured: dict = {}
    real_create_job = job_service.create_job

    def recorder(*args, **kwargs):
        captured["auto_commit"] = kwargs.get("auto_commit", True)
        return real_create_job(*args, **kwargs)

    monkeypatch.setattr("app.services.workflow_engine.create_job", recorder)
    job = _create_inspection_job(
        db_session, run, graph, graph.nodes[0], node_run, [node_run]
    )
    assert captured["auto_commit"] is False
    assert node_run.job_id == job.id
    assert job.id


def test_parse_chapter_rejects_when_pages_exist(client, db_session):
    project = client.post("/api/v1/projects", json={"name": "已有分页禁止再解析"}).json()
    imported = client.post(
        f"/api/v1/projects/{project['id']}/sources/import",
        json={"title": "第一章", "text": "顾川推开门。"},
    )
    assert imported.status_code == 201
    chapter_id = imported.json()["chapters"][0]["id"]
    db_session.add(
        MangaPage(chapter_id=chapter_id, page_number=1, scene_ids=["old-scene-id"])
    )
    db_session.commit()

    response = client.post(f"/api/v1/chapters/{chapter_id}/parse")
    assert response.status_code == 409
    assert "已有分页" in response.json()["detail"]


def test_story_parse_does_not_wipe_scenes_when_pages_exist(client, db_session, monkeypatch):
    project = client.post("/api/v1/projects", json={"name": "解析不得打散分页场景"}).json()
    imported = client.post(
        f"/api/v1/projects/{project['id']}/sources/import",
        json={"title": "第一章", "text": "顾川推开门。"},
    ).json()
    chapter_id = imported["chapters"][0]["id"]
    scene = Scene(chapter_id=chapter_id, ordinal=1, location="教室")
    db_session.add(scene)
    db_session.flush()
    db_session.add(
        MangaPage(chapter_id=chapter_id, page_number=1, scene_ids=[scene.id])
    )
    db_session.commit()

    def forbid_paid_call(*_args, **_kwargs):
        raise AssertionError("已有分页时不得发起解析模型调用")

    monkeypatch.setattr(
        "app.services.worker_handlers.story_parse.provider._binding",
        forbid_paid_call,
    )
    job = GenerationJob(
        project_id=project["id"],
        target_type="CHAPTER",
        target_id=chapter_id,
        job_type="SOURCE_PARSE",
        status=JobStatus.PREPARING,
    )
    db_session.add(job)
    db_session.commit()

    with pytest.raises(ProviderAdapterError) as excinfo:
        _run_story_parse(db_session, job)
    assert excinfo.value.code == "CHAPTER_HAS_PAGES"
    assert excinfo.value.retryable is False
    assert db_session.scalar(select(Scene.id).where(Scene.id == scene.id)) == scene.id
    page = db_session.scalar(select(MangaPage).where(MangaPage.chapter_id == chapter_id))
    assert page.scene_ids == [scene.id]


def test_plan_rejects_while_source_parse_is_active(client, db_session):
    project = client.post("/api/v1/projects", json={"name": "解析中禁止分页"}).json()
    imported = client.post(
        f"/api/v1/projects/{project['id']}/sources/import",
        json={"title": "第一章", "text": "顾川推开门。"},
    ).json()
    chapter_id = imported["chapters"][0]["id"]
    scene = Scene(chapter_id=chapter_id, ordinal=1, location="教室")
    db_session.add(scene)
    db_session.flush()
    chapter = db_session.get(Chapter, chapter_id)
    chapter.status = "SCRIPT_READY"
    db_session.add(
        GenerationJob(
            project_id=project["id"],
            target_type="CHAPTER",
            target_id=chapter_id,
            job_type="SOURCE_PARSE",
            status=JobStatus.GENERATING,
        )
    )
    db_session.commit()

    response = client.post(
        f"/api/v1/chapters/{chapter_id}/plan",
        json={"replace_existing": True},
    )
    assert response.status_code == 409
    assert "正在生成剧本" in response.json()["detail"]
    assert db_session.scalar(select(MangaPage.id).where(MangaPage.chapter_id == chapter_id)) is None


def test_story_parse_aborts_if_pages_appear_before_persist(client, db_session, monkeypatch):
    from types import SimpleNamespace

    from app.services.ai_schemas import BeatDraft, CharacterDraft, SceneDraft, StoryParseOutput

    project = client.post("/api/v1/projects", json={"name": "解析提交前插入分页"}).json()
    imported = client.post(
        f"/api/v1/projects/{project['id']}/sources/import",
        json={"title": "第一章", "text": "顾川推开门。"},
    ).json()
    chapter_id = imported["chapters"][0]["id"]
    scene = Scene(chapter_id=chapter_id, ordinal=1, location="教室")
    db_session.add(scene)
    db_session.commit()
    scene_id = scene.id

    def invoke_provider(_db, _binding, _fn):
        if db_session.scalar(select(MangaPage.id).where(MangaPage.chapter_id == chapter_id)) is None:
            db_session.add(
                MangaPage(chapter_id=chapter_id, page_number=1, scene_ids=[scene_id])
            )
            db_session.flush()
        return StoryParseOutput(
            characters=[CharacterDraft(primary_name="顾川")],
            scenes=[
                SceneDraft(
                    ordinal=1,
                    location="新教室",
                    source_segment_ids=[],
                    beats=[BeatDraft(ordinal=1, action="他推门")],
                )
            ],
        )

    monkeypatch.setattr(
        "app.services.worker_handlers.story_parse.provider._binding",
        lambda *args, **kwargs: SimpleNamespace(
            resolved=SimpleNamespace(model=SimpleNamespace(id=None)),
        ),
    )
    monkeypatch.setattr(
        "app.services.worker_handlers.story_parse.provider._invoke_provider",
        invoke_provider,
    )
    job = GenerationJob(
        project_id=project["id"],
        target_type="CHAPTER",
        target_id=chapter_id,
        job_type="SOURCE_PARSE",
        status=JobStatus.PREPARING,
    )
    db_session.add(job)
    db_session.commit()

    with pytest.raises(ProviderAdapterError) as excinfo:
        _run_story_parse(db_session, job)
    assert excinfo.value.code == "CHAPTER_HAS_PAGES"
    assert db_session.scalar(select(Scene.id).where(Scene.id == scene_id)) == scene_id
    page = db_session.scalar(select(MangaPage).where(MangaPage.chapter_id == chapter_id))
    assert page.scene_ids == [scene_id]


def test_story_parse_reuses_ready_script_when_pages_exist(client, db_session, monkeypatch):
    project = client.post("/api/v1/projects", json={"name": "工作流复用已有剧本"}).json()
    imported = client.post(
        f"/api/v1/projects/{project['id']}/sources/import",
        json={"title": "第一章", "text": "顾川推开门。"},
    ).json()
    chapter_id = imported["chapters"][0]["id"]
    chapter = db_session.get(Chapter, chapter_id)
    scene = Scene(chapter_id=chapter_id, ordinal=1, location="教室")
    db_session.add(scene)
    db_session.flush()
    db_session.add(
        ScriptRevision(
            chapter_id=chapter_id,
            source_revision_id=chapter.current_source_revision_id,
            revision_no=1,
            status="READY",
            coverage={"ratio": 1},
        )
    )
    db_session.add(MangaPage(chapter_id=chapter_id, page_number=1, scene_ids=[scene.id]))
    db_session.commit()

    def forbid_paid_call(*_args, **_kwargs):
        raise AssertionError("已有分页且剧本 READY 时不得再解析")

    monkeypatch.setattr(
        "app.services.worker_handlers.story_parse.provider._binding",
        forbid_paid_call,
    )
    job = GenerationJob(
        project_id=project["id"],
        target_type="CHAPTER",
        target_id=chapter_id,
        job_type="SOURCE_PARSE",
        status=JobStatus.PREPARING,
    )
    db_session.add(job)
    db_session.commit()
    _run_story_parse(db_session, job)
    assert db_session.scalar(select(Scene.id).where(Scene.id == scene.id)) == scene.id
    page = db_session.scalar(select(MangaPage).where(MangaPage.chapter_id == chapter_id))
    assert page.scene_ids == [scene.id]


def test_revise_source_rejects_while_source_parse_is_active(client, db_session):
    project = client.post("/api/v1/projects", json={"name": "解析中禁止修订原文"}).json()
    imported = client.post(
        f"/api/v1/projects/{project['id']}/sources/import",
        json={"title": "第一章", "text": "顾川推开门。"},
    ).json()
    chapter_id = imported["chapters"][0]["id"]
    db_session.add(
        GenerationJob(
            project_id=project["id"],
            target_type="CHAPTER",
            target_id=chapter_id,
            job_type="SOURCE_PARSE",
            status=JobStatus.GENERATING,
        )
    )
    db_session.commit()
    response = client.post(
        f"/api/v1/chapters/{chapter_id}/revisions",
        json={"title": "第一章", "text": "顾川关上门。", "source_type": "PASTE"},
    )
    assert response.status_code == 409
    assert "正在生成剧本" in response.json()["detail"]
