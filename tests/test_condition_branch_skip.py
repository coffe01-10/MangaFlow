"""Condition-branch skipping must be transitive (reconciliation).

The pre-fix skip only inspected DIRECT edges from a completed
control.condition: in a condition→A→B chain on the unselected port, A became
SKIPPED and B then passed the parent gate (SKIPPED counts as a satisfied
parent) because B's edge arrives from A, not from the condition — so B
executed on a wholly dead branch (a paid generator.page barrier could even
pause at WAITING_APPROVAL and later be approved). Dead-branch membership is
now computed transitively per reconciliation pass, while parentless nodes and
merges fed by any live branch stay runnable.

All rows are seeded directly (the run-guard regression style): with the
queue disabled no provider call can run, and reconcile's decisions are a
pure function of the seeded node_run/job states.
"""

import pytest
from sqlalchemy import select

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
from app.services.workflow_engine import reconcile_run
from app.services.workflow_engine.catalog import _edge, _node, graph_checksum
from app.services.workflow_engine.validation import validate_graph


def _seed_graph_run(db, name: str, graph: dict):
    report = validate_graph(graph)
    assert report.valid, [issue.message for issue in report.issues]
    project = Project(name=name)
    db.add(project)
    db.flush()
    workflow = WorkflowDefinition(project_id=project.id, name=name, draft_graph=graph)
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
        scope_type="CHAPTER",
        scope_id="scope-x",
        status="RUNNING",
        start_node_ids=[],
        stop_node_ids=[],
        started_at=utcnow(),
    )
    db.add(run)
    db.commit()
    return project, run


def _node_run(db, run, node_id, node_type, *, status="WAITING", output_refs=None):
    item = WorkflowNodeRun(
        workflow_run_id=run.id,
        node_id=node_id,
        node_type=node_type,
        status=status,
        output_refs=output_refs if output_refs is not None else {},
        started_at=run.started_at if status == "COMPLETED" else None,
        finished_at=utcnow() if status == "COMPLETED" else None,
    )
    db.add(item)
    db.flush()
    return item


def _node_job(db, project, run, item, *, status=JobStatus.WAITING):
    job = GenerationJob(
        project_id=project.id,
        target_type="WORKFLOW_NODE",
        target_id=item.id,
        job_type="WORKFLOW_NODE",
        status=status,
        request_parameters={
            "workflow_run_id": run.id,
            "workflow_node_run_id": item.id,
            "node_id": item.node_id,
            "node_type": item.node_type,
        },
        idempotency_key=f"workflow:{run.id}:{item.node_id}:1",
    )
    db.add(job)
    db.flush()
    item.job_id = job.id
    return job


def _node_statuses(db, run_id: str) -> dict[str, WorkflowNodeRun]:
    return {
        item.node_id: item
        for item in db.scalars(
            select(WorkflowNodeRun).where(WorkflowNodeRun.workflow_run_id == run_id)
        )
    }


def test_unselected_branch_skip_is_transitive(db_session, monkeypatch):
    """(a) condition→A→B chain on the unselected port: A AND B are SKIPPED,
    and B is never scheduled — its planning-minted job is not enqueued (and a
    barrier node would never reach WAITING_APPROVAL), so no paid work can
    start on the dead branch."""

    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    graph = {
        "schema_version": 2,
        "nodes": [
            _node("src", "source.chapter", "章节", 0, 0),
            _node("parse", "agent.parse", "解析", 200, 0, model_alias="auto"),
            _node(
                "cond",
                "control.condition",
                "条件",
                400,
                0,
                condition={"path": "$.story.ready", "operator": "eq", "value": True},
            ),
            _node("dead_a", "agent.adapt", "未选分支A", 600, 0, model_alias="auto"),
            _node(
                "dead_b",
                "director.storyboard",
                "未选分支B",
                800,
                0,
                model_alias="auto",
            ),
        ],
        "edges": [
            _edge("src", "source", "parse", "source"),
            _edge("parse", "story", "cond", "value"),
            _edge("cond", "false", "dead_a", "story"),
            _edge("dead_a", "script", "dead_b", "script"),
        ],
    }
    project, run = _seed_graph_run(db_session, "条件传递跳过", graph)
    _node_run(
        db_session, run, "src", "source.chapter", status="COMPLETED",
        output_refs={"kind": "source"},
    )
    _node_run(
        db_session, run, "parse", "agent.parse", status="COMPLETED",
        output_refs={"story": {"ready": True}},
    )
    cond = _node_run(
        db_session,
        run,
        "cond",
        "control.condition",
        output_refs={"selected_port": "true", "matched": True, "value": True},
    )
    _node_job(db_session, project, run, cond, status=JobStatus.COMPLETED)
    dead_a = _node_run(db_session, run, "dead_a", "agent.adapt")
    job_a = _node_job(db_session, project, run, dead_a)
    dead_b = _node_run(db_session, run, "dead_b", "director.storyboard")
    job_b = _node_job(db_session, project, run, dead_b)
    db_session.commit()

    result = reconcile_run(db_session, run.id)

    db_session.expire_all()
    assert result.status == "COMPLETED"
    statuses = _node_statuses(db_session, run.id)
    assert statuses["cond"].status == "COMPLETED"
    # Direct branch keeps the pinned skip behavior…
    assert statuses["dead_a"].status == "SKIPPED"
    assert statuses["dead_a"].output_refs.get("reason") == "CONDITION_BRANCH_NOT_SELECTED"
    # …and the skip now propagates past the first hop: B is skipped before
    # any scheduling (the old code set it RUNNING and enqueued its job).
    assert statuses["dead_b"].status == "SKIPPED"
    assert statuses["dead_b"].output_refs.get("reason") == "CONDITION_BRANCH_NOT_SELECTED"
    for job in (job_a, job_b):
        row = db_session.get(GenerationJob, job.id)
        assert row.status == JobStatus.WAITING  # never enqueued…
        assert row.error_code is None  # …not even stamped QUEUE_DISABLED


@pytest.mark.parametrize("selected_port", ["true", "false"])
def test_merge_with_dead_and_selected_parents_runs(db_session, monkeypatch, selected_port):
    """(b)/(c) diamond: control.merge has one parent on the dead branch and
    one on the selected branch — it is NOT dead, runs, and its downstream is
    scheduled, whichever branch the condition selected."""

    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    graph = {
        "schema_version": 2,
        "nodes": [
            _node("src", "source.chapter", "章节", 0, 0),
            _node("parse", "agent.parse", "解析", 180, 0, model_alias="auto"),
            _node(
                "cond",
                "control.condition",
                "条件",
                360,
                0,
                condition={"path": "$.story.ready", "operator": "eq", "value": True},
            ),
            _node(
                "branch_true",
                "control.condition",
                "真分支",
                540,
                0,
                condition={"path": "$.value", "operator": "exists"},
            ),
            _node(
                "branch_false",
                "control.condition",
                "假分支",
                540,
                200,
                condition={"path": "$.value", "operator": "exists"},
            ),
            _node("merge", "control.merge", "合并", 720, 100),
            _node(
                "down",
                "control.condition",
                "下游",
                900,
                100,
                condition={"path": "$.merged", "operator": "exists"},
            ),
        ],
        "edges": [
            _edge("src", "source", "parse", "source"),
            _edge("parse", "story", "cond", "value"),
            _edge("cond", "true", "branch_true", "value"),
            _edge("cond", "false", "branch_false", "value"),
            _edge("branch_true", "true", "merge", "left"),
            _edge("branch_false", "true", "merge", "right"),
            _edge("merge", "merged", "down", "value"),
        ],
    }
    alive_id, dead_id = (
        ("branch_true", "branch_false")
        if selected_port == "true"
        else ("branch_false", "branch_true")
    )
    project, run = _seed_graph_run(
        db_session, f"菱形合并-{selected_port}", graph
    )
    _node_run(
        db_session, run, "src", "source.chapter", status="COMPLETED",
        output_refs={"kind": "source"},
    )
    _node_run(
        db_session, run, "parse", "agent.parse", status="COMPLETED",
        output_refs={"story": {"ready": True}},
    )
    cond = _node_run(
        db_session,
        run,
        "cond",
        "control.condition",
        output_refs={"selected_port": selected_port, "matched": True, "value": True},
    )
    _node_job(db_session, project, run, cond, status=JobStatus.COMPLETED)
    alive = _node_run(
        db_session,
        run,
        alive_id,
        "control.condition",
        output_refs={"selected_port": "true", "matched": True},
    )
    _node_job(db_session, project, run, alive, status=JobStatus.COMPLETED)
    dead = _node_run(db_session, run, dead_id, "control.condition")
    _node_job(db_session, project, run, dead)
    merge = _node_run(
        db_session, run, "merge", "control.merge", output_refs={"merged": {"left": {}}}
    )
    _node_job(db_session, project, run, merge, status=JobStatus.COMPLETED)
    down = _node_run(db_session, run, "down", "control.condition")
    _node_job(db_session, project, run, down)
    db_session.commit()

    result = reconcile_run(db_session, run.id)

    db_session.expire_all()
    statuses = _node_statuses(db_session, run.id)
    assert statuses[alive_id].status == "COMPLETED"
    assert statuses[dead_id].status == "SKIPPED"
    assert statuses[dead_id].output_refs.get("reason") == "CONDITION_BRANCH_NOT_SELECTED"
    # The merge has parents on both a dead and the selected branch: it runs.
    assert statuses["merge"].status == "COMPLETED"
    assert "reason" not in statuses["merge"].output_refs
    # Downstream of the merge is scheduled, never skipped.
    assert statuses["down"].status == "RUNNING"
    assert statuses["down"].job_id is not None
    assert result.status == "RUNNING"
