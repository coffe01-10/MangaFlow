"""Exercise both archive/start lock orders with two real PostgreSQL sessions."""

import time
from concurrent.futures import ThreadPoolExecutor
from queue import Queue

import pytest
from app.api.routes.projects import archive_project
from app.models import GenerationJob, Project, WorkflowDefinition, WorkflowRun
from app.services.job_service import reset_for_retry
from app.services.ordinal_allocator import lock_entity
from app.services.workflow_engine import planning
from fastapi import HTTPException
from sqlalchemy import select, text

from tests.test_run_creation_guard import _seed_published_workflow, _start_kwargs


@pytest.mark.parametrize("winner", ["archive", "start", "archive_retry", "archive_archive"])
def test_pg_archive_serializes_every_restart_entry(live_pg_session_factory, monkeypatch, winner):
    factory = live_pg_session_factory
    # Only prevent scheduling; every lock, insert, cancellation and commit is real PG.
    monkeypatch.setattr(planning, "reconcile_run", lambda *_args: None)
    with factory() as seed:
        workflow = _seed_published_workflow(seed)
        workflow_id, project_id = workflow.id, workflow.project_id
        project_name = seed.get(Project, project_id).name
        job = GenerationJob(
            project_id=project_id,
            target_type="PROJECT",
            target_id=project_id,
            job_type="SOURCE_PARSE",
            status="FAILED",
        )
        seed.add(job)
        seed.commit()
        job_id = job.id

    pid_ready = Queue()

    def start(db):
        return planning.create_workflow_run(
            db, db.get(WorkflowDefinition, workflow_id), **_start_kwargs(project_id)
        )

    def contender():
        with factory() as db:
            pid_ready.put(db.scalar(text("SELECT pg_backend_pid()")))
            if winner in {"start", "archive_archive"}:
                return archive_project(project_id, project_name, db)
            if winner == "archive_retry":
                return reset_for_retry(db, db.get(GenerationJob, job_id))
            return start(db)

    with ThreadPoolExecutor(max_workers=1) as pool, factory() as owner:
        lock_entity(owner, Project, project_id)
        pending = pool.submit(contender)
        try:
            pid = pid_ready.get(timeout=5)
            deadline = time.monotonic() + 4
            while True:
                with factory() as observer:
                    blocked = observer.scalar(
                        text("SELECT cardinality(pg_blocking_pids(:pid))"), {"pid": pid}
                    )
                if blocked:
                    break
                assert not pending.done(), "contender bypassed the held project lock"
                assert time.monotonic() < deadline, "contender never waited on the project lock"
                time.sleep(0.02)
            if winner == "start":
                start(owner)
            else:
                archive_project(project_id, project_name, owner)
        finally:
            owner.rollback()  # Always release the lock before joining a failed contender.
        if winner == "start":
            pending.result(timeout=5)
        elif winner == "archive":
            with pytest.raises(ValueError, match="已归档"):
                pending.result(timeout=5)
        else:
            with pytest.raises(HTTPException) as raised:
                pending.result(timeout=5)
            assert raised.value.status_code == (404 if winner == "archive_archive" else 409)

    with factory() as db:
        assert db.get(Project, project_id).deleted_at is not None
        runs = list(db.scalars(select(WorkflowRun).where(WorkflowRun.project_id == project_id)))
        assert all(run.status == "CANCELLED" for run in runs)
        assert len(runs) == (1 if winner == "start" else 0)
        assert db.get(GenerationJob, job_id).status == "FAILED"
