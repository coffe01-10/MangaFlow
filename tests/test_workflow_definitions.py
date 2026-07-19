from copy import deepcopy
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from app.config import get_settings
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
    Scene,
    ScriptRevision,
    SourceSegment,
    WorkflowNodeRun,
    WorkflowRun,
    WorkflowVersion,
    utcnow,
)
from app.services.ai_schemas import InspectionItem, PageInspectionOutput
from app.services.workflow_engine import (
    default_graph,
    execute_workflow_node,
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
    assert report.topological_order[-1] == "export"

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
        export_job = db_session.get(GenerationJob, node_run("export").job_id)
        _complete_job(db_session, run_id, export_job, execute_workflow_node)

        completed = client.get(f"/api/v1/workflow-runs/{run_id}").json()
        assert completed["status"] == "COMPLETED"
        assert len(completed["node_runs"]) == len(default_graph()["nodes"])
        assert {item["status"] for item in completed["node_runs"]} == {"COMPLETED"}
        assert node_run("export").output_refs["export_id"]

        dependencies = {
            (item.job_id, item.depends_on_job_id)
            for item in db_session.query(JobDependency).all()
        }
        assert (inspection_job.id, generate_job.id) in dependencies
        assert (export_job.id, inspection_job.id) in dependencies
        job_count = db_session.query(GenerationJob).count()
        reconcile_run(db_session, run_id)
        reconcile_run(db_session, run_id)
        assert db_session.query(GenerationJob).count() == job_count
        settings.queue_enabled = previous_queue


def test_node_catalog_and_soft_delete(client):
    catalog = client.get("/api/v1/workflow-node-types")
    assert catalog.status_code == 200
    types = {item["type"] for item in catalog.json()}
    assert {"source.chapter", "generator.page", "control.approval", "output.export"} <= types

    project = _project(client)
    workflow = _workflow(client, project["id"])
    deleted = client.delete(f"/api/v1/workflows/{workflow['id']}")
    assert deleted.status_code == 204
    assert client.get(f"/api/v1/workflows/{workflow['id']}").status_code == 404
