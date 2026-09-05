"""Regression tests: workflow export nodes must be idempotent across job reclaim.

Verified chain: the export node handler (workflow_engine/execution.py) calls
``create_export``, which unconditionally ``db.add``s an ExportBundle row and
commits. Lease-expiry reclaim (recover resets the job to WAITING -> re-claim)
and RQ redelivery after a crash between create_export's commit and the job
completion CAS both re-execute the handler, duplicating ExportBundle rows.
The content-hash artifact token only dedupes the FILE on disk, not the ROW.
"""

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.domain.states import JobStatus, Resolution
from app.models import (
    Asset,
    Chapter,
    ExportBundle,
    GenerationBatch,
    GenerationJob,
    InspectionResult,
    MangaPage,
    PageCandidate,
    Project,
    WorkflowDefinition,
    WorkflowNodeRun,
)
from app.schemas import ExportRequest
from app.services.workflow_engine import (
    create_workflow_run,
    execute_workflow_node,
    publish_workflow,
)

REQUIRED_QUALITY_CATEGORIES = ("SPEAKER", "CHARACTER", "OUTFIT", "PROP", "CONTINUITY")


@pytest.fixture
def storage_root(tmp_path, monkeypatch):
    root = tmp_path / "storage"
    monkeypatch.setattr(get_settings(), "storage_root", root)
    return root


def _chapter_export_chain_graph() -> dict:
    return {
        "nodes": [
            {
                "id": "pages",
                "type": "source.approved_pages",
                "name": "成品页面",
                "position": {"x": 0, "y": 0},
                "inputs": [],
                "outputs": [
                    {
                        "id": "pages",
                        "label": "生产通过页面",
                        "data_type": "asset",
                        "required": False,
                    }
                ],
                "config": {},
            },
            {
                "id": "export",
                "type": "output.chapter_export",
                "name": "整章导出",
                "position": {"x": 300, "y": 0},
                "inputs": [
                    {
                        "id": "pages",
                        "label": "生产通过页面",
                        "data_type": "asset",
                        "required": True,
                    }
                ],
                "outputs": [
                    {
                        "id": "files",
                        "label": "导出文件",
                        "data_type": "asset",
                        "required": False,
                    }
                ],
                "config": {},
            },
        ],
        "edges": [
            {
                "id": "pages:pages-export:pages",
                "source_node": "pages",
                "source_port": "pages",
                "target_node": "export",
                "target_port": "pages",
            }
        ],
    }


def _seed_export_ready_chapter(db) -> dict:
    """Seed a chapter with one page that passes the production readiness gate."""
    project = Project(name="导出幂等测试项目")
    db.add(project)
    db.flush()
    chapter = Chapter(
        project_id=project.id, ordinal=1, title="第一章", status="PAGES_PLANNED"
    )
    db.add(chapter)
    db.flush()
    batch = GenerationBatch(project_id=project.id, chapter_id=chapter.id, ordinal=1)
    db.add(batch)
    db.flush()
    asset = Asset(
        project_id=project.id,
        kind="PAGE_IMAGE",
        original_name="page-0001.png",
        storage_key="test/page-0001.png",
        mime_type="image/png",
        byte_size=1024,
        width=512,
        height=512,
        sha256="export-idempotency-page-hash",
    )
    db.add(asset)
    db.flush()
    page = MangaPage(
        chapter_id=chapter.id,
        page_number=1,
        storyboard_version=1,
        source_coverage={"complete": True},
    )
    db.add(page)
    db.flush()
    candidate = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=1,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        status="INSPECTED",
        asset_id=asset.id,
        is_selected=True,
    )
    db.add(candidate)
    db.flush()
    page.selected_candidate_id = candidate.id
    page.selected_candidate_ack_version = page.storyboard_version
    page.continuity_status = "PASSED"
    for category in REQUIRED_QUALITY_CATEGORIES:
        db.add(
            InspectionResult(
                candidate_id=candidate.id,
                storyboard_version=page.storyboard_version,
                category=category,
                outcome="PASS",
            )
        )
    db.commit()
    return {
        "project": project,
        "chapter": chapter,
        "page": page,
        "candidate": candidate,
    }


def _seed_export_node_run(db, monkeypatch, chapter: Chapter) -> WorkflowNodeRun:
    """Publish the chain graph and create a CHAPTER-scoped run like the API does."""
    # 只拦截 enqueue：避免本地执行器在测试后派发后台任务。
    monkeypatch.setattr("app.services.workflow_engine.enqueue_job", lambda db, job: job)
    workflow = WorkflowDefinition(
        project_id=chapter.project_id,
        name="导出幂等工作流",
        draft_graph=_chapter_export_chain_graph(),
        is_active=True,
    )
    db.add(workflow)
    db.commit()
    publish_workflow(db, workflow)
    create_workflow_run(
        db,
        workflow,
        scope_type="CHAPTER",
        scope_id=chapter.id,
        start_node_ids=[],
        stop_node_ids=[],
    )
    node_run = db.scalar(
        select(WorkflowNodeRun).where(
            WorkflowNodeRun.node_id == "export",
            WorkflowNodeRun.node_type.in_(["output.export", "output.chapter_export"]),
        )
    )
    assert node_run is not None, "运行计划必须为导出节点创建节点运行"
    return node_run


def _bundles_for_chapter(db, chapter_id) -> list[ExportBundle]:
    return list(
        db.scalars(select(ExportBundle).where(ExportBundle.chapter_id == chapter_id))
    )


def test_export_node_reexecution_keeps_single_bundle(
    db_session, storage_root, monkeypatch
):
    """T1: re-running the handler for the same job (lease reclaim / redelivery)
    must not insert a second ExportBundle row."""
    seeded = _seed_export_ready_chapter(db_session)
    node_run = _seed_export_node_run(db_session, monkeypatch, seeded["chapter"])
    job = db_session.get(GenerationJob, node_run.job_id)
    assert job is not None and job.job_type == "WORKFLOW_NODE"

    execute_workflow_node(db_session, job)
    execute_workflow_node(db_session, job)

    bundles = _bundles_for_chapter(db_session, seeded["chapter"].id)
    assert len(bundles) == 1
    bundle = bundles[0]
    assert node_run.output_refs["export_id"] == bundle.id
    assert node_run.output_refs["export_type"] == "JSON"
    assert node_run.output_refs["storage_key"] == bundle.storage_key
    assert (storage_root / bundle.storage_key).is_file()


def test_route_create_export_without_flag_keeps_row_per_call(db_session, storage_root):
    """T2: direct create_export calls keep the historical behavior — every call
    without the worker-side dedupe flag inserts its own row."""
    from app.api.routes.exports import create_export

    seeded = _seed_export_ready_chapter(db_session)
    first = create_export(
        seeded["chapter"].id, ExportRequest(export_type="JSON"), db_session
    )
    second = create_export(
        seeded["chapter"].id, ExportRequest(export_type="JSON"), db_session
    )

    assert first.id != second.id
    assert len(_bundles_for_chapter(db_session, seeded["chapter"].id)) == 2


def test_export_node_reclaim_returns_existing_bundle_identity(
    db_session, storage_root, monkeypatch
):
    """T3: the reuse path must return the EXISTING bundle's identity (not a
    fresh insert) and the handler must complete normally for the retry job."""
    seeded = _seed_export_ready_chapter(db_session)
    node_run = _seed_export_node_run(db_session, monkeypatch, seeded["chapter"])
    job = db_session.get(GenerationJob, node_run.job_id)
    assert job is not None

    execute_workflow_node(db_session, job)
    original = db_session.get(ExportBundle, node_run.output_refs["export_id"])
    assert original is not None

    # Worst case: a fresh job row for the same node run, as a new attempt after
    # recovery would look like to the handler.
    retry_job = GenerationJob(
        project_id=job.project_id,
        target_type=job.target_type,
        target_id=node_run.id,
        job_type="WORKFLOW_NODE",
        status=JobStatus.WAITING,
    )
    db_session.add(retry_job)
    db_session.commit()

    execute_workflow_node(db_session, retry_job)

    bundles = _bundles_for_chapter(db_session, seeded["chapter"].id)
    assert len(bundles) == 1
    assert bundles[0].id == original.id
    assert node_run.output_refs["export_id"] == original.id
    assert node_run.output_refs["job_id"] == retry_job.id
    assert node_run.output_refs["storage_key"] == original.storage_key


def test_export_node_reuses_identical_bundle_created_via_route(
    db_session, storage_root, monkeypatch
):
    """A bundle the route already committed for the same deterministic artifact
    must be reused by the node path instead of duplicated."""
    from app.api.routes.exports import create_export

    seeded = _seed_export_ready_chapter(db_session)
    route_bundle = create_export(
        seeded["chapter"].id, ExportRequest(export_type="JSON"), db_session
    )
    node_run = _seed_export_node_run(db_session, monkeypatch, seeded["chapter"])
    job = db_session.get(GenerationJob, node_run.job_id)
    assert job is not None

    execute_workflow_node(db_session, job)

    bundles = _bundles_for_chapter(db_session, seeded["chapter"].id)
    assert len(bundles) == 1
    assert bundles[0].id == route_bundle.id
    assert node_run.output_refs["export_id"] == route_bundle.id
