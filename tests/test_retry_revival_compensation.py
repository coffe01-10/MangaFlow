"""Post-commit compensation and arbitration for reset_for_retry revivals.

The R2 pre-commit revival re-verify is a dead fence in production: the initial
CAS holds the job row's write lock until commit, so across two real
connections the guard can only ever see its own uncommitted WAITING row (the
StaticPool suite makes it look effective because every session shares one
connection). The races that survive all land AFTER the retry commits, so the
repairs live there:

- (i) cancel-side escalation: a cancel_job whose run read said FAILED claims
  the freshly committed WAITING row once the retry's lock releases; the
  post-claim re-read must escalate to cancel_run so the revived run ends
  CANCELLED instead of a zombie RUNNING run behind a CANCELLED mid-chain node.
- (ii) retry-side compensation: a bare post-commit CANCELLED flip (any writer
  that cancels the job row without terminalizing the run) is compensated back
  to the exact pre-retry FAILED state — job, run, own node, revived tail.
- (iii) sibling arbitration: two concurrent retries of sibling FAILED mutex
  jobs can both pass the pre-CAS has_active_job guard (each revival is
  invisible until it commits). Oldest-wins arbitration — mirroring the R1
  story_parse guard — lets exactly one committed revival survive; a genuinely
  pre-existing sibling keeps being rejected by the pre-CAS guard alone.

The queue stays disabled throughout — no provider call can run.
"""

from datetime import UTC, datetime, timedelta
import logging
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.domain.states import JobStatus
from app.models import (
    GenerationJob,
    Project,
    WorkflowDefinition,
    WorkflowNodeRun,
    WorkflowRun,
    WorkflowVersion,
    utcnow,
)
from app.services import job_service
from app.services.workflow_engine.catalog import graph_checksum

RUN_FINISHED = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
NODE_FINISHED = datetime(2026, 1, 1, 12, 1, 0, tzinfo=UTC)
JOB_FINISHED = datetime(2026, 1, 1, 12, 2, 0, tzinfo=UTC)
TAIL_CANCELLED = datetime(2026, 1, 1, 12, 3, 0, tzinfo=UTC)


def _session_factory(db_session):
    return sessionmaker(bind=db_session.get_bind(), autoflush=False, expire_on_commit=False)


def _seed_failed_workflow(db, name: str, *, target: str, created_at: datetime) -> SimpleNamespace:
    """A FAILED workflow run whose inspect node/job failed mid-chain and whose
    output tail was swept to CANCELLED — the exact shape reset_for_retry
    revives (run RUNNING, node RUNNING, tail WAITING)."""

    project = Project(name=name)
    db.add(project)
    db.flush()
    graph = {"schema_version": 2, "nodes": [], "edges": []}
    workflow = WorkflowDefinition(project_id=project.id, name=name + "流程", draft_graph=graph)
    db.add(workflow)
    db.flush()
    version = WorkflowVersion(
        workflow_id=workflow.id,
        revision=1,
        graph=graph,
        graph_checksum=graph_checksum(graph),
        validation_report={"valid": True},
    )
    db.add(version)
    db.flush()
    run = WorkflowRun(
        workflow_id=workflow.id,
        workflow_version_id=version.id,
        project_id=project.id,
        scope_type="PAGE",
        scope_id=f"scope-{target}",
        status="FAILED",
        started_at=RUN_FINISHED - timedelta(minutes=10),
        finished_at=RUN_FINISHED,
    )
    db.add(run)
    db.flush()
    job = GenerationJob(
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=target,
        job_type="PAGE_INSPECT",
        status=JobStatus.FAILED,
        error_code="UPSTREAM",
        error_message="上游失败",
        progress=40,
        created_at=created_at,
        started_at=RUN_FINISHED,
        finished_at=JOB_FINISHED,
        request_parameters={"workflow_run_id": run.id},
    )
    db.add(job)
    db.flush()
    node = WorkflowNodeRun(
        workflow_run_id=run.id,
        node_id="inspect",
        node_type="quality.inspect",
        status="FAILED",
        error_code="UPSTREAM",
        error_message="上游失败",
        finished_at=NODE_FINISHED,
        job_id=job.id,
    )
    db.add(node)
    tail_job = GenerationJob(
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=f"{target}-tail",
        job_type="PAGE_GENERATE",
        status=JobStatus.CANCELLED,
        created_at=created_at + timedelta(seconds=1),
        cancelled_at=TAIL_CANCELLED,
        finished_at=TAIL_CANCELLED,
        request_parameters={"workflow_run_id": run.id},
    )
    db.add(tail_job)
    db.flush()
    tail_node = WorkflowNodeRun(
        workflow_run_id=run.id,
        node_id="output",
        node_type="output.page",
        status="CANCELLED",
        finished_at=TAIL_CANCELLED,
        job_id=tail_job.id,
    )
    db.add(tail_node)
    db.commit()
    db.expire_all()
    # Round-tripped values: SQLite stores datetimes as strings, so equality
    # assertions must compare against what actually reads back, not the
    # in-memory seed constants.
    return SimpleNamespace(
        project_id=project.id,
        run_id=run.id,
        node_id=node.id,
        job_id=job.id,
        tail_node_id=tail_node.id,
        tail_job_id=tail_job.id,
        run_finished_at=db.get(WorkflowRun, run.id).finished_at,
        node_finished_at=db.get(WorkflowNodeRun, node.id).finished_at,
        job_finished_at=db.get(GenerationJob, job.id).finished_at,
        tail_cancelled_at=db.get(GenerationJob, tail_job.id).cancelled_at,
        tail_node_finished_at=db.get(WorkflowNodeRun, tail_node.id).finished_at,
    )


def _commit_sibling_revival(factory, job_id: str) -> None:
    """Commit the sibling's revival exactly as its own reset_for_retry CAS
    would have written it.

    Driving the real function is impossible under the suite's StaticPool: the
    sibling session shares this test's connection, so its pre-CAS
    has_active_job guard would see OUR session's uncommitted WAITING row and
    409 — the single-connection visibility that also fakes the R2 pre-commit
    interleave. In production each guard is blind to the other's uncommitted
    row, which is precisely the both-pass window this test pins.
    """

    with factory() as other:
        other.execute(
            update(GenerationJob)
            .where(GenerationJob.id == job_id)
            .values(
                status=JobStatus.WAITING,
                error_code=None,
                error_message=None,
                progress=0,
                started_at=None,
                finished_at=None,
                cancelled_at=None,
                lease_owner=None,
                lease_expires_at=None,
                scheduled_at=utcnow() + timedelta(seconds=1),
            )
            .execution_options(synchronize_session=False)
        )
        other.commit()


def test_cancel_after_retry_commit_escalates_to_cancel_run(db_session, monkeypatch):
    """(i) The production interleave the dead R2 fence cannot catch: the retry
    COMMITS its revival (run RUNNING, tail WAITING) between cancel_job's
    stale FAILED run read and its mark_job_cancelled claim. The post-claim
    re-read must escalate to cancel_run so the revived run ends CANCELLED —
    not a zombie RUNNING run permanently locking the scope."""

    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    ids = _seed_failed_workflow(
        db_session, "取消升级", target="candidate-escalate", created_at=RUN_FINISHED
    )
    factory = _session_factory(db_session)
    real_mark = job_service.mark_job_cancelled
    triggered = {"done": False}

    def _retry_commits_then_cancel(db, target):
        if not triggered["done"] and target.id == ids.job_id:
            triggered["done"] = True
            # The retry's whole transaction lands between our FAILED run read
            # and our claim (which blocks on its row lock until it commits).
            with factory() as other:
                job_service.reset_for_retry(other, other.get(GenerationJob, ids.job_id))
        return real_mark(db, target)

    monkeypatch.setattr(job_service, "mark_job_cancelled", _retry_commits_then_cancel)

    job_service.cancel_job(db_session, db_session.get(GenerationJob, ids.job_id))

    db_session.expire_all()
    run = db_session.get(WorkflowRun, ids.run_id)
    assert run.status == "CANCELLED"
    assert run.finished_at is not None
    assert db_session.get(GenerationJob, ids.job_id).status == JobStatus.CANCELLED
    assert db_session.get(WorkflowNodeRun, ids.node_id).status == "CANCELLED"
    assert db_session.get(WorkflowNodeRun, ids.tail_node_id).status == "CANCELLED"
    assert db_session.get(GenerationJob, ids.tail_job_id).status == JobStatus.CANCELLED


def test_post_commit_cancel_flip_is_compensated_to_failed(db_session, monkeypatch):
    """(ii) A CANCELLED flip landing right after the retry's commit — from a
    writer with no run-side escalation — must be compensated: the pre-retry
    FAILED shape (job status/error fields, run status/finished_at, own node,
    revived tail nodes and their jobs) is restored exactly, and the retry
    caller sees the stale-claim 409."""

    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    ids = _seed_failed_workflow(
        db_session, "提交后翻转", target="candidate-flip", created_at=RUN_FINISHED
    )
    factory = _session_factory(db_session)
    real_guard = job_service._verify_retry_revival_post_commit
    flipped = {"done": False}

    def _flip_to_cancelled_then_guard(db, job, snapshot):
        if not flipped["done"]:
            flipped["done"] = True
            with factory() as other:
                other.execute(
                    update(GenerationJob)
                    .where(GenerationJob.id == ids.job_id)
                    .values(status=JobStatus.CANCELLED, cancelled_at=utcnow())
                    .execution_options(synchronize_session=False)
                )
                other.commit()
        return real_guard(db, job, snapshot)

    monkeypatch.setattr(
        job_service, "_verify_retry_revival_post_commit", _flip_to_cancelled_then_guard
    )

    with pytest.raises(HTTPException) as exc_info:
        job_service.reset_for_retry(db_session, db_session.get(GenerationJob, ids.job_id))

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "任务状态已变化，请刷新后重试"
    db_session.expire_all()
    # The whole revival is rolled back to the exact pre-retry shape.
    run = db_session.get(WorkflowRun, ids.run_id)
    assert run.status == "FAILED"
    assert run.finished_at == ids.run_finished_at
    node = db_session.get(WorkflowNodeRun, ids.node_id)
    assert node.status == "FAILED"
    assert node.finished_at == ids.node_finished_at
    assert node.error_code == "UPSTREAM"
    tail_node = db_session.get(WorkflowNodeRun, ids.tail_node_id)
    assert tail_node.status == "CANCELLED"
    assert tail_node.finished_at == ids.tail_node_finished_at
    tail_job = db_session.get(GenerationJob, ids.tail_job_id)
    assert tail_job.status == JobStatus.CANCELLED
    assert tail_job.cancelled_at == ids.tail_cancelled_at
    assert tail_job.finished_at == ids.tail_cancelled_at
    job = db_session.get(GenerationJob, ids.job_id)
    assert job.status == JobStatus.FAILED
    assert job.error_code == "UPSTREAM"
    assert job.error_message == "上游失败"
    assert job.progress == 40
    assert job.finished_at == ids.job_finished_at
    assert job.cancelled_at is None


def test_sibling_retry_arbitration_keeps_exactly_oldest_revival(db_session, monkeypatch):
    """(iii) Both sibling retries passed their pre-CAS guards blind and the
    OLDER sibling's revival commits between our (younger) commit and our
    post-commit re-read. Oldest-wins arbitration must compensate OUR revival
    and 409, leaving exactly one ACTIVE sibling — the older one."""

    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    older = _seed_failed_workflow(
        db_session,
        "兄弟仲裁-老",
        target="candidate-sibling",
        created_at=datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC),
    )
    younger = _seed_failed_workflow(
        db_session,
        "兄弟仲裁-新",
        target="candidate-sibling",
        created_at=datetime(2026, 1, 1, 11, 0, 0, tzinfo=UTC),
    )
    factory = _session_factory(db_session)
    real_guard = job_service._verify_retry_revival_post_commit
    interleaved = {"done": False}

    def _commit_older_revival_then_guard(db, job, snapshot):
        if not interleaved["done"]:
            interleaved["done"] = True
            _commit_sibling_revival(factory, older.job_id)
        return real_guard(db, job, snapshot)

    monkeypatch.setattr(
        job_service, "_verify_retry_revival_post_commit", _commit_older_revival_then_guard
    )

    with pytest.raises(HTTPException) as exc_info:
        job_service.reset_for_retry(db_session, db_session.get(GenerationJob, younger.job_id))

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "任务状态已变化，请重试"
    db_session.expire_all()
    # The younger revival is fully compensated back to its pre-retry shape.
    young_job = db_session.get(GenerationJob, younger.job_id)
    assert young_job.status == JobStatus.FAILED
    assert young_job.error_code == "UPSTREAM"
    assert db_session.get(WorkflowRun, younger.run_id).status == "FAILED"
    assert db_session.get(WorkflowNodeRun, younger.node_id).status == "FAILED"
    assert db_session.get(WorkflowNodeRun, younger.tail_node_id).status == "CANCELLED"
    assert db_session.get(GenerationJob, younger.tail_job_id).status == JobStatus.CANCELLED
    # Exactly one sibling survives: the older committed revival.
    active_siblings = list(
        db_session.scalars(
            select(GenerationJob.id).where(
                GenerationJob.job_type == "PAGE_INSPECT",
                GenerationJob.target_type == "PAGE_CANDIDATE",
                GenerationJob.target_id == "candidate-sibling",
                GenerationJob.status.in_(job_service.ACTIVE_JOB_STATUSES),
            )
        )
    )
    assert active_siblings == [older.job_id]
    assert db_session.get(GenerationJob, older.job_id).status == JobStatus.WAITING


def test_sibling_arbitration_loss_warns_when_compensation_cannot_undo(
    db_session, monkeypatch, caplog
):
    """(iii, undo miss) The arbitration-loss compensation CAS misses when the
    younger revived row is claimed between the retry's commit and the undo
    (WAITING → PREPARING lease claim): the younger revival stays committed and
    dispatchable next to the older survivor — the duplicate the arbitration
    exists to prevent — while the caller only sees the generic 409, so a
    WARNING naming the job and target must reach operators."""

    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    older = _seed_failed_workflow(
        db_session,
        "兄弟仲裁-撤销失败-老",
        target="candidate-sibling-miss",
        created_at=datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC),
    )
    younger = _seed_failed_workflow(
        db_session,
        "兄弟仲裁-撤销失败-新",
        target="candidate-sibling-miss",
        created_at=datetime(2026, 1, 1, 11, 0, 0, tzinfo=UTC),
    )
    factory = _session_factory(db_session)
    real_oldest = job_service.oldest_active_job_id

    def _claim_younger_and_revive_older(db, **kwargs):
        # Between our (younger) revival commit and the arbitration read: a
        # worker claims the freshly revived WAITING row, and the OLDER
        # sibling's revival commits. The arbitration still sees the older
        # sibling as the winner, but the undo CAS can no longer match the
        # younger row's WAITING status.
        with factory() as other:
            other.execute(
                update(GenerationJob)
                .where(GenerationJob.id == younger.job_id)
                .values(
                    status=JobStatus.PREPARING,
                    lease_owner="worker-arbitration-miss",
                    lease_expires_at=utcnow() + timedelta(seconds=60),
                )
                .execution_options(synchronize_session=False)
            )
            other.commit()
        _commit_sibling_revival(factory, older.job_id)
        return real_oldest(db, **kwargs)

    monkeypatch.setattr(job_service, "oldest_active_job_id", _claim_younger_and_revive_older)

    with pytest.raises(HTTPException) as exc_info, caplog.at_level(
        logging.WARNING, logger="mangaflow.jobs"
    ):
        job_service.reset_for_retry(db_session, db_session.get(GenerationJob, younger.job_id))

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "任务状态已变化，请重试"
    warnings = [
        record
        for record in caplog.records
        if record.levelno == logging.WARNING and "sibling-arbitration loss" in record.getMessage()
    ]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert str(younger.job_id) in message
    assert "candidate-sibling-miss" in message
    assert "duplicate" in message
    db_session.expire_all()
    # The undo could not land: the younger revival stays claimed while the
    # older sibling also survives — exactly the surfaced duplicate.
    assert db_session.get(GenerationJob, younger.job_id).status == JobStatus.PREPARING
    assert db_session.get(GenerationJob, older.job_id).status == JobStatus.WAITING


def test_preexisting_older_active_sibling_rejected_by_pre_cas_guard(db_session, monkeypatch):
    """A legitimately pre-existing ACTIVE sibling (older, committed long before
    our retry) is rejected by the pre-CAS has_active_job guard with the mutex
    message — the post-commit arbitration never runs, so it is not
    double-handled."""

    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    ids = _seed_failed_workflow(
        db_session,
        "预存兄弟",
        target="candidate-preexisting",
        created_at=datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC),
    )
    sibling = GenerationJob(
        project_id=ids.project_id,
        target_type="PAGE_CANDIDATE",
        target_id="candidate-preexisting",
        job_type="PAGE_INSPECT",
        status=JobStatus.QUEUED,
        created_at=datetime(2025, 12, 31, 10, 0, 0, tzinfo=UTC),
    )
    db_session.add(sibling)
    db_session.commit()
    submitted: list[str] = []
    monkeypatch.setattr(job_service, "_submit_local", lambda job_id: submitted.append(job_id))
    reached_guard = []
    real_guard = job_service._verify_retry_revival_post_commit
    monkeypatch.setattr(
        job_service,
        "_verify_retry_revival_post_commit",
        lambda db, job, snapshot: reached_guard.append(True) or real_guard(db, job, snapshot),
    )

    with pytest.raises(HTTPException) as exc_info:
        job_service.reset_for_retry(db_session, db_session.get(GenerationJob, ids.job_id))

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "该目标已有进行中的同类任务，请等待完成后再重试"
    assert reached_guard == []  # rejected before the commit; no arbitration
    db_session.expire_all()
    assert db_session.get(GenerationJob, ids.job_id).status == JobStatus.FAILED
    assert db_session.get(GenerationJob, sibling.id).status == JobStatus.QUEUED
    assert submitted == []
