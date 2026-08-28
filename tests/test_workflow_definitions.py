from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier

import pytest
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.database import Base
from app.domain.states import JobStatus
from app.model_adapters.base import ModelResponse
from app.models import (
    Beat,
    Chapter,
    GenerationJob,
    JobDependency,
    MangaPage,
    PageCandidate,
    Panel,
    Project,
    Scene,
    ScriptRevision,
    SourceSegment,
    WorkflowDefinition,
    WorkflowNodeRun,
    WorkflowRun,
    WorkflowVersion,
    utcnow,
)
from app.services.ai_schemas import InspectionItem, PageInspectionOutput
from app.services import workflow_engine
from app.services.workflow_engine import (
    PublishRevisionConflictError,
    chapter_export_graph,
    default_graph,
    execute_workflow_node,
    node_type_catalog,
    publish_workflow,
    reconcile_run,
    validate_graph,
)
from app.worker_tasks import _run_inspection, _run_page_generate


def _project(client):
    response = client.post("/api/v1/projects", json={"name": "工作流项目"})
    assert response.status_code == 201
    return response.json()


def _workflow(client, project_id: str):
    response = client.post(
        f"/api/v1/projects/{project_id}/workflows",
        json={"name": "漫画生产线", "template": "manga_default"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _png_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (48, 64), (242, 239, 231)).save(output, format="PNG")
    return output.getvalue()


class DeterministicWorkflowAdapter:
    def generate_page(self, _request):
        return ModelResponse(
            model_id="fake-nano-banana-2",
            request_id="fake-page-request",
            usage={"input_tokens": 1, "output_images": 1},
            images=(_png_bytes(),),
        )

    def analyze_multimodal(self, _request, output_schema):
        assert output_schema is PageInspectionOutput
        return PageInspectionOutput(
            items=[
                InspectionItem(
                    category=category,
                    outcome="PASS",
                    score=1.0,
                    severity="INFO",
                    details={"expected": "deterministic", "observed": "deterministic"},
                    regions=[],
                )
                for category in ["SPEAKER", "CHARACTER", "OUTFIT", "PROP", "CONTINUITY"]
            ]
        )


def _complete_job(db_session, run_id: str, job: GenerationJob, runner) -> None:
    job.status = JobStatus.PREPARING
    job.error_code = None
    job.error_message = None
    job.started_at = job.started_at or utcnow()
    job.attempt_count += 1
    runner(db_session, job)
    job.status = JobStatus.COMPLETED
    job.progress = 100
    job.finished_at = utcnow()
    db_session.commit()
    reconcile_run(db_session, run_id)


def test_default_graph_is_strict_and_valid():
    graph = default_graph()
    report = validate_graph(graph)
    assert report.valid is True
    assert report.topological_order[0] in {"chapter", "assets"}
    assert report.topological_order[-1] == "complete"

    invalid = deepcopy(graph)
    invalid["edges"][0]["target_port"] = "missing"
    report = validate_graph(invalid)
    assert report.valid is False
    assert {item.code for item in report.issues} >= {"UNKNOWN_PORT", "MISSING_REQUIRED_INPUT"}

    cyclic = deepcopy(graph)
    cyclic["edges"].append(
        {
            "id": "cycle",
            "source_node": "inspect",
            "source_port": "report",
            "target_node": "parse",
            "target_port": "source",
        }
    )
    report = validate_graph(cyclic)
    assert report.valid is False
    assert {item.code for item in report.issues} >= {"PORT_TYPE_MISMATCH", "CYCLE_DETECTED"}


def test_chapter_export_graph_is_separate_and_valid():
    graph = chapter_export_graph()
    report = validate_graph(graph)

    assert report.valid is True
    assert report.topological_order == ["pages", "export"]
    assert {node["type"] for node in graph["nodes"]} == {
        "source.approved_pages",
        "output.chapter_export",
    }


def test_legacy_page_export_node_schema_remains_valid():
    graph = default_graph()
    legacy_spec = next(item for item in node_type_catalog() if item.type == "output.export")
    complete = next(node for node in graph["nodes"] if node["id"] == "complete")
    complete["type"] = "output.export"
    complete["inputs"] = [item.model_dump(mode="json") for item in legacy_spec.inputs]
    complete["outputs"] = [item.model_dump(mode="json") for item in legacy_spec.outputs]

    assert validate_graph(graph).valid is True


def test_workflow_crud_optimistic_lock_publish_restore_and_json(client, db_session):
    project = _project(client)
    workflow = _workflow(client, project["id"])

    listed = client.get(f"/api/v1/projects/{project['id']}/workflows")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [workflow["id"]]

    stale = client.patch(
        f"/api/v1/workflows/{workflow['id']}",
        json={"version": workflow["version"] + 1, "name": "冲突名称"},
    )
    assert stale.status_code == 409

    graph = workflow["draft_graph"]
    graph["nodes"][0]["position"] = {"x": 88, "y": 99}
    updated = client.patch(
        f"/api/v1/workflows/{workflow['id']}",
        json={"version": workflow["version"], "draft_graph": graph},
    )
    assert updated.status_code == 200, updated.text
    updated_workflow = updated.json()
    assert updated_workflow["draft_version"] == workflow["draft_version"] + 1

    validated = client.post(f"/api/v1/workflows/{workflow['id']}/validate")
    assert validated.status_code == 200
    assert validated.json()["valid"] is True

    published = client.post(f"/api/v1/workflows/{workflow['id']}/publish")
    assert published.status_code == 200, published.text
    immutable_graph = deepcopy(published.json()["graph"])
    version_id = published.json()["id"]

    current = client.get(f"/api/v1/workflows/{workflow['id']}").json()
    changed = deepcopy(current["draft_graph"])
    changed["nodes"][0]["name"] = "后来修改"
    saved = client.patch(
        f"/api/v1/workflows/{workflow['id']}",
        json={"version": current["version"], "draft_graph": changed},
    )
    assert saved.status_code == 200
    assert db_session.get(WorkflowVersion, version_id).graph == immutable_graph

    restored = client.post(
        f"/api/v1/workflow-versions/{version_id}/restore",
        json={"version": saved.json()["version"]},
    )
    assert restored.status_code == 200
    assert restored.json()["draft_graph"] == immutable_graph

    exported = client.get(f"/api/v1/workflows/{workflow['id']}/export")
    assert exported.status_code == 200
    assert exported.json()["schema"] == "mangaflow.workflow.v2"

    imported = client.post(
        f"/api/v1/projects/{project['id']}/workflows/import",
        json={
            "name": "复制生产线",
            "description": "JSON 导入",
            "graph": exported.json()["graph"],
        },
    )
    assert imported.status_code == 201, imported.text


def test_run_pauses_before_single_page_generation_and_reuses_jobs(client, db_session):
    project = _project(client)
    workflow = _workflow(client, project["id"])
    published = client.post(f"/api/v1/workflows/{workflow['id']}/publish")
    assert published.status_code == 200

    chapter = Chapter(project_id=project["id"], title="第一章", ordinal=1, status="PAGES_PLANNED")
    db_session.add(chapter)
    db_session.flush()
    page = MangaPage(
        chapter_id=chapter.id,
        page_number=1,
        panel_count=4,
        scene_ids=["scene"],
        beat_ids=["beat"],
        source_coverage={"complete": True},
    )
    db_session.add(page)
    db_session.commit()

    settings = get_settings()
    previous_queue = settings.queue_enabled
    settings.queue_enabled = False
    try:
        response = client.post(
            f"/api/v1/workflows/{workflow['id']}/runs",
            json={
                "scope_type": "PAGE",
                "scope_id": page.id,
                "start_node_ids": ["generate"],
                "stop_node_ids": ["generate"],
            },
        )
        assert response.status_code == 202, response.text
        run = response.json()
        assert run["status"] == "PAUSED"
        generate = next(item for item in run["node_runs"] if item["node_id"] == "generate")
        assert generate["status"] == "WAITING_APPROVAL"
        assert generate["job_id"] is None

        approved = client.post(
            f"/api/v1/workflow-runs/{run['id']}/nodes/generate/approve",
            json={"image_model_alias": "image.nano_banana_pro", "resolution": "1K"},
        )
        assert approved.status_code == 200, approved.text
        generate = approved.json()["node_runs"][0]
        assert generate["status"] == "RUNNING"
        assert generate["job_id"]
        candidate = db_session.get(PageCandidate, generate["output_refs"]["candidate_id"])
        assert candidate is not None
        assert candidate.ordinal == 1
        assert candidate.model_alias == "image.nano_banana_pro"

        duplicate = client.post(
            f"/api/v1/workflow-runs/{run['id']}/nodes/generate/approve",
            json={"image_model_alias": "image.nano_banana_2", "resolution": "1K"},
        )
        assert duplicate.status_code == 409
        assert db_session.query(PageCandidate).count() == 1

        cancelled = client.post(f"/api/v1/jobs/{generate['job_id']}/cancel")
        assert cancelled.status_code == 200, cancelled.text
        stopped = client.get(f"/api/v1/workflow-runs/{run['id']}")
        assert stopped.status_code == 200
        assert stopped.json()["status"] == "CANCELLED"
        db_session.expire_all()
        assert db_session.get(PageCandidate, candidate.id).status == "CANCELLED"
    finally:
        settings.queue_enabled = previous_queue


def test_generation_gate_requires_explicit_equal_model_choice(client, db_session):
    project = _project(client)
    workflow = _workflow(client, project["id"])
    assert client.post(f"/api/v1/workflows/{workflow['id']}/publish").status_code == 200
    chapter = Chapter(project_id=project["id"], title="第一章", ordinal=1)
    db_session.add(chapter)
    db_session.flush()
    page = MangaPage(chapter_id=chapter.id, page_number=1)
    db_session.add(page)
    db_session.commit()

    settings = get_settings()
    previous_queue = settings.queue_enabled
    settings.queue_enabled = False
    try:
        run = client.post(
            f"/api/v1/workflows/{workflow['id']}/runs",
            json={
                "scope_type": "PAGE",
                "scope_id": page.id,
                "start_node_ids": ["generate"],
                "stop_node_ids": ["generate"],
            },
        ).json()
        response = client.post(
            f"/api/v1/workflow-runs/{run['id']}/nodes/generate/approve",
            json={"resolution": "1K"},
        )
        assert response.status_code == 409
        assert "明确选择" in response.json()["detail"]
        assert db_session.query(PageCandidate).count() == 0

        automatic = client.post(
            f"/api/v1/workflow-runs/{run['id']}/nodes/generate/approve",
            json={"image_model_alias": "auto", "resolution": "1K"},
        )
        assert automatic.status_code == 409
        assert "明确选择" in automatic.json()["detail"]
        assert db_session.query(PageCandidate).count() == 0
    finally:
        settings.queue_enabled = previous_queue


def test_default_dag_deterministic_full_run_to_export(
    client, db_session, monkeypatch
):
    with TemporaryDirectory() as directory:
        settings = get_settings()
        previous_queue = settings.queue_enabled
        monkeypatch.setattr(settings, "queue_enabled", False)
        monkeypatch.setattr(settings, "storage_root", Path(directory) / "storage")
        monkeypatch.setattr(settings, "upload_root", Path(directory) / "uploads")
        adapter = DeterministicWorkflowAdapter()
        monkeypatch.setattr("app.worker_tasks._adapter", lambda _alias: adapter)

        project = _project(client)
        workflow = _workflow(client, project["id"])
        published = client.post(f"/api/v1/workflows/{workflow['id']}/publish")
        assert published.status_code == 200
        assert published.json()["revision"] == 1
        imported = client.post(
            f"/api/v1/projects/{project['id']}/sources/import",
            json={"title": "第一章", "text": "雨停了。\n\n她推开门，决定把真相说清楚。"},
        ).json()["chapters"][0]
        segment = db_session.query(SourceSegment).filter_by(
            source_revision_id=imported["current_source_revision_id"]
        ).first()
        chapter = db_session.get(Chapter, imported["id"])
        chapter.status = "PAGES_PLANNED"
        scene = Scene(
            chapter_id=chapter.id,
            ordinal=1,
            source_range={"segment_ids": [segment.id]},
        )
        db_session.add(scene)
        db_session.flush()
        beat = Beat(
            scene_id=scene.id,
            ordinal=1,
            action="她推开门。",
            source_range={"segment_ids": [segment.id]},
        )
        db_session.add(beat)
        db_session.flush()
        script = ScriptRevision(
            chapter_id=chapter.id,
            source_revision_id=chapter.current_source_revision_id,
            revision_no=1,
            status="READY",
            coverage={"complete": True, "segment_ids": [segment.id]},
        )
        page = MangaPage(
            chapter_id=chapter.id,
            page_number=1,
            scene_ids=[scene.id],
            beat_ids=[beat.id],
            panel_count=3,
            source_coverage={
                "complete": True,
                "ranges": [
                    {
                        "segment_id": segment.id,
                        "start_offset": 0,
                        "end_offset": len(segment.text),
                        "text": segment.text,
                    }
                ],
            },
        )
        db_session.add_all([script, page])
        db_session.flush()
        db_session.add(
            Panel(
                page_id=page.id,
                reading_order=1,
                bounds={"x": 0.02, "y": 0.02, "width": 0.96, "height": 0.96},
                actions={"source_text": segment.text},
            )
        )
        db_session.commit()

        started = client.post(
            f"/api/v1/workflows/{workflow['id']}/runs",
            json={"scope_type": "PAGE", "scope_id": page.id},
        )
        assert started.status_code == 202, started.text
        run_id = started.json()["id"]

        def node_run(node_id: str) -> WorkflowNodeRun:
            return db_session.query(WorkflowNodeRun).filter_by(
                workflow_run_id=run_id, node_id=node_id
            ).one()

        parse_job = db_session.get(GenerationJob, node_run("parse").job_id)
        _complete_job(db_session, run_id, parse_job, lambda *_args: None)
        for node_id in ("adapt", "storyboard"):
            job = db_session.get(GenerationJob, node_run(node_id).job_id)
            _complete_job(db_session, run_id, job, execute_workflow_node)

        paused = client.get(f"/api/v1/workflow-runs/{run_id}").json()
        assert paused["status"] == "PAUSED"
        assert next(
            item for item in paused["node_runs"] if item["node_id"] == "generate"
        )["status"] == "WAITING_APPROVAL"

        approved_generation = client.post(
            f"/api/v1/workflow-runs/{run_id}/nodes/generate/approve",
            json={"image_model_alias": "image.nano_banana_2", "resolution": "1K"},
        )
        assert approved_generation.status_code == 200, approved_generation.text
        generate_run = node_run("generate")
        generate_job = db_session.get(GenerationJob, generate_run.job_id)
        generate_job.status = JobStatus.FAILED
        generate_job.error_code = "UPSTREAM"
        generate_job.started_at = generate_job.created_at
        generate_job.finished_at = generate_job.created_at
        generate_run.status = "FAILED"
        run_record = db_session.get(WorkflowRun, run_id)
        run_record.status = "FAILED"
        db_session.commit()
        retried = client.post(f"/api/v1/jobs/{generate_job.id}/retry")
        assert retried.status_code == 200
        assert retried.json()["started_at"] is None
        assert retried.json()["finished_at"] is None
        assert db_session.get(WorkflowRun, run_id).status == "RUNNING"
        assert node_run("generate").status == "RUNNING"
        _complete_job(db_session, run_id, generate_job, _run_page_generate)
        candidate_id = node_run("generate").output_refs["candidate_id"]
        candidate = db_session.get(PageCandidate, candidate_id)
        assert candidate.status == "READY"
        assert candidate.based_on_storyboard_version == page.storyboard_version

        adoption_pause = client.get(f"/api/v1/workflow-runs/{run_id}").json()
        assert adoption_pause["status"] == "PAUSED"
        selected = client.post(
            f"/api/v1/pages/{page.id}/select-candidate",
            json={"candidate_id": candidate_id, "manual_text_confirmed": True},
        )
        assert selected.status_code == 200, selected.text
        adopted = client.post(
            f"/api/v1/workflow-runs/{run_id}/nodes/adopt/approve",
            json={"candidate_id": candidate_id},
        )
        assert adopted.status_code == 200, adopted.text

        inspection_job = db_session.get(GenerationJob, node_run("inspect").job_id)
        _complete_job(db_session, run_id, inspection_job, _run_inspection)
        complete_job = db_session.get(GenerationJob, node_run("complete").job_id)
        _complete_job(db_session, run_id, complete_job, execute_workflow_node)

        completed = client.get(f"/api/v1/workflow-runs/{run_id}").json()
        assert completed["status"] == "COMPLETED"
        assert len(completed["node_runs"]) == len(default_graph()["nodes"])
        assert {item["status"] for item in completed["node_runs"]} == {"COMPLETED"}
        assert node_run("complete").output_refs["asset_id"] == candidate.asset_id

        dependencies = {
            (item.job_id, item.depends_on_job_id)
            for item in db_session.query(JobDependency).all()
        }
        assert (inspection_job.id, generate_job.id) in dependencies
        assert (complete_job.id, inspection_job.id) in dependencies
        job_count = db_session.query(GenerationJob).count()
        reconcile_run(db_session, run_id)
        reconcile_run(db_session, run_id)
        assert db_session.query(GenerationJob).count() == job_count
        settings.queue_enabled = previous_queue


def test_node_catalog_and_soft_delete(client):
    catalog = client.get("/api/v1/workflow-node-types")
    assert catalog.status_code == 200
    types = {item["type"] for item in catalog.json()}
    assert {
        "source.chapter",
        "source.approved_pages",
        "generator.page",
        "control.approval",
        "output.page",
        "output.export",
        "output.chapter_export",
    } <= types

    project = _project(client)
    workflow = _workflow(client, project["id"])
    deleted = client.delete(f"/api/v1/workflows/{workflow['id']}")
    assert deleted.status_code == 204
    assert client.get(f"/api/v1/workflows/{workflow['id']}").status_code == 404


def _file_session_factory():
    directory = TemporaryDirectory(ignore_cleanup_errors=True)
    engine = create_engine(
        f"sqlite:///{Path(directory.name) / 'publish.db'}",
        connect_args={"check_same_thread": False},
    )
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    return directory, factory


def _seed_publishable_workflow(factory) -> str:
    with factory() as db:
        project = Project(name="并发发布")
        db.add(project)
        db.flush()
        workflow = WorkflowDefinition(
            project_id=project.id,
            name="单页生产流程",
            draft_graph=default_graph(),
        )
        db.add(workflow)
        db.commit()
        return workflow.id


@pytest.fixture
def publish_sessions(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'concurrent-publish.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    try:
        yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    finally:
        engine.dispose()


@pytest.mark.parametrize("invalid", [False, True])
def test_publish_refreshes_and_validates_current_draft(publish_sessions, invalid):
    factory = publish_sessions
    workflow_id = _seed_publishable_workflow(factory)
    with factory() as first:
        stale = first.get(WorkflowDefinition, workflow_id)
        with factory() as other:
            current = other.get(WorkflowDefinition, workflow_id)
            graph = deepcopy(current.draft_graph)
            graph["nodes"][0]["name"] = "新草稿"
            if invalid:
                graph["nodes"][0]["type"] = "unknown.node"
            current.draft_graph = graph
            current.version += 1
            other.commit()
        if invalid:
            with pytest.raises(ValueError, match="校验失败"):
                publish_workflow(first, stale)
        else:
            published = publish_workflow(first, stale)
            assert published.graph == graph
            assert published.validation_report == validate_graph(graph).model_dump(
                mode="json"
            )
    with factory() as db:
        current = db.get(WorkflowDefinition, workflow_id)
        assert current.version == (2 if invalid else 3)
        if invalid:
            assert current.published_version_id is None
            assert list(db.scalars(select(WorkflowVersion))) == []


def test_simultaneous_sqlite_publishes_are_controlled(publish_sessions, monkeypatch):
    factory = publish_sessions
    workflow_id = _seed_publishable_workflow(factory)
    barrier = Barrier(2, timeout=10)
    original = workflow_engine._next_revision

    def allocate_together(db, target_id):
        revision = original(db, target_id)
        if not db.info.get("publish_synchronized"):
            db.info["publish_synchronized"] = True
            barrier.wait()
        return revision

    monkeypatch.setattr(workflow_engine, "_next_revision", allocate_together)

    def publish_once():
        with factory() as db:
            try:
                return publish_workflow(db, db.get(WorkflowDefinition, workflow_id))
            except Exception as error:
                return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: publish_once(), range(2)))
    assert all(
        isinstance(item, (WorkflowVersion, PublishRevisionConflictError))
        for item in results
    ), results
    assert any(isinstance(item, WorkflowVersion) for item in results)
    with factory() as db:
        versions = list(
            db.scalars(select(WorkflowVersion).order_by(WorkflowVersion.revision))
        )
        assert [item.revision for item in versions] == list(range(1, len(versions) + 1))
        current = db.get(WorkflowDefinition, workflow_id)
        assert current.published_version_id == versions[-1].id
        assert current.version == 1 + len(versions)


@pytest.mark.parametrize("error_code", [sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED])
def test_sqlite_lock_conflict_returns_409_and_allows_retry(
    client, db_session, monkeypatch, error_code
):
    project = _project(client)
    workflow = _workflow(client, project["id"])
    first = client.post(f"/api/v1/workflows/{workflow['id']}/publish")
    assert first.status_code == 200
    calls = []

    def fail_with_lock(_db, _workflow_id):
        calls.append(True)
        error = sqlite3.OperationalError("database is locked")
        error.sqlite_errorcode = error_code
        raise OperationalError("INSERT", {}, error)

    with monkeypatch.context() as patch:
        patch.setattr(workflow_engine, "_next_revision", fail_with_lock)
        conflicted = client.post(f"/api/v1/workflows/{workflow['id']}/publish")
    assert conflicted.status_code == 409
    assert len(calls) == workflow_engine.PUBLISH_REVISION_MAX_ATTEMPTS
    current = db_session.get(WorkflowDefinition, workflow["id"])
    assert current.published_version_id == first.json()["id"]
    second = client.post(f"/api/v1/workflows/{workflow['id']}/publish")
    assert second.status_code == 200, second.text
    assert second.json()["revision"] == 2


def test_publish_does_not_hide_unrelated_database_errors(publish_sessions, monkeypatch):
    workflow_id = _seed_publishable_workflow(publish_sessions)
    error = sqlite3.OperationalError("no such table")
    error.sqlite_errorcode = sqlite3.SQLITE_ERROR

    def fail(_db, _workflow_id):
        raise OperationalError("SELECT", {}, error)

    monkeypatch.setattr(workflow_engine, "_next_revision", fail)
    with publish_sessions() as db:
        with pytest.raises(OperationalError, match="no such table"):
            publish_workflow(db, db.get(WorkflowDefinition, workflow_id))


def test_independent_sessions_publish_successive_revisions():
    directory, factory = _file_session_factory()
    try:
        workflow_id = _seed_publishable_workflow(factory)
        with factory() as first:
            first_version = publish_workflow(
                first, first.get(WorkflowDefinition, workflow_id)
            )
        with factory() as second:
            second_version = publish_workflow(
                second, second.get(WorkflowDefinition, workflow_id)
            )

        assert first_version.revision == 1
        assert second_version.revision == 2
        with factory() as db:
            revisions = sorted(
                db.scalars(
                    select(WorkflowVersion.revision).where(
                        WorkflowVersion.workflow_id == workflow_id
                    )
                )
            )
            workflow = db.get(WorkflowDefinition, workflow_id)
            assert revisions == [1, 2]
            assert workflow.published_version_id == second_version.id
    finally:
        directory.cleanup()


def test_publish_retries_unique_revision_after_integrity_error(monkeypatch):
    directory, factory = _file_session_factory()
    try:
        workflow_id = _seed_publishable_workflow(factory)
        with factory() as other:
            first = publish_workflow(other, other.get(WorkflowDefinition, workflow_id))
        original = workflow_engine._next_revision
        calls = {"n": 0}

        def collide_then_allocate(db, target_id):
            calls["n"] += 1
            if calls["n"] == 1:
                return 1
            return original(db, target_id)

        monkeypatch.setattr(workflow_engine, "_next_revision", collide_then_allocate)

        with factory() as db:
            published = publish_workflow(db, db.get(WorkflowDefinition, workflow_id))

        assert first.revision == 1
        assert published.revision == 2
        assert calls["n"] == 2
        with factory() as db:
            workflow = db.get(WorkflowDefinition, workflow_id)
            revisions = sorted(
                db.scalars(
                    select(WorkflowVersion.revision).where(
                        WorkflowVersion.workflow_id == workflow_id
                    )
                )
            )
            assert revisions == [1, 2]
            assert workflow.published_version_id == published.id
    finally:
        directory.cleanup()


def test_publish_revision_conflict_returns_409_and_keeps_pointer(client, db_session, monkeypatch):
    project = _project(client)
    workflow = _workflow(client, project["id"])
    first = client.post(f"/api/v1/workflows/{workflow['id']}/publish")
    assert first.status_code == 200, first.text
    pointer = db_session.get(WorkflowDefinition, workflow["id"]).published_version_id
    assert pointer == first.json()["id"]

    monkeypatch.setattr(workflow_engine, "_next_revision", lambda _db, _workflow_id: 1)
    conflicted = client.post(f"/api/v1/workflows/{workflow['id']}/publish")
    assert conflicted.status_code == 409
    assert conflicted.json()["detail"] == "工作流正在被其他请求发布，请稍后重试"

    db_session.expire_all()
    current = db_session.get(WorkflowDefinition, workflow["id"])
    assert current.published_version_id == pointer
    revisions = list(
        db_session.scalars(
            select(WorkflowVersion.revision).where(WorkflowVersion.workflow_id == workflow["id"])
        )
    )
    assert revisions == [1]


def test_exhausted_revision_retries_do_not_raise_unhandled_error(monkeypatch):
    directory, factory = _file_session_factory()
    try:
        workflow_id = _seed_publishable_workflow(factory)
        with factory() as db:
            publish_workflow(db, db.get(WorkflowDefinition, workflow_id))

        monkeypatch.setattr(workflow_engine, "_next_revision", lambda _db, _workflow_id: 1)
        with factory() as db:
            workflow = db.get(WorkflowDefinition, workflow_id)
            pointer = workflow.published_version_id
            original_version = workflow.version
            with pytest.raises(PublishRevisionConflictError, match="请稍后重试"):
                publish_workflow(db, workflow, max_attempts=2)
            db.expire_all()
            current = db.get(WorkflowDefinition, workflow_id)
            assert current.published_version_id == pointer
            assert current.version == original_version
            assert list(
                db.scalars(
                    select(WorkflowVersion.revision).where(
                        WorkflowVersion.workflow_id == workflow_id
                    )
                )
            ) == [1]
    finally:
        directory.cleanup()
