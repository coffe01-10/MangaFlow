"""Reconcile failures must be logged and isolated, never silently swallowed.

R-5: after a job's COMPLETED claim is committed and its outputs flushed, a
workflow reconcile failure falls through to the worker failure path, whose
conditional claim only matches ACTIVE rows — for a COMPLETED job it no-ops
with zero logging while the node/run stall RUNNING forever.
R-6: recover_pending_jobs must not let one poisoned run skip the remaining
reconciliations or the phase-2 WAITING/QUEUED requeue, and the startup
caller must not let a recovery failure abort API boot.
"""

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import database, worker_tasks
from app.config import Settings
from app.database import Base
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
from app.services import job_service, workflow_engine
from app.services.workflow_engine import default_graph


def _failure_records(caplog, message: str) -> list[logging.LogRecord]:
    return [
        record
        for record in caplog.records
        if record.levelno >= logging.ERROR and message in record.getMessage()
    ]


def _workflow_run(db, project: Project, name: str, checksum: str) -> WorkflowRun:
    workflow = WorkflowDefinition(project_id=project.id, name=name, draft_graph=default_graph())
    db.add(workflow)
    db.flush()
    version = WorkflowVersion(
        workflow_id=workflow.id,
        revision=1,
        graph=default_graph(),
        graph_checksum=checksum,
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
        status="RUNNING",
    )
    db.add(run)
    db.flush()
    return run


def _expired_workflow_job(db, project: Project, run: WorkflowRun, target: str) -> GenerationJob:
    """An exhausted-lease GENERATING job of a workflow node, past its lease."""

    job = GenerationJob(
        project_id=project.id,
        target_type="CHAPTER",
        target_id=target,
        job_type="SOURCE_PARSE",
        status=JobStatus.GENERATING,
        attempt_count=1,
        max_attempts=1,
        lease_owner="dead-worker",
        lease_expires_at=datetime.now(UTC) - timedelta(minutes=1),
        request_parameters={"workflow_run_id": run.id},
    )
    db.add(job)
    db.flush()
    db.add(
        WorkflowNodeRun(
            workflow_run_id=run.id,
            node_id="generate",
            node_type="generator.page",
            status="RUNNING",
            job_id=job.id,
        )
    )
    return job


def _set_queue_mode(db, mode: str) -> None:
    db.add(AppSetting(key="runtime", value={"queue_mode": mode}, version=1))
    db.commit()


def test_reconcile_failure_after_completion_is_logged_not_swallowed(monkeypatch, caplog):
    """R-5: a post-completion reconcile failure is logged; the job stays COMPLETED.

    The exception must not reach the outer ``except Exception``: its failure
    claim cannot match a COMPLETED row, so the failure used to vanish without
    any log and the run stayed RUNNING with nothing to heal it.
    """

    with TemporaryDirectory() as directory:
        engine = create_engine(
            f"sqlite:///{Path(directory) / 'reconcile-after-complete.db'}",
            connect_args={"check_same_thread": False},
        )
        testing_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        Base.metadata.create_all(engine)
        try:
            with testing_session() as db:
                project = Project(name="完成后reconcile失败")
                db.add(project)
                db.flush()
                run = _workflow_run(db, project, "完成工作流", "r" * 64)
                db.flush()
                job = GenerationJob(
                    project_id=project.id,
                    target_type="CHAPTER",
                    target_id="reconcile-after-complete",
                    job_type="SOURCE_PARSE",
                    status=JobStatus.QUEUED,
                    request_parameters={"workflow_run_id": run.id},
                )
                db.add(job)
                db.commit()
                job_id, run_id = job.id, run.id

            monkeypatch.setattr(worker_tasks, "SessionLocal", testing_session)
            monkeypatch.setattr(database, "SessionLocal", testing_session)
            monkeypatch.setattr(worker_tasks, "_run_story_parse", lambda _db, _job: None)

            def poisoned_reconcile(_db, poisoned_run_id):
                raise RuntimeError(f"reconcile exploded for {poisoned_run_id}")

            monkeypatch.setattr(workflow_engine, "reconcile_run", poisoned_reconcile)

            with caplog.at_level(logging.ERROR, logger="mangaflow.worker"):
                worker_tasks.execute_job(job_id)

            with testing_session() as db:
                completed = db.get(GenerationJob, job_id)
                # The job is done and its outputs flushed: the reconcile
                # failure must not mark it failed or retry it.
                assert completed.status == JobStatus.COMPLETED
                assert completed.error_code is None
                assert completed.error_message is None
                assert completed.progress == 100
                # The run itself was not healed by the poisoned reconcile.
                assert db.get(WorkflowRun, run_id).status == "RUNNING"

            records = _failure_records(caplog, "reconcile failed after job completion")
            assert records, (
                "expected an ERROR log naming the run after the post-completion "
                "reconcile failure (pre-fix it vanished with zero logging)"
            )
            assert run_id in records[0].getMessage()
        finally:
            engine.dispose()


def test_recovery_pass_reconciles_remaining_runs_after_poisoned_run(
    db_session, monkeypatch, caplog
):
    """R-6: one poisoned run must not skip the other runs' reconciliation."""

    _set_queue_mode(db_session, "LOCAL")
    project = Project(name="恢复reconcile隔离")
    db_session.add(project)
    db_session.flush()
    run_a = _workflow_run(db_session, project, "运行A", "a" * 64)
    run_b = _workflow_run(db_session, project, "运行B", "b" * 64)
    db_session.flush()
    job_a = _expired_workflow_job(db_session, project, run_a, "exhausted-a")
    job_b = _expired_workflow_job(db_session, project, run_b, "exhausted-b")
    db_session.commit()

    real_reconcile_run = workflow_engine.reconcile_run

    def poisoned_reconcile(db, run_id):
        if run_id == run_a.id:
            raise RuntimeError(f"reconcile exploded for {run_id}")
        return real_reconcile_run(db, run_id)

    monkeypatch.setattr(workflow_engine, "reconcile_run", poisoned_reconcile)
    monkeypatch.setattr(
        job_service, "get_settings", lambda: Settings(environment="development")
    )
    monkeypatch.setattr(job_service, "_submit_local", lambda _job_id: None)

    with caplog.at_level(logging.ERROR, logger="mangaflow.jobs"):
        job_service.recover_pending_jobs(db_session)

    db_session.expire_all()
    # Both exhausted jobs were terminalized by phase 1...
    assert db_session.get(GenerationJob, job_a.id).status == JobStatus.FAILED
    assert db_session.get(GenerationJob, job_b.id).status == JobStatus.FAILED
    # ...and run B still got reconciled to its terminal state despite the
    # poisoned run A (pre-fix run B was skipped entirely).
    assert db_session.get(WorkflowRun, run_b.id).status == "FAILED"
    assert db_session.get(WorkflowRun, run_a.id).status == "RUNNING"

    records = _failure_records(caplog, "reconcile failed during recovery")
    assert records, (
        "expected an ERROR log naming the poisoned run during recovery "
        "(pre-fix the pass aborted without any log)"
    )
    assert run_a.id in records[0].getMessage()


def test_recovery_survives_reconcile_raising_for_every_run_and_still_requeues(
    db_session, monkeypatch, caplog
):
    """R-6/startup: recovery never raises and still runs the phase-2 requeue.

    The lifespan calls recover_pending_jobs without its own recovery path, so
    a persistently poisoned run used to abort the requeue and, at startup,
    the API boot itself.
    """

    _set_queue_mode(db_session, "LOCAL")
    project = Project(name="恢复启动隔离")
    db_session.add(project)
    db_session.flush()
    run = _workflow_run(db_session, project, "毒化运行", "c" * 64)
    db_session.flush()
    _expired_workflow_job(db_session, project, run, "exhausted-poison")
    waiting = GenerationJob(
        project_id=project.id,
        target_type="CHAPTER",
        target_id="waiting-target",
        job_type="SOURCE_PARSE",
        status=JobStatus.WAITING,
    )
    db_session.add(waiting)
    db_session.commit()
    waiting_id = waiting.id

    def poisoned_reconcile(_db, run_id):
        raise RuntimeError(f"reconcile exploded for {run_id}")

    monkeypatch.setattr(workflow_engine, "reconcile_run", poisoned_reconcile)
    monkeypatch.setattr(
        job_service, "get_settings", lambda: Settings(environment="development")
    )
    submitted: list[str] = []
    monkeypatch.setattr(job_service, "_submit_local", lambda job_id: submitted.append(job_id))

    with caplog.at_level(logging.ERROR, logger="mangaflow.jobs"):
        recovered = job_service.recover_pending_jobs(db_session)

    # Phase-2 requeue still ran for the WAITING job.
    assert submitted == [waiting_id]
    assert recovered == 1
    db_session.expire_all()
    assert db_session.get(GenerationJob, waiting_id).status == JobStatus.QUEUED

    records = _failure_records(caplog, "reconcile failed during recovery")
    assert records and run.id in records[0].getMessage()
