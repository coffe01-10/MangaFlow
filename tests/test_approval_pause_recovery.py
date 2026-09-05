"""Job recovery must never schedule an unscheduled workflow node.

P1 (approval gate): the default graph's ``complete`` (output.page) node gets
its planning job with ZERO dependency rows — its only upstream node
(quality.inspect) receives no planning job — so recover_pending_jobs phase 2
saw a WAITING job with vacuously-complete dependencies and enqueued it
project-wide, every recovery pass, while the run was paused at the
generate/adopt approval barriers. The output.page handler then failed with
PAGE_NOT_PRODUCTION_READY, the retries burned, and reconcile flipped the
PAUSED run to FAILED mid-approval, destroying the human gate.

The rule: recovery re-runs interrupted WORK (the owning node_run is RUNNING —
reconcile sets it RUNNING immediately before enqueueing); scheduling NEW work
(node_run still WAITING) belongs to reconcile_run alone. Jobs without a
workflow node linkage keep the legacy behavior untouched.
"""

from sqlalchemy import select

from app.config import Settings
from app.domain.states import JobStatus
from app.models import (
    AppSetting,
    GenerationJob,
    Project,
    WorkflowDefinition,
    WorkflowNodeRun,
    WorkflowRun,
    WorkflowVersion,
)
from app.services import job_service
from app.services.workflow_engine import default_graph


def _set_queue_mode(db_session, mode: str) -> None:
    db_session.add(AppSetting(key="runtime", value={"queue_mode": mode}, version=1))
    db_session.commit()


def _workflow_run(db, project: Project, name: str, *, status: str) -> WorkflowRun:
    workflow = WorkflowDefinition(project_id=project.id, name=name, draft_graph=default_graph())
    db.add(workflow)
    db.flush()
    version = WorkflowVersion(
        workflow_id=workflow.id,
        revision=1,
        graph=default_graph(),
        graph_checksum=f"{name}"[:1] * 64,
        validation_report={"valid": True},
    )
    db.add(version)
    db.flush()
    run = WorkflowRun(
        workflow_id=workflow.id,
        workflow_version_id=version.id,
        project_id=project.id,
        scope_type="PAGE",
        scope_id=f"scope-{name}",
        status=status,
    )
    db.add(run)
    db.flush()
    return run


def _output_page_job(
    db,
    project: Project,
    run: WorkflowRun,
    node_run: WorkflowNodeRun,
    *,
    status: JobStatus,
) -> GenerationJob:
    """The planning-shaped output.page row: WAITING with no dependency rows."""

    job = GenerationJob(
        project_id=project.id,
        target_type="WORKFLOW_NODE",
        target_id=node_run.id,
        job_type="WORKFLOW_NODE",
        status=status,
        error_code="LOCAL_WORKER" if status == JobStatus.QUEUED else None,
        error_message=(
            "本地后台执行器正在处理任务" if status == JobStatus.QUEUED else None
        ),
        request_parameters={
            "workflow_run_id": run.id,
            "workflow_node_run_id": node_run.id,
            "node_id": "complete",
            "node_type": "output.page",
            "config": {"notes": ""},
        },
        idempotency_key=f"workflow:{run.id}:complete:1",
    )
    db.add(job)
    db.flush()
    node_run.job_id = job.id
    db.flush()
    return job


def _paused_run_with_unscheduled_output_node(db, project: Project, name: str):
    """A run paused at the approval barriers, its output.job not yet scheduled.

    Graph-faithful: planning seeds a node_run for EVERY graph node, and a run
    is PAUSED exactly while the generate/adopt barriers sit in
    WAITING_APPROVAL. reconcile's parent gate ignores parents that have no
    node_run row, so a faithful seed is what keeps the scheduling decision
    honest.
    """

    run = _workflow_run(db, project, name, status="PAUSED")
    db.add_all(
        [
            WorkflowNodeRun(
                workflow_run_id=run.id,
                node_id="generate",
                node_type="generator.page",
                status="WAITING_APPROVAL",
            ),
            WorkflowNodeRun(
                workflow_run_id=run.id,
                node_id="adopt",
                node_type="control.approval",
                status="WAITING_APPROVAL",
            ),
            WorkflowNodeRun(
                workflow_run_id=run.id,
                node_id="inspect",
                node_type="quality.inspect",
                status="WAITING",
            ),
            WorkflowNodeRun(
                workflow_run_id=run.id,
                node_id="complete",
                node_type="output.page",
                status="WAITING",
            ),
        ]
    )
    db.flush()
    node_run = db.scalar(
        select(WorkflowNodeRun).where(
            WorkflowNodeRun.workflow_run_id == run.id,
            WorkflowNodeRun.node_id == "complete",
        )
    )
    return run, node_run


def test_recovery_does_not_execute_output_page_of_paused_run(
    db_session, monkeypatch
):
    """T1: a PAUSED run's dependency-less output.page job must stay untouched.

    Pre-fix the vacuous-dependency WAITING row was enqueued on every recovery
    pass, the handler raised PAGE_NOT_PRODUCTION_READY, retries burned, and
    reconcile flipped the run FAILED mid-approval.
    """

    _set_queue_mode(db_session, "LOCAL")
    project = Project(name="审批暂停恢复")
    db_session.add(project)
    db_session.flush()
    run, node_run = _paused_run_with_unscheduled_output_node(db_session, project, "暂停运行")
    job = _output_page_job(
        db_session, project, run, node_run, status=JobStatus.WAITING
    )
    db_session.commit()
    job_id, run_id, node_run_id = job.id, run.id, node_run.id

    monkeypatch.setattr(
        job_service, "get_settings", lambda: Settings(environment="development")
    )
    submitted: list[str] = []
    monkeypatch.setattr(job_service, "_submit_local", lambda job_id: submitted.append(job_id))

    recovered = job_service.recover_pending_jobs(db_session)

    assert submitted == [], "recovery must not execute a paused run's output job"
    assert recovered == 0
    db_session.expire_all()
    reloaded = db_session.get(GenerationJob, job_id)
    assert reloaded.status == JobStatus.WAITING
    assert db_session.get(WorkflowRun, run_id).status == "PAUSED"
    assert db_session.get(WorkflowNodeRun, node_run_id).status == "WAITING"


def test_recovery_still_reruns_interrupted_workflow_work(db_session, monkeypatch):
    """T2: a workflow job whose node_run is RUNNING is legitimate recovery work.

    A retryable failure resets the job to WAITING while its node stays
    RUNNING; if the executor then dies before the retry fires, recovery is
    what re-runs the interrupted work. That path must stay intact.
    """

    _set_queue_mode(db_session, "LOCAL")
    project = Project(name="中断工作恢复")
    db_session.add(project)
    db_session.flush()
    run = _workflow_run(db_session, project, "中断运行", status="RUNNING")
    node_run = WorkflowNodeRun(
        workflow_run_id=run.id,
        node_id="parse",
        node_type="agent.parse",
        status="RUNNING",
        started_at=run.started_at,
    )
    db_session.add(node_run)
    db_session.flush()
    job = GenerationJob(
        project_id=project.id,
        target_type="WORKFLOW_NODE",
        target_id=node_run.id,
        job_type="SOURCE_PARSE",
        status=JobStatus.WAITING,
        error_code="UPSTREAM_TIMEOUT",
        request_parameters={
            "workflow_run_id": run.id,
            "workflow_node_run_id": node_run.id,
            "node_id": "parse",
            "node_type": "agent.parse",
        },
    )
    db_session.add(job)
    db_session.commit()
    job_id = job.id

    monkeypatch.setattr(
        job_service, "get_settings", lambda: Settings(environment="development")
    )
    submitted: list[str] = []
    monkeypatch.setattr(job_service, "_submit_local", lambda job_id: submitted.append(job_id))

    recovered = job_service.recover_pending_jobs(db_session)

    assert submitted == [job_id], "interrupted work (node RUNNING) must be re-enqueued"
    assert recovered == 1
    db_session.expire_all()
    requeued = db_session.get(GenerationJob, job_id)
    assert requeued.status == JobStatus.QUEUED
    assert requeued.error_code == "LOCAL_WORKER"


def test_recovery_still_reenqueues_plain_non_workflow_jobs(db_session, monkeypatch):
    """T3: jobs without workflow linkage keep the legacy requeue behavior."""

    _set_queue_mode(db_session, "LOCAL")
    project = Project(name="普通任务恢复")
    db_session.add(project)
    db_session.flush()
    job = GenerationJob(
        project_id=project.id,
        target_type="CHAPTER",
        target_id="plain-target",
        job_type="SOURCE_PARSE",
        status=JobStatus.WAITING,
    )
    db_session.add(job)
    db_session.commit()
    job_id = job.id

    monkeypatch.setattr(
        job_service, "get_settings", lambda: Settings(environment="development")
    )
    submitted: list[str] = []
    monkeypatch.setattr(job_service, "_submit_local", lambda job_id: submitted.append(job_id))

    recovered = job_service.recover_pending_jobs(db_session)

    assert submitted == [job_id]
    assert recovered == 1
    db_session.expire_all()
    assert db_session.get(GenerationJob, job_id).status == JobStatus.QUEUED


def test_recovery_skips_queued_local_worker_output_page_of_paused_run(
    db_session, monkeypatch
):
    """T4: the QUEUED+LOCAL_WORKER re-adoption path is gated the same way.

    A row that a previous process already queued locally must not be
    re-adopted into execution while its node is unscheduled and its run is
    paused at approval.
    """

    _set_queue_mode(db_session, "LOCAL")
    project = Project(name="本地排队暂停恢复")
    db_session.add(project)
    db_session.flush()
    run, node_run = _paused_run_with_unscheduled_output_node(
        db_session, project, "本地排队暂停"
    )
    job = _output_page_job(
        db_session, project, run, node_run, status=JobStatus.QUEUED
    )
    db_session.commit()
    job_id, run_id = job.id, run.id

    monkeypatch.setattr(
        job_service, "get_settings", lambda: Settings(environment="development")
    )
    submitted: list[str] = []
    monkeypatch.setattr(job_service, "_submit_local", lambda job_id: submitted.append(job_id))

    recovered = job_service.recover_pending_jobs(db_session)

    assert submitted == [], "the LOCAL_WORKER re-adoption path must also be gated"
    assert recovered == 0
    db_session.expire_all()
    reloaded = db_session.get(GenerationJob, job_id)
    assert reloaded.status == JobStatus.QUEUED
    assert reloaded.error_code == "LOCAL_WORKER"
    assert db_session.get(WorkflowRun, run_id).status == "PAUSED"


def test_recovery_self_heals_crash_stranded_child_via_reconcile(
    db_session, monkeypatch
):
    """T5: a WAITING-node skip must still reconcile the run, not drop it.

    Crash-window shape: the parent job committed COMPLETED, but the worker
    died before reconcile_run could schedule the child (child node_run still
    WAITING). Recovery must not run the child directly, and it must not skip
    it either — it must hand the run to reconcile_run, which schedules every
    legally unlocked node and leaves gated ones alone. Without the reconcile
    trigger the run stalls forever (no other component reconciles it).
    """

    from app.models import JobDependency, WorkflowNodeRun as WNR

    _set_queue_mode(db_session, "LOCAL")
    project = Project(name="崩溃窗口自愈")
    db_session.add(project)
    db_session.flush()
    run = _workflow_run(db_session, project, "崩溃运行", status="RUNNING")
    parent_node = WNR(
        workflow_run_id=run.id,
        node_id="parse",
        node_type="agent.parse",
        # SKIPPED passes reconcile's parent gate while skipping the
        # completed-output validation that expects real output_refs.
        status="SKIPPED",
    )
    child_node = WNR(
        workflow_run_id=run.id,
        node_id="adapt",
        node_type="agent.adapt",
        status="WAITING",
    )
    db_session.add_all([parent_node, child_node])
    db_session.flush()
    parent_job = GenerationJob(
        project_id=project.id,
        target_type="CHAPTER",
        target_id="chapter-1",
        job_type="SOURCE_PARSE",
        status=JobStatus.COMPLETED,
        request_parameters={"workflow_run_id": run.id},
        idempotency_key=f"workflow:{run.id}:parse:1",
    )
    child_job = GenerationJob(
        project_id=project.id,
        target_type="WORKFLOW_NODE",
        target_id=child_node.id,
        job_type="SOURCE_REWRITE",
        status=JobStatus.WAITING,
        request_parameters={
            "workflow_run_id": run.id,
            "workflow_node_run_id": child_node.id,
            "node_id": "adapt",
            "node_type": "agent.adapt",
        },
        idempotency_key=f"workflow:{run.id}:adapt:1",
    )
    db_session.add_all([parent_job, child_job])
    db_session.flush()
    parent_node.job_id = parent_job.id
    child_node.job_id = child_job.id
    db_session.add(JobDependency(job_id=child_job.id, depends_on_job_id=parent_job.id))
    db_session.commit()
    child_job_id, child_node_id = child_job.id, child_node.id

    monkeypatch.setattr(
        job_service, "get_settings", lambda: Settings(environment="development")
    )
    submitted: list[str] = []
    monkeypatch.setattr(job_service, "_submit_local", lambda job_id: submitted.append(job_id))

    job_service.recover_pending_jobs(db_session)

    assert submitted == [child_job_id], (
        "recovery must reconcile the run so the crash-stranded child is scheduled"
    )
    db_session.expire_all()
    assert db_session.get(GenerationJob, child_job_id).status == JobStatus.QUEUED
    child = db_session.get(WNR, child_node_id)
    assert child.status == "RUNNING", "reconcile must mark the scheduled node RUNNING"
