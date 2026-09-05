"""Guards: inspect failure must not own the inspected candidate; re-parse must not wipe live pages."""

from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import sessionmaker

from app import worker_tasks
from app.config import get_settings
from app.database import Base
from app.domain.states import JobStatus, PageStatus, Resolution
from app.model_adapters.base import ProviderAdapterError
from app.models import (
    Asset,
    Chapter,
    GenerationBatch,
    GenerationJob,
    InspectionResult,
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
from app.services.page_completion import REQUIRED_QUALITY_CATEGORIES
from app.services.worker_handlers.story_parse import _run_story_parse
from app.services.workflow_engine.reconciliation import _create_inspection_job, reconcile_run
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


# The former test_mark_job_failed_does_not_clobber_inspected_target pinned one
# guarantee on the now-deleted dead helper mark_job_failed: a terminal PAGE_INSPECT
# failure must not stamp the adopted candidate FAILED. That guarantee lives on the
# live worker-failure path (test_inspect_failure_does_not_mark_ready_candidate_failed,
# test_terminal_inspect_failure_restores_final_checking_page) and on the recovery
# path (tests/test_inspect_exit_recovery.py).


def _adopt_candidate(page, candidate) -> None:
    """Mirror select_candidate's post-adoption write: page parked in
    FINAL_CHECKING until a fresh PAGE_INSPECT resolves it."""

    page.selected_candidate_ack_version = page.storyboard_version
    page.status = PageStatus.FINAL_CHECKING
    page.continuity_status = "NOT_CHECKED"
    page.version += 1


def _extra_candidate(db, page, *, ordinal=2, is_selected=False, status="READY"):
    batch = db.scalar(select(GenerationBatch).where(GenerationBatch.page_id == page.id))
    candidate = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=ordinal,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        status=status,
        is_selected=is_selected,
    )
    db.add(candidate)
    db.commit()
    return candidate


def _inspect_job(db, project, candidate) -> GenerationJob:
    job = GenerationJob(
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_INSPECT",
        status=JobStatus.CONSISTENCY_CHECKING,
    )
    db.add(job)
    db.commit()
    return job


def test_terminal_inspect_failure_restores_final_checking_page(db_session):
    """A terminal PAGE_INSPECT failure on the adopted candidate must converge
    the page to NEEDS_REPAIR instead of leaving it stuck FINAL_CHECKING."""
    project, page, candidate, _generate_job = _ready_candidate(db_session)
    _adopt_candidate(page, candidate)
    db_session.commit()
    inspect_job = _inspect_job(db_session, project, candidate)
    owner = _own_lease(db_session, inspect_job)
    version_before = page.version

    marked, _, is_final = _mark_worker_failure(
        db_session,
        inspect_job.id,
        owner,
        "WORKER_ERROR",
        "质检执行器崩溃",
        retryable=False,
    )
    assert marked is True and is_final is True
    db_session.expire_all()
    assert db_session.get(GenerationJob, inspect_job.id).status == JobStatus.FAILED
    restored = db_session.get(MangaPage, page.id)
    assert restored.status == PageStatus.NEEDS_REPAIR
    assert restored.continuity_status == "NEEDS_REVIEW"
    assert restored.version == version_before + 1
    # The inspected candidate holds adopted work and must stay untouched.
    assert db_session.get(PageCandidate, candidate.id).status == "READY"
    assert db_session.get(PageCandidate, candidate.id).is_selected is True


def test_cancelled_inspect_job_restores_final_checking_page(db_session):
    """Cancelling a PAGE_INSPECT job must not leave the adopted page parked in
    FINAL_CHECKING either (workflow cancel_run sweeps inspect jobs)."""
    project, page, candidate, _generate_job = _ready_candidate(db_session)
    _adopt_candidate(page, candidate)
    db_session.commit()
    inspect_job = _inspect_job(db_session, project, candidate)
    version_before = page.version

    job_service.mark_job_cancelled(db_session, inspect_job)
    db_session.commit()
    db_session.expire_all()
    assert db_session.get(GenerationJob, inspect_job.id).status == JobStatus.CANCELLED
    restored = db_session.get(MangaPage, page.id)
    assert restored.status == PageStatus.NEEDS_REPAIR
    assert restored.continuity_status == "NEEDS_REVIEW"
    assert restored.version == version_before + 1
    assert db_session.get(PageCandidate, candidate.id).status == "READY"


def test_terminal_inspect_failure_ignores_non_selected_candidate(db_session):
    """A terminal inspect failure on a non-adopted candidate must not touch the
    page: the FINAL_CHECKING gate belongs to the selected candidate's run."""
    project, page, selected, _generate_job = _ready_candidate(db_session)
    other = _extra_candidate(db_session, page)
    _adopt_candidate(page, selected)
    db_session.commit()
    inspect_job = _inspect_job(db_session, project, other)
    owner = _own_lease(db_session, inspect_job)

    marked, _, _ = _mark_worker_failure(
        db_session,
        inspect_job.id,
        owner,
        "WORKER_ERROR",
        "质检执行器崩溃",
        retryable=False,
    )
    assert marked is True
    db_session.expire_all()
    restored = db_session.get(MangaPage, page.id)
    assert restored.status == PageStatus.FINAL_CHECKING
    assert restored.continuity_status == "NOT_CHECKED"
    assert db_session.get(PageCandidate, other.id).status == "READY"


def test_terminal_inspect_failure_does_not_downgrade_final_ready_page(db_session):
    """A failed re-inspection must not downgrade a page whose gate already
    completed (FINAL_READY from a prior passing inspection)."""
    project, page, candidate, _generate_job = _ready_candidate(db_session)
    page.selected_candidate_ack_version = page.storyboard_version
    page.status = PageStatus.FINAL_READY
    page.continuity_status = "PASSED"
    page.version += 1
    db_session.commit()
    inspect_job = _inspect_job(db_session, project, candidate)
    owner = _own_lease(db_session, inspect_job)

    marked, _, _ = _mark_worker_failure(
        db_session,
        inspect_job.id,
        owner,
        "WORKER_ERROR",
        "质检执行器崩溃",
        retryable=False,
    )
    assert marked is True
    db_session.expire_all()
    restored = db_session.get(MangaPage, page.id)
    assert restored.status == PageStatus.FINAL_READY
    assert restored.continuity_status == "PASSED"


def test_terminal_inspect_failure_on_non_selected_non_checking_page_is_noop(db_session):
    """Non-selected candidate plus a page outside FINAL_CHECKING: the exit
    restore must be a complete no-op on both status and continuity."""
    project, page, selected, _generate_job = _ready_candidate(db_session)
    other = _extra_candidate(db_session, page)
    page.status = PageStatus.DRAFT_READY
    page.continuity_status = "NOT_CHECKED"
    db_session.commit()
    inspect_job = _inspect_job(db_session, project, other)
    owner = _own_lease(db_session, inspect_job)

    marked, _, _ = _mark_worker_failure(
        db_session,
        inspect_job.id,
        owner,
        "WORKER_ERROR",
        "质检执行器崩溃",
        retryable=False,
    )
    assert marked is True
    db_session.expire_all()
    restored = db_session.get(MangaPage, page.id)
    assert restored.status == PageStatus.DRAFT_READY
    assert restored.continuity_status == "NOT_CHECKED"


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


def _active_inspect_job_ids(db, candidate_id) -> list[str]:
    return list(
        db.scalars(
            select(GenerationJob.id).where(
                GenerationJob.job_type == "PAGE_INSPECT",
                GenerationJob.target_type == "PAGE_CANDIDATE",
                GenerationJob.target_id == candidate_id,
                GenerationJob.status.in_(job_service.ACTIVE_JOB_STATUSES),
            )
        )
    )


def test_inspect_rejects_while_retried_failed_job_is_still_active(
    client, db_session, monkeypatch
):
    """A FAILED inspect job whose key was collapsed to closed:{id} and that was
    then retried via reset_for_retry is ACTIVE again; re-inspect must not spawn
    a second paid PAGE_INSPECT job for the same candidate."""
    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    project, _page, candidate, _generate_job = _ready_candidate(db_session)
    old = job_service.create_job(
        db_session,
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_INSPECT",
        idempotency_key=f"inspect:{candidate.id}:{candidate.version}",
    )
    old.status = JobStatus.FAILED
    # Same rewrite create_job performs when it collapses a terminal duplicate.
    old.idempotency_key = f"closed:{old.id}"
    db_session.commit()

    job_service.reset_for_retry(db_session, old)
    db_session.expire_all()
    assert db_session.get(GenerationJob, old.id).status == JobStatus.WAITING

    response = client.post(
        f"/api/v1/candidates/{candidate.id}/inspect",
        json={"categories": ["CONTINUITY"]},
    )
    assert response.status_code == 409, response.json()
    assert _active_inspect_job_ids(db_session, candidate.id) == [old.id]


def test_inspect_rejects_while_queued_inspect_job_is_active(client, db_session):
    """An in-flight (QUEUED) inspect job blocks a second inspect for the same
    candidate instead of silently returning it or creating a duplicate."""
    project, _page, candidate, _generate_job = _ready_candidate(db_session)
    queued = GenerationJob(
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_INSPECT",
        status=JobStatus.QUEUED,
        idempotency_key=f"inspect:{candidate.id}:{candidate.version}",
    )
    db_session.add(queued)
    db_session.commit()

    response = client.post(
        f"/api/v1/candidates/{candidate.id}/inspect",
        json={"categories": ["CONTINUITY"]},
    )
    assert response.status_code == 409, response.json()
    assert _active_inspect_job_ids(db_session, candidate.id) == [queued.id]


def test_inspect_still_creates_new_job_after_unretried_terminal_failure(
    client, db_session, monkeypatch
):
    """A terminal FAILED inspect with no active replacement keeps the
    collapse+recreate path: re-inspect after failure must stay possible."""
    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    project, _page, candidate, _generate_job = _ready_candidate(db_session)
    failed = job_service.create_job(
        db_session,
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_INSPECT",
        idempotency_key=f"inspect:{candidate.id}:{candidate.version}",
    )
    failed.status = JobStatus.FAILED
    failed.idempotency_key = f"closed:{failed.id}"
    db_session.commit()

    response = client.post(
        f"/api/v1/candidates/{candidate.id}/inspect",
        json={"categories": ["CONTINUITY"]},
    )
    assert response.status_code == 202, response.json()
    assert response.json()["id"] != failed.id
    assert _active_inspect_job_ids(db_session, candidate.id) == [response.json()["id"]]


def test_create_inspection_job_adopts_route_created_active_inspect_job(
    db_session, monkeypatch
):
    """The workflow reconcile path must adopt an already-active PAGE_INSPECT
    job for the candidate instead of creating a second paid duplicate."""
    project, page, candidate, _generate_job = _ready_candidate(db_session)
    graph = WorkflowGraph(
        nodes=[
            WorkflowNodeDefinition(id="inspect", type="quality.inspect", name="质量检查")
        ]
    )
    workflow = WorkflowDefinition(
        project_id=project.id, name="质检收养", draft_graph=graph.model_dump(mode="json")
    )
    db_session.add(workflow)
    db_session.flush()
    version = WorkflowVersion(
        workflow_id=workflow.id,
        revision=1,
        graph=graph.model_dump(mode="json"),
        graph_checksum="inspect-adopt",
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
    )
    db_session.add(node_run)
    db_session.flush()
    route_job = GenerationJob(
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_INSPECT",
        status=JobStatus.QUEUED,
    )
    db_session.add(route_job)
    db_session.commit()

    job = _create_inspection_job(db_session, run, graph, graph.nodes[0], node_run, [node_run])
    assert job.id == route_job.id
    assert node_run.job_id == route_job.id
    duplicates = db_session.scalars(
        select(GenerationJob.id).where(
            GenerationJob.job_type == "PAGE_INSPECT",
            GenerationJob.target_id == candidate.id,
        )
    )
    assert list(duplicates) == [route_job.id]


def test_executed_adopted_inspect_job_reconciles_its_workflow_run(monkeypatch):
    """execute_job's success path must reconcile the run of an adopted
    route-created inspect job: its request_parameters carry no workflow_run_id,
    so the WorkflowNodeRun.job_id link is the only reference to the run.
    Without that fallback the completed node and its run stall RUNNING forever
    (the run-list endpoint never reconciles)."""
    with TemporaryDirectory() as directory:
        engine = create_engine(
            f"sqlite:///{Path(directory) / 'adopt-success.db'}",
            connect_args={"check_same_thread": False},
        )
        testing_session = sessionmaker(
            bind=engine, autoflush=False, expire_on_commit=False
        )
        Base.metadata.create_all(engine)
        with testing_session() as db:
            project, page, candidate, _generate_job = _ready_candidate(db)
            # Success-side page state the inspect node's completion gate needs.
            page.selected_candidate_ack_version = page.storyboard_version
            page.continuity_status = "PASSED"
            graph = WorkflowGraph(
                nodes=[
                    WorkflowNodeDefinition(id="inspect", type="quality.inspect", name="质量检查")
                ]
            )
            workflow = WorkflowDefinition(
                project_id=project.id, name="收养成功推进", draft_graph=graph.model_dump(mode="json")
            )
            db.add(workflow)
            db.flush()
            version = WorkflowVersion(
                workflow_id=workflow.id,
                revision=1,
                graph=graph.model_dump(mode="json"),
                graph_checksum="adopt-success",
            )
            db.add(version)
            db.flush()
            run = WorkflowRun(
                workflow_id=workflow.id,
                workflow_version_id=version.id,
                project_id=project.id,
                scope_type="PAGE",
                scope_id=page.id,
                status="RUNNING",
            )
            db.add(run)
            db.flush()
            node_run = WorkflowNodeRun(
                workflow_run_id=run.id,
                node_id="inspect",
                node_type="quality.inspect",
                status="WAITING",
            )
            db.add(node_run)
            db.flush()
            route_job = GenerationJob(
                project_id=project.id,
                target_type="PAGE_CANDIDATE",
                target_id=candidate.id,
                job_type="PAGE_INSPECT",
                status=JobStatus.QUEUED,
            )
            db.add(route_job)
            db.flush()
            adopted = _create_inspection_job(
                db, run, graph, graph.nodes[0], node_run, [node_run]
            )
            assert adopted.id == route_job.id
            db.commit()
            job_id = route_job.id
            run_id = run.id
            node_run_id = node_run.id

        def fake_inspection(db, job):
            # Success effects of the real handler that the completion gate
            # reads: passing results per required category, INSPECTED candidate.
            inspected = db.get(PageCandidate, job.target_id)
            inspected_page = db.get(MangaPage, inspected.page_id)
            for category in REQUIRED_QUALITY_CATEGORIES:
                db.add(
                    InspectionResult(
                        candidate_id=inspected.id,
                        storyboard_version=inspected_page.storyboard_version,
                        category=category,
                        outcome="PASS",
                    )
                )
            inspected.status = "INSPECTED"
            db.commit()

        monkeypatch.setattr(worker_tasks, "SessionLocal", testing_session)
        monkeypatch.setattr(worker_tasks, "_run_inspection", fake_inspection)
        worker_tasks.execute_job(job_id)

        with testing_session() as db:
            finished_run = db.get(WorkflowRun, run_id)
            assert finished_run.status == "COMPLETED", finished_run.status
            assert db.get(WorkflowNodeRun, node_run_id).status == "COMPLETED"
        engine.dispose()


def test_reconcile_final_write_does_not_overwrite_concurrent_cancel(db_session, monkeypatch):
    """A cancel_run claim landing between reconcile's status refresh and the
    final run write owns the row: the final write must not flip the CANCELLED
    run to its recomputed state (later reconciles early-return on terminal, so
    the overwrite would be permanent)."""
    project, page, candidate, _generate_job = _ready_candidate(db_session)
    graph = WorkflowGraph(
        nodes=[
            WorkflowNodeDefinition(id="inspect", type="quality.inspect", name="质量检查")
        ]
    )
    workflow = WorkflowDefinition(
        project_id=project.id, name="取消不被覆盖", draft_graph=graph.model_dump(mode="json")
    )
    db_session.add(workflow)
    db_session.flush()
    version = WorkflowVersion(
        workflow_id=workflow.id,
        revision=1,
        graph=graph.model_dump(mode="json"),
        graph_checksum="cancel-claim",
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
    completed_job = GenerationJob(
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_INSPECT",
        status=JobStatus.COMPLETED,
        finished_at=utcnow(),
    )
    db_session.add(completed_job)
    db_session.flush()
    node_run = WorkflowNodeRun(
        workflow_run_id=run.id,
        node_id="inspect",
        node_type="quality.inspect",
        status="COMPLETED",
        job_id=completed_job.id,
        finished_at=utcnow(),
    )
    db_session.add(node_run)
    db_session.commit()
    run_id = run.id
    pre_cancel_version = run.version

    original_refresh = db_session.refresh
    cancelled = {"claimed": False}

    def refresh_then_cancel(target, *args, **kwargs):
        original_refresh(target, *args, **kwargs)
        if (
            not cancelled["claimed"]
            and isinstance(target, WorkflowRun)
            and target.id == run_id
        ):
            # Cancel lands in the window right after the refresh: same claim
            # cancel_run uses (terminal rows excluded, version bumped).
            cancelled["claimed"] = True
            canceller = sessionmaker(
                bind=db_session.get_bind(), autoflush=False, expire_on_commit=False
            )()
            try:
                canceller.execute(
                    update(WorkflowRun)
                    .where(WorkflowRun.id == run_id)
                    .values(
                        status="CANCELLED",
                        finished_at=utcnow(),
                        version=WorkflowRun.version + 1,
                    )
                )
                canceller.commit()
            finally:
                canceller.close()

    monkeypatch.setattr(db_session, "refresh", refresh_then_cancel)
    reconciled = reconcile_run(db_session, run_id)
    assert reconciled.status == "CANCELLED"
    assert reconciled.version == pre_cancel_version + 1


def test_reset_for_retry_does_not_clobber_live_worker_claim(db_session, monkeypatch):
    """A worker claim landing between the retry route's read and the reset must
    win: reset_for_retry must not clobber the lease or requeue the job."""
    project, _page, candidate, _generate_job = _ready_candidate(db_session)
    job = GenerationJob(
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_GENERATE",
        status=JobStatus.FAILED,
        error_code="WORKER_ERROR",
    )
    db_session.add(job)
    db_session.commit()

    # Simulate a worker claim landing after the route read the row.
    worker = sessionmaker(bind=db_session.get_bind(), autoflush=False, expire_on_commit=False)()
    try:
        worker.execute(
            update(GenerationJob)
            .where(GenerationJob.id == job.id)
            .values(
                status=JobStatus.GENERATING,
                lease_owner="worker-1",
                lease_expires_at=utcnow() + timedelta(minutes=5),
            )
        )
        worker.commit()
    finally:
        worker.close()

    enqueued: list[str] = []
    monkeypatch.setattr(
        job_service, "enqueue_job", lambda db, job: enqueued.append(job.id) or job
    )
    # The merged semantics surface a lost claim as a 409 to the caller
    # instead of silently returning the unchanged row.
    with pytest.raises(HTTPException) as excinfo:
        job_service.reset_for_retry(db_session, job)
    assert excinfo.value.status_code == 409
    db_session.expire_all()
    row = db_session.get(GenerationJob, job.id)
    assert row.status == JobStatus.GENERATING
    assert row.lease_owner == "worker-1"
    assert enqueued == []


def test_reset_for_retry_does_not_resurrect_cancelled_job(db_session, monkeypatch):
    """A job cancelled in the race window must stay cancelled: the retry reset
    must not flip it back to WAITING and clear cancelled_at."""
    project, _page, candidate, _generate_job = _ready_candidate(db_session)
    job = GenerationJob(
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_GENERATE",
        status=JobStatus.FAILED,
        error_code="WORKER_ERROR",
    )
    db_session.add(job)
    db_session.commit()

    cancelled_at = utcnow()
    worker = sessionmaker(bind=db_session.get_bind(), autoflush=False, expire_on_commit=False)()
    try:
        worker.execute(
            update(GenerationJob)
            .where(GenerationJob.id == job.id)
            .values(
                status=JobStatus.CANCELLED,
                cancelled_at=cancelled_at,
                finished_at=cancelled_at,
            )
        )
        worker.commit()
    finally:
        worker.close()

    enqueued: list[str] = []
    monkeypatch.setattr(
        job_service, "enqueue_job", lambda db, job: enqueued.append(job.id) or job
    )
    # Merged semantics: a lost claim raises 409 instead of silently no-oping.
    with pytest.raises(HTTPException) as excinfo:
        job_service.reset_for_retry(db_session, job)
    assert excinfo.value.status_code == 409
    db_session.expire_all()
    row = db_session.get(GenerationJob, job.id)
    assert row.status == JobStatus.CANCELLED
    assert row.cancelled_at is not None
    assert enqueued == []


def test_reset_for_retry_still_resets_job_and_revives_failed_workflow_run(
    db_session, monkeypatch
):
    """Normal path stays intact: FAILED job becomes WAITING and enqueues, and a
    FAILED workflow run/node pair linked to the job is revived to RUNNING."""
    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    project, page, candidate, _generate_job = _ready_candidate(db_session)
    graph = WorkflowGraph(
        nodes=[
            WorkflowNodeDefinition(id="inspect", type="quality.inspect", name="质量检查")
        ]
    )
    workflow = WorkflowDefinition(
        project_id=project.id, name="重试恢复", draft_graph=graph.model_dump(mode="json")
    )
    db_session.add(workflow)
    db_session.flush()
    version = WorkflowVersion(
        workflow_id=workflow.id,
        revision=1,
        graph=graph.model_dump(mode="json"),
        graph_checksum="retry-revive",
    )
    db_session.add(version)
    db_session.flush()
    run = WorkflowRun(
        workflow_id=workflow.id,
        workflow_version_id=version.id,
        project_id=project.id,
        scope_type="PAGE",
        scope_id=page.id,
        status="FAILED",
        finished_at=utcnow(),
    )
    db_session.add(run)
    db_session.flush()
    job = GenerationJob(
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_INSPECT",
        status=JobStatus.FAILED,
        error_code="WORKER_ERROR",
        error_message="质检失败",
        progress=40,
        request_parameters={"workflow_run_id": run.id},
    )
    db_session.add(job)
    db_session.flush()
    node_run = WorkflowNodeRun(
        workflow_run_id=run.id,
        node_id="inspect",
        node_type="quality.inspect",
        status="FAILED",
        error_code="WORKER_ERROR",
        error_message="质检失败",
        job_id=job.id,
    )
    db_session.add(node_run)
    db_session.commit()

    enqueued: list[str] = []
    real_enqueue = job_service.enqueue_job

    def recording_enqueue(db, job):
        enqueued.append(job.id)
        return real_enqueue(db, job)

    monkeypatch.setattr(job_service, "enqueue_job", recording_enqueue)
    job_service.reset_for_retry(db_session, job)
    db_session.expire_all()
    row = db_session.get(GenerationJob, job.id)
    assert row.status == JobStatus.WAITING
    assert row.progress == 0
    assert row.started_at is None
    assert enqueued == [job.id]
    revived_run = db_session.get(WorkflowRun, run.id)
    assert revived_run.status == "RUNNING"
    assert revived_run.finished_at is None
    revived_node = db_session.get(WorkflowNodeRun, node_run.id)
    assert revived_node.status == "RUNNING"
    assert revived_node.error_code is None


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
