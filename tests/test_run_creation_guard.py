"""Duplicate run starts must not double-execute paid work.

POST /workflows/{id}/runs had no active-run guard: every run mints per-run
idempotency keys (workflow:{run}:{node}:1), so a double-click or a client
retry created a second concurrent run and a second paid job on the same
target. The guard admits one non-terminal run per
(workflow, scope_type, scope_id); terminal runs never block retry_run or a
fresh start, and different scopes stay independent.

A single source-node graph keeps the fixture minimal (no chapter revision,
no pages, no paid jobs). Active-run preconditions are seeded directly as
WorkflowRun rows because a source-only run reconcile-completes instantly.
"""

import pytest
from sqlalchemy import select

from app.models import Project, WorkflowDefinition, WorkflowRun, utcnow
from app.services.workflow_engine import create_workflow_run, publish_workflow


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
    project = Project(name="运行创建防护")
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


def _seed_run(db, workflow: WorkflowDefinition, *, status: str, scope_id: str | None) -> WorkflowRun:
    run = WorkflowRun(
        workflow_id=workflow.id,
        workflow_version_id=workflow.published_version_id,
        project_id=workflow.project_id,
        scope_type="PROJECT",
        scope_id=scope_id,
        status=status,
        started_at=None if status in {"COMPLETED", "FAILED", "CANCELLED"} else utcnow(),
        finished_at=utcnow() if status in {"COMPLETED", "FAILED", "CANCELLED"} else None,
    )
    db.add(run)
    db.commit()
    return run


def _start_kwargs(project_id: str) -> dict:
    return {
        "scope_type": "PROJECT",
        "scope_id": project_id,
        "start_node_ids": [],
        "stop_node_ids": [],
    }


def test_second_active_run_for_same_scope_is_refused(db_session):
    """T1 (failing-first): a second start while one run is active 409s."""

    workflow = _seed_published_workflow(db_session)
    _seed_run(db_session, workflow, status="RUNNING", scope_id=workflow.project_id)

    with pytest.raises(ValueError, match="该范围已有进行中的运行"):
        create_workflow_run(
            db_session, workflow, **_start_kwargs(workflow.project_id)
        )

    runs = db_session.scalars(
        select(WorkflowRun).where(WorkflowRun.workflow_id == workflow.id)
    ).all()
    assert len(runs) == 1


@pytest.mark.parametrize("terminal", ["FAILED", "CANCELLED", "COMPLETED"])
def test_terminal_runs_do_not_block_a_new_start(db_session, terminal):
    """T2: start-after-failure / cancel / completion all keep working."""

    workflow = _seed_published_workflow(db_session)
    seeded = _seed_run(db_session, workflow, status=terminal, scope_id=workflow.project_id)
    first_id = seeded.id

    # A source-only run reconcile-completes instantly; what matters here is
    # that the terminal predecessor did not block the start.
    second = create_workflow_run(
        db_session, workflow, **_start_kwargs(workflow.project_id)
    )
    assert second.status in {"RUNNING", "COMPLETED"}
    assert second.id != first_id


def test_different_scope_ids_run_concurrently(db_session):
    """T3: the guard is per (workflow, scope_type, scope_id), not global."""

    workflow = _seed_published_workflow(db_session)
    _seed_run(db_session, workflow, status="RUNNING", scope_id=None)

    other = create_workflow_run(
        db_session, workflow, **_start_kwargs(workflow.project_id)
    )
    assert other.status in {"RUNNING", "COMPLETED"}


def test_route_renders_blocked_start_as_409(db_session, client):
    """T4: the start route maps the guard onto the family's 409 shape."""

    workflow = _seed_published_workflow(db_session)
    _seed_run(db_session, workflow, status="RUNNING", scope_id=workflow.project_id)

    conflict = client.post(
        f"/api/v1/workflows/{workflow.id}/runs",
        json=_start_kwargs(workflow.project_id),
    )
    assert conflict.status_code == 409
    assert "该范围已有进行中的运行" in conflict.json()["detail"]
