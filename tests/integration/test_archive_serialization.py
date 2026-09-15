"""Exercise both archive/start lock orders with two real PostgreSQL sessions."""

import time
from concurrent.futures import ThreadPoolExecutor
from queue import Queue

import pytest
from app.models import (
    GenerationJob,
    Project,
    WorkflowDefinition,
    WorkflowNodeRun,
    WorkflowRun,
    utcnow,
)
from app.services.job_service import reset_for_retry
from app.services.ordinal_allocator import lock_entity
from app.services.project_archive import archive_project
from app.services.workflow_engine import (
    approve_node,
    default_graph,
    planning,
    publish_workflow,
)
from fastapi import HTTPException
from sqlalchemy import select, text

from tests.test_run_creation_guard import _seed_published_workflow, _start_kwargs


@pytest.mark.parametrize(
    "winner",
    ["archive", "start", "archive_retry", "archive_archive", "archive_approve"],
)
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
        paused_run_id = node_id = None
        if winner == "archive_approve":
            # A run parked at the GENERATE barrier is exactly the approval
            # unit that locks run → project inside create_generation_batch;
            # archive's project → run order must serialize ahead of it.
            approve_wf = WorkflowDefinition(
                project_id=project_id,
                name="审批竞争流程",
                draft_graph=default_graph(),
            )
            seed.add(approve_wf)
            seed.flush()
            publish_workflow(seed, approve_wf)
            seed.refresh(approve_wf)
            paused_run = WorkflowRun(
                workflow_id=approve_wf.id,
                workflow_version_id=approve_wf.published_version_id,
                project_id=project_id,
                # The approval unit stops at the project fence before ever
                # dereferencing the page, so a placeholder scope is enough.
                scope_type="PAGE",
                scope_id="page-race",
                status="PAUSED",
                started_at=utcnow(),
            )
            seed.add(paused_run)
            seed.flush()
            seed.add(
                WorkflowNodeRun(
                    workflow_run_id=paused_run.id,
                    node_id="generate",
                    node_type="generator.page",
                    status="WAITING_APPROVAL",
                )
            )
            seed.commit()
            paused_run_id, node_id = paused_run.id, "generate"

    pid_ready = Queue()

    def start(db):
        return planning.create_workflow_run(
            db, db.get(WorkflowDefinition, workflow_id), **_start_kwargs(project_id)
        )

    def contender():
        with factory() as db:
            pid_ready.put(db.scalar(text("SELECT pg_backend_pid()")))
            if winner in {"start", "archive_archive"}:
                return archive_project(db, project_id, confirm_name=project_name)
            if winner == "archive_retry":
                return reset_for_retry(db, db.get(GenerationJob, job_id))
            if winner == "archive_approve":
                return approve_node(
                    db,
                    paused_run_id,
                    node_id,
                    image_model_alias="image.nano_banana_2",
                    resolution="1K",
                )
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
                archive_project(owner, project_id, confirm_name=project_name)
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
            assert raised.value.status_code == (
                404 if winner in {"archive_archive", "archive_approve"} else 409
            )

    with factory() as db:
        assert db.get(Project, project_id).deleted_at is not None
        runs = list(db.scalars(select(WorkflowRun).where(WorkflowRun.project_id == project_id)))
        assert all(run.status == "CANCELLED" for run in runs)
        assert len(runs) == (1 if winner in {"start", "archive_approve"} else 0)
        assert db.get(GenerationJob, job_id).status == "FAILED"
        if winner == "archive_approve":
            barrier_node = db.scalar(
                select(WorkflowNodeRun).where(
                    WorkflowNodeRun.workflow_run_id == paused_run_id,
                    WorkflowNodeRun.node_id == node_id,
                )
            )
            assert barrier_node.status == "CANCELLED"
