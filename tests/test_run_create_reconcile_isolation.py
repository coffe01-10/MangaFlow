"""A failed post-creation reconcile must not fail an already-committed start.

create_workflow_run commits the run (RUNNING) and only then runs the first
scheduling reconcile. A bare reconcile call meant that a scheduling failure
(e.g. _submit_local re-raising on executor shutdown) surfaced to the client
as a 500 after the run was already committed — and the natural client retry
of POST /runs then hit the duplicate-run guard (409 该范围已有进行中的运行)，
locking the scope until recovery healed the run or someone cancelled it.
Mirror the completion path (worker_tasks) and the recovery loop
(job_service): log the failure and return the committed run — it self-heals
via the next reconcile trigger (recovery routes WAITING-node jobs back to
reconcile_run). The committed run is never rolled back.

A single source-node graph keeps the fixture minimal (no chapter revision,
no pages, no paid jobs), per tests/test_run_creation_guard.py.
"""

import logging

from sqlalchemy import select

from app.models import Project, WorkflowDefinition, WorkflowRun
from app.services.workflow_engine import create_workflow_run, publish_workflow
from app.services.workflow_engine import planning


def _failure_records(caplog, message: str) -> list[logging.LogRecord]:
    return [
        record
        for record in caplog.records
        if record.levelno >= logging.ERROR and message in record.getMessage()
    ]


def _source_only_graph() -> dict:
    return {
        "schema_version": 2,
        "nodes": [
            {
                "id": "src",
                "type": "source.chapter",
                "name": "原文",
                "inputs": [],
                "outputs": [
                    {
                        "id": "source",
                        "label": "原始文本",
                        "data_type": "text",
                        "required": False,
                    }
                ],
            },
        ],
        "edges": [],
    }


def _seed_published_workflow(db) -> WorkflowDefinition:
    project = Project(name="创建后reconcile隔离")
    db.add(project)
    db.flush()
    workflow = WorkflowDefinition(
        project_id=project.id,
        name="源节点流程",
        draft_graph=_source_only_graph(),
    )
    db.add(workflow)
    db.commit()
    publish_workflow(db, workflow)
    db.refresh(workflow)
    return workflow


def _start_kwargs(project_id: str) -> dict:
    return {
        "scope_type": "PROJECT",
        "scope_id": project_id,
        "start_node_ids": [],
        "stop_node_ids": [],
    }


def test_reconcile_failure_after_creation_does_not_fail_the_start(
    db_session, monkeypatch, caplog
):
    """T1 (failing-first): a poisoned first reconcile must not 500 the start."""

    workflow = _seed_published_workflow(db_session)

    def poisoned_reconcile(_db, poisoned_run_id):
        raise RuntimeError(f"reconcile exploded for {poisoned_run_id}")

    # planning.py binds reconcile_run at module top from .reconciliation, so
    # the seam create_workflow_run actually resolves is the planning module
    # global — not the workflow_engine facade (whose lazy-import seams are
    # patched by tests/test_reconcile_failure_isolation.py).
    monkeypatch.setattr(planning, "reconcile_run", poisoned_reconcile)

    with caplog.at_level(logging.ERROR, logger="mangaflow.workflow"):
        started = create_workflow_run(
            db_session, workflow, **_start_kwargs(workflow.project_id)
        )

    # The start returned the committed run: with the poisoned reconcile never
    # advancing anything, it stays exactly as committed (RUNNING) instead of
    # the request failing after the fact.
    assert started.status == "RUNNING"
    runs = db_session.scalars(
        select(WorkflowRun).where(WorkflowRun.workflow_id == workflow.id)
    ).all()
    assert len(runs) == 1
    assert runs[0].id == started.id
    assert runs[0].status == "RUNNING"

    records = _failure_records(caplog, "reconcile failed after creation")
    assert records, (
        "expected an ERROR log naming the run after the post-creation "
        "reconcile failure (pre-fix the exception propagated and nothing "
        "was logged)"
    )
    assert started.id in records[0].getMessage()


def test_reconcile_success_path_is_preserved(db_session):
    """T2 (preservation): without the poison, creation behaves as before."""

    workflow = _seed_published_workflow(db_session)

    started = create_workflow_run(db_session, workflow, **_start_kwargs(workflow.project_id))

    # The real first reconcile still runs: a source-only run
    # reconcile-completes instantly (same premise as
    # tests/test_run_creation_guard.py).
    assert started.status == "COMPLETED"
