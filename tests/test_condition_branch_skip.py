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
from app.config import get_settings
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
from app.services.job_service import dependencies_complete
from app.services.workflow_engine import reconcile_run
from app.services.workflow_engine.catalog import _edge, _node, graph_checksum
from app.services.workflow_engine.validation import validate_graph
from sqlalchemy import select


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
        # Dead-branch planning jobs are CANCELLED at skip time, not left
        # WAITING: a stranded WAITING row both deadlocks diamond merges
        # (dependencies require every parent job COMPLETED) and hands the
        # recovery loop a row it would resurrect — and paid-execute.
        assert row.status == JobStatus.CANCELLED


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


# P1（第 12 轮主循环第 3 批审查）：菱形 merge 从 WAITING 被调度时，死分支
# 遗留的 WAITING planning job 会让 dependencies_complete（要求全部依赖
# COMPLETED）永远为假——run 卡死 RUNNING，唯一活跃 run 守卫锁死 scope；
# 而恢复循环又会把死分支 job 重新入队、真实执行（含付费调用）死分支。
# 跳过时必须取消死 job 并收回其入边依赖，让 merge 只依赖活分支。
def test_diamond_merge_scheduling_survives_dead_branch_job(db_session, monkeypatch):
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
                "alive",
                "control.condition",
                "活分支",
                540,
                0,
                condition={"path": "$.value", "operator": "exists"},
            ),
            _node(
                "dead",
                "control.condition",
                "死分支",
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
            _edge("cond", "true", "alive", "value"),
            _edge("cond", "false", "dead", "value"),
            _edge("alive", "true", "merge", "left"),
            _edge("dead", "true", "merge", "right"),
            _edge("merge", "merged", "down", "value"),
        ],
    }
    project, run = _seed_graph_run(db_session, "菱形合并死锁", graph)
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
    cond_job = _node_job(db_session, project, run, cond, status=JobStatus.COMPLETED)
    alive = _node_run(
        db_session,
        run,
        "alive",
        "control.condition",
        output_refs={"selected_port": "true", "matched": True},
    )
    alive_job = _node_job(db_session, project, run, alive, status=JobStatus.COMPLETED)
    dead = _node_run(db_session, run, "dead", "control.condition")
    dead_job = _node_job(db_session, project, run, dead)
    # Merge waits for scheduling with planning-wired dependencies on BOTH
    # branch jobs — the exact shape create_workflow_run produces.
    merge = _node_run(db_session, run, "merge", "control.merge")
    merge_job = _node_job(db_session, project, run, merge)
    db_session.add_all(
        [
            JobDependency(job_id=merge_job.id, depends_on_job_id=alive_job.id),
            JobDependency(job_id=merge_job.id, depends_on_job_id=dead_job.id),
        ]
    )
    down = _node_run(db_session, run, "down", "control.condition")
    down_job = _node_job(db_session, project, run, down)
    db_session.add(JobDependency(job_id=down_job.id, depends_on_job_id=merge_job.id))
    db_session.commit()

    result = reconcile_run(db_session, run.id)

    db_session.expire_all()
    statuses = _node_statuses(db_session, run.id)
    assert statuses["dead"].status == "SKIPPED"
    # The dead branch's planning job is retired, so recovery can never
    # resurrect (and pay for) the branch the condition did not select.
    assert db_session.get(GenerationJob, dead_job.id).status == JobStatus.CANCELLED
    # Its inbound dependency edge is retracted: the merge no longer waits on
    # the dead job. The dependency walk may traverse the skipped node up to
    # the completed condition (harmless — already COMPLETED); the essential
    # property is that every remaining dependency is satisfied.
    merge_deps = list(
        db_session.scalars(
            select(JobDependency.depends_on_job_id).where(
                JobDependency.job_id == merge_job.id
            )
        )
    )
    assert dead_job.id not in merge_deps
    assert set(merge_deps) <= {alive_job.id, cond_job.id}
    assert dependencies_complete(db_session, db_session.get(GenerationJob, merge_job.id))
    # The merge is scheduled (node RUNNING; queue disabled stamps the job
    # QUEUE_DISABLED instead of executing) — not wedged WAITING forever.
    assert statuses["merge"].status == "RUNNING"
    merge_job_row = db_session.get(GenerationJob, merge_job.id)
    assert merge_job_row.error_code == "QUEUE_DISABLED"
    # Downstream still waits on the merge; the run is legitimately RUNNING
    # (scheduling made progress), not a zombie.
    assert statuses["down"].status == "WAITING"
    assert result.status == "RUNNING"

    # Idempotent second pass: the healed state must not regress.
    reconcile_run(db_session, run.id)
    db_session.expire_all()
    assert (
        db_session.get(GenerationJob, dead_job.id).status == JobStatus.CANCELLED
    )
    assert _node_statuses(db_session, run.id)["merge"].status == "RUNNING"


def test_recovery_refuses_skipped_nodes(db_session):
    """A SKIPPED node's job belongs to reconcile (cancellation), never to the
    recovery loop — enqueueing it would execute the dead branch."""
    from app.services.job_service import _workflow_node_blocks_recovery

    project, run = _seed_graph_run(
        db_session,
        "恢复阻断",
        {
            "schema_version": 2,
            "nodes": [
                _node("src", "source.chapter", "章节", 0, 0),
                _node("parse", "agent.parse", "解析", 200, 0, model_alias="auto"),
                _node("n", "agent.adapt", "节点", 400, 0, model_alias="auto"),
            ],
            "edges": [
                _edge("src", "source", "parse", "source"),
                _edge("parse", "story", "n", "story"),
            ],
        },
    )
    item = _node_run(db_session, run, "n", "agent.adapt", status="SKIPPED")
    db_session.commit()

    assert _workflow_node_blocks_recovery(db_session, item.id) is True


def test_reconcile_heals_skipped_node_with_stranded_waiting_job(db_session, monkeypatch):
    """Legacy rows (skipped under the pre-fix code, job left WAITING) are
    healed on the first reconcile: the job is cancelled and its inbound
    dependency edges retracted."""
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
            _node("dead", "agent.adapt", "死分支", 600, 0, model_alias="auto"),
            _node("merge", "control.merge", "合并", 800, 0),
        ],
        "edges": [
            _edge("src", "source", "parse", "source"),
            _edge("parse", "story", "cond", "value"),
            _edge("cond", "false", "dead", "story"),
            _edge("cond", "true", "merge", "left"),
            _edge("dead", "script", "merge", "right"),
        ],
    }
    project, run = _seed_graph_run(db_session, "死分支遗留自愈", graph)
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
        status="COMPLETED",
        output_refs={"selected_port": "true", "matched": True, "value": True},
    )
    cond_job = _node_job(db_session, project, run, cond, status=JobStatus.COMPLETED)
    # The legacy shape: node already SKIPPED, planning job still WAITING, and
    # the merge's dependency set still contains the dead job.
    dead = _node_run(
        db_session,
        run,
        "dead",
        "agent.adapt",
        status="SKIPPED",
        output_refs={"reason": "CONDITION_BRANCH_NOT_SELECTED"},
    )
    dead_job = _node_job(db_session, project, run, dead)
    merge = _node_run(db_session, run, "merge", "control.merge")
    merge_job = _node_job(db_session, project, run, merge)
    db_session.add_all(
        [
            JobDependency(job_id=merge_job.id, depends_on_job_id=cond_job.id),
            JobDependency(job_id=merge_job.id, depends_on_job_id=dead_job.id),
        ]
    )
    db_session.commit()

    reconcile_run(db_session, run.id)

    db_session.expire_all()
    assert db_session.get(GenerationJob, dead_job.id).status == JobStatus.CANCELLED
    merge_deps = list(
        db_session.scalars(
            select(JobDependency.depends_on_job_id).where(
                JobDependency.job_id == merge_job.id
            )
        )
    )
    assert merge_deps == [cond_job.id]
    statuses = _node_statuses(db_session, run.id)
    assert statuses["merge"].status == "RUNNING"

