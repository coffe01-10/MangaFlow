"""Regression guards: a terminal-FAILED run must not strand downstream state.

When reconcile_run claims the run FAILED, dependent child node_runs and their
dependency-blocked WAITING jobs used to stay non-terminal forever: the script
delete guard kept 409ing on ACTIVE_JOB_STATUSES, and the jobs list showed an
unlabeled active row. The FAILED claim path must sweep the run's own children
(mirroring cancel_run) inside the same transaction, and the job retry route
must refuse to revive dependency-blocked WAITING jobs (reset_for_retry would
resurrect a FAILED run to phantom RUNNING).
"""

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.domain.states import JobStatus
from app.models import (
    GenerationJob,
    JobDependency,
    Project,
    WorkflowDefinition,
    WorkflowNodeRun,
    WorkflowRun,
    WorkflowVersion,
    utcnow,
)
from app.services.job_service import ACTIVE_JOB_STATUSES
from app.services.workflow_engine.catalog import _edge, _node
from app.services.workflow_engine.reconciliation import reconcile_run


def _seed_graph(db):
    """assets → page; adapt → page: a failed adapt leaves page dep-blocked."""
    return {
        "nodes": [
            _node("adapt", "agent.adapt", "漫画改编", 0, 0, model_alias="auto"),
            _node("assets", "source.assets", "参考资产", 0, 300, notes="人物、服装、风格"),
            _node(
                "page",
                "generator.page",
                "单页生成",
                300,
                0,
                model_alias=None,
                resolution="1K",
                requires_approval=True,
            ),
        ],
        "edges": [
            _edge("adapt", "script", "page", "panels"),
            _edge("assets", "assets", "page", "assets"),
        ],
    }


def _seed_run(db, *, status="RUNNING"):
    project = Project(name="失败清扫项目")
    db.add(project)
    db.flush()
    graph = _seed_graph(db)
    workflow = WorkflowDefinition(project_id=project.id, name="失败清扫工作流", draft_graph=graph)
    db.add(workflow)
    db.flush()
    version = WorkflowVersion(
        workflow_id=workflow.id, revision=1, graph=graph, graph_checksum="failure-sweep"
    )
    db.add(version)
    db.flush()
    run = WorkflowRun(
        workflow_id=workflow.id,
        workflow_version_id=version.id,
        project_id=project.id,
        scope_type="PROJECT",
        status=status,
        started_at=utcnow(),
        finished_at=utcnow() if status in {"FAILED", "CANCELLED"} else None,
    )
    db.add(run)
    db.flush()
    return project, run


def _seed_node_job(
    db,
    project,
    run,
    node_id,
    node_type,
    *,
    job_status,
    node_status,
    depends_on=None,
    error_code=None,
):
    node_run = WorkflowNodeRun(
        workflow_run_id=run.id,
        node_id=node_id,
        node_type=node_type,
        status=node_status,
    )
    db.add(node_run)
    db.flush()
    job = GenerationJob(
        project_id=project.id,
        target_type="WORKFLOW_NODE",
        target_id=node_run.id,
        job_type="WORKFLOW_NODE",
        status=job_status,
        request_parameters={"workflow_run_id": run.id, "node_id": node_id, "node_type": node_type},
        error_code=error_code,
    )
    db.add(job)
    db.flush()
    node_run.job_id = job.id
    if depends_on:
        db.add(JobDependency(job_id=job.id, depends_on_job_id=depends_on.id))
    db.flush()
    return node_run, job


def _seed_failed_chain(db):
    """adapt FAILED (node FAILED) → page WAITING (node WAITING) + COMPLETED sibling."""
    project, run = _seed_run(db)
    sibling_node_run, sibling_job = _seed_node_job(
        db,
        project,
        run,
        "assets",
        "source.assets",
        job_status=JobStatus.COMPLETED,
        node_status="COMPLETED",
    )
    sibling_job.finished_at = utcnow()
    failed_node_run, failed_job = _seed_node_job(
        db,
        project,
        run,
        "adapt",
        "agent.adapt",
        job_status=JobStatus.FAILED,
        node_status="FAILED",
        error_code="WORKER_ERROR",
    )
    failed_job.finished_at = utcnow()
    waiting_node_run, waiting_job = _seed_node_job(
        db,
        project,
        run,
        "page",
        "generator.page",
        job_status=JobStatus.WAITING,
        node_status="WAITING",
        depends_on=failed_job,
    )
    db.commit()
    return project, run, failed_node_run, failed_job, waiting_node_run, waiting_job


def test_failed_run_claim_cancels_dependency_blocked_children(db_session):
    """T1: reconcile claiming the run FAILED must terminalize the stranded
    WAITING child node_run and its dependency-blocked job, and leave the
    COMPLETED sibling and the causal FAILED job untouched."""
    (
        _project,
        run,
        failed_node_run,
        failed_job,
        waiting_node_run,
        waiting_job,
    ) = _seed_failed_chain(db_session)

    reconciled = reconcile_run(db_session, run.id)

    assert reconciled.status == "FAILED"
    assert reconciled.finished_at is not None
    # Stranded child swept out of the active set.
    db_session.refresh(waiting_node_run)
    db_session.refresh(waiting_job)
    assert waiting_node_run.status == "CANCELLED"
    assert waiting_node_run.finished_at is not None
    assert waiting_job.status == JobStatus.CANCELLED
    assert waiting_job.cancelled_at is not None
    # The causal failure stays diagnosable.
    db_session.refresh(failed_node_run)
    db_session.refresh(failed_job)
    assert failed_node_run.status == "FAILED"
    assert failed_job.status == JobStatus.FAILED
    # COMPLETED sibling is untouched.
    sibling_node_run = db_session.scalar(
        select(WorkflowNodeRun).where(
            WorkflowNodeRun.workflow_run_id == run.id, WorkflowNodeRun.node_id == "assets"
        )
    )
    sibling_job = db_session.get(GenerationJob, sibling_node_run.job_id)
    db_session.refresh(sibling_node_run)
    db_session.refresh(sibling_job)
    assert sibling_node_run.status == "COMPLETED"
    assert sibling_job.status == JobStatus.COMPLETED


def test_failed_run_claim_cancels_late_jobs_scoped_to_the_run(db_session):
    """T2: jobs without node_run linkage but carrying the run's
    workflow_run_id parameter must be swept; other runs' jobs must not."""
    project, run, _failed_node, _failed_job, _waiting_node, waiting_job = _seed_failed_chain(
        db_session
    )
    late_job = GenerationJob(
        project_id=project.id,
        target_type="WORKFLOW_NODE",
        target_id=project.id,
        job_type="PAGE_INSPECT",
        status=JobStatus.QUEUED,
        request_parameters={"workflow_run_id": run.id, "node_id": "inspect"},
    )
    db_session.add(late_job)
    other_project = Project(name="其他项目")
    db_session.add(other_project)
    db_session.flush()
    other_run = WorkflowRun(
        workflow_id=run.workflow_id,
        workflow_version_id=run.workflow_version_id,
        project_id=other_project.id,
        scope_type="PROJECT",
        status="RUNNING",
    )
    db_session.add(other_run)
    db_session.flush()
    other_job = GenerationJob(
        project_id=other_project.id,
        target_type="WORKFLOW_NODE",
        target_id=other_project.id,
        job_type="WORKFLOW_NODE",
        status=JobStatus.WAITING,
        request_parameters={"workflow_run_id": other_run.id},
    )
    db_session.add(other_job)
    db_session.commit()

    reconciled = reconcile_run(db_session, run.id)

    assert reconciled.status == "FAILED"
    db_session.refresh(late_job)
    assert late_job.status == JobStatus.CANCELLED
    # Strictly scoped: another run's WAITING job is not touched.
    db_session.refresh(other_job)
    assert other_job.status == JobStatus.WAITING
    db_session.refresh(waiting_job)
    assert waiting_job.status == JobStatus.CANCELLED


def test_lost_failed_claim_rolls_back_the_sweep(db_session, monkeypatch):
    """T3: if a concurrent canceller owns the terminal claim between the last
    refresh and the FAILED write, the claim misses and the sweep must not
    persist — the run stays CANCELLED and the child job stays WAITING."""
    (
        _project,
        run,
        _failed_node,
        _failed_job,
        waiting_node_run,
        waiting_job,
    ) = _seed_failed_chain(db_session)
    run_id = run.id

    original_refresh = db_session.refresh
    cancelled = {"claimed": False}

    def refresh_then_cancel(target, *args, **kwargs):
        original_refresh(target, *args, **kwargs)
        if (
            not cancelled["claimed"]
            and isinstance(target, WorkflowRun)
            and target.id == run_id
        ):
            # Cancel lands right after reconcile's refresh: the same claim
            # cancel_run uses (terminal rows excluded, version bumped).
            cancelled["claimed"] = True
            canceller = sessionmaker(
                bind=db_session.get_bind(), autoflush=False, expire_on_commit=False
            )()
            try:
                canceller.execute(
                    WorkflowRun.__table__.update()
                    .where(WorkflowRun.__table__.c.id == run_id)
                    .values(status="CANCELLED", finished_at=utcnow())
                )
                canceller.commit()
            finally:
                canceller.close()

    monkeypatch.setattr(db_session, "refresh", refresh_then_cancel)
    reconciled = reconcile_run(db_session, run_id)

    assert cancelled["claimed"] is True
    assert reconciled.status == "CANCELLED"
    db_session.refresh(waiting_node_run)
    db_session.refresh(waiting_job)
    assert waiting_node_run.status == "WAITING"
    assert waiting_job.status == JobStatus.WAITING


def test_retry_route_refuses_dependency_blocked_waiting_job(client, db_session):
    """T4a: retrying a WAITING child whose dependency is not COMPLETED must
    409 — reset_for_retry would otherwise revive the FAILED run to phantom
    RUNNING before enqueue_job's dependency gate refuses."""
    _project, run, _failed_node, failed_job, _waiting_node, waiting_job = _seed_failed_chain(
        db_session
    )
    db_session.refresh(run)
    run.status = "FAILED"
    run.finished_at = utcnow()
    db_session.commit()

    response = client.post(f"/api/v1/jobs/{waiting_job.id}/retry")

    assert response.status_code == 409
    assert "依赖" in response.json()["detail"]
    db_session.refresh(run)
    assert run.status == "FAILED"
    db_session.refresh(waiting_job)
    assert waiting_job.status == JobStatus.WAITING


def test_retry_route_allows_failed_job_with_completed_dependencies(
    client, db_session, monkeypatch
):
    """T4b: a FAILED job whose dependencies COMPLETED stays retryable."""
    project, run = _seed_run(db_session)
    parent_node_run, parent_job = _seed_node_job(
        db_session,
        project,
        run,
        "adapt",
        "agent.adapt",
        job_status=JobStatus.COMPLETED,
        node_status="COMPLETED",
    )
    parent_job.finished_at = utcnow()
    child_node_run, child_job = _seed_node_job(
        db_session,
        project,
        run,
        "page",
        "generator.page",
        job_status=JobStatus.FAILED,
        node_status="FAILED",
        depends_on=parent_job,
        error_code="WORKER_ERROR",
    )
    child_job.finished_at = utcnow()
    run.status = "FAILED"
    run.finished_at = utcnow()
    db_session.commit()

    enqueued: list[str] = []
    import app.services.job_service as job_service

    monkeypatch.setattr(
        job_service, "enqueue_job", lambda db, job: enqueued.append(job.id) or job
    )

    response = client.post(f"/api/v1/jobs/{child_job.id}/retry")

    assert response.status_code == 200
    db_session.refresh(child_job)
    assert child_job.status == JobStatus.WAITING
    assert enqueued == [child_job.id]
    # The run revival on a legitimate retry is preserved.
    db_session.refresh(run)
    assert run.status == "RUNNING"
    db_session.refresh(child_node_run)
    assert child_node_run.status == "RUNNING"


def test_failed_run_sweep_unblocks_project_active_job_guard(db_session):
    """T5: after the sweep, the project-scoped ACTIVE_JOB_STATUSES check used
    by the script delete guard no longer sees any job for the project."""
    project, run, *_rest = _seed_failed_chain(db_session)
    late_job = GenerationJob(
        project_id=project.id,
        target_type="WORKFLOW_NODE",
        target_id=project.id,
        job_type="PAGE_INSPECT",
        status=JobStatus.QUEUED,
        request_parameters={"workflow_run_id": run.id, "node_id": "inspect"},
    )
    db_session.add(late_job)
    db_session.commit()

    before = len(
        list(
            db_session.scalars(
                select(GenerationJob.id).where(
                    GenerationJob.project_id == project.id,
                    GenerationJob.status.in_(ACTIVE_JOB_STATUSES),
                )
            )
        )
    )
    assert before > 0

    reconcile_run(db_session, run.id)

    after = list(
        db_session.scalars(
            select(GenerationJob.id).where(
                GenerationJob.project_id == project.id,
                GenerationJob.status.in_(ACTIVE_JOB_STATUSES),
            )
        )
    )
    assert after == []
