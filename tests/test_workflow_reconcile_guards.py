"""Round-6 reconcile/planning race guards (issues #655/#656/#657).

Each test forces the concurrent writer to land inside the exact window the
review flagged, using the shared-connection two-session pattern from
``test_workflow_race_guards.py``: the SQLite StaticPool hands both sessions
the same connection, so the second session's commit is immediately visible
to the first session's next read — the check-then-commit interleave the
production race exhibits.

The queue stays disabled throughout — no provider call can run.
"""

from sqlalchemy import update
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.models import (
    Project,
    WorkflowDefinition,
    WorkflowNodeRun,
    WorkflowRun,
    WorkflowVersion,
    utcnow,
)
from app.services.workflow_engine.catalog import graph_checksum
from app.services.workflow_engine.reconciliation import reconcile_run


def _session_factory(db_session):
    return sessionmaker(bind=db_session.get_bind(), autoflush=False, expire_on_commit=False)


def _definition_and_version(db, project: Project, name: str, graph: dict):
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
    return workflow, version


def _cancel_run_via_second_session(factory, run_id: str) -> None:
    """Mimic cancel_run's committed claim: run CANCELLED + non-terminal nodes
    swept to CANCELLED (lifecycle.py cancel_run), all committed by the
    concurrent writer."""
    with factory() as other:
        other.execute(
            update(WorkflowRun)
            .where(WorkflowRun.id == run_id)
            .values(
                status="CANCELLED",
                finished_at=utcnow(),
                version=WorkflowRun.version + 1,
            )
            .execution_options(synchronize_session=False)
        )
        other.execute(
            update(WorkflowNodeRun)
            .where(
                WorkflowNodeRun.workflow_run_id == run_id,
                WorkflowNodeRun.status.not_in(["COMPLETED", "FAILED", "CANCELLED"]),
            )
            .values(status="CANCELLED", finished_at=utcnow())
            .execution_options(synchronize_session=False)
        )
        other.commit()


def test_running_fast_path_is_fenced_against_concurrent_cancel(db_session, monkeypatch):
    """#655: the desired==RUNNING fast path used to commit the session's
    pending node writes unconditionally. A cancel_run committing between
    reconcile's final status recheck (the line-443 refresh) and that commit
    had its CANCELLED node row overwritten by the pending RUNNING stamp — a
    zombie RUNNING node under a CANCELLED run. The fast path must claim the
    run conditionally and roll the pending writes back when the claim is
    lost, instead of resurrecting the node."""

    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    project = Project(name="RUNNING快路径终态栅栏")
    db_session.add(project)
    db_session.flush()
    graph = {"schema_version": 2, "nodes": [], "edges": []}
    _, version = _definition_and_version(db_session, project, "快路径栅栏流程", graph)
    run = WorkflowRun(
        workflow_id=version.workflow_id,
        workflow_version_id=version.id,
        project_id=project.id,
        scope_type="PAGE",
        scope_id="scope-running-fast-path",
        status="RUNNING",
        started_at=utcnow(),
    )
    db_session.add(run)
    db_session.flush()
    node_run = WorkflowNodeRun(
        workflow_run_id=run.id,
        node_id="work",
        node_type="agent.adapt",
        status="WAITING",
        output_refs={},
    )
    db_session.add(node_run)
    db_session.commit()
    run_id, node_run_id = run.id, node_run.id

    # The mid-pass RUNNING stamp pending in the session (reconciliation's
    # line-430 write): flushed only by the fast path's commit, never yet on
    # the connection.
    node_run.status = "RUNNING"
    node_run.started_at = utcnow()

    factory = _session_factory(db_session)
    fired: list[bool] = []
    real_refresh = db_session.refresh

    def _refresh_with_concurrent_cancel(obj, *args, **kwargs):
        refreshed = real_refresh(obj, *args, **kwargs)
        # Fire once, right after the final pre-commit status recheck (the
        # run-status refresh at reconciliation's line 443 — the fixture keeps
        # it the only refresh of the pass) reads RUNNING, so the cancel lands
        # exactly between that recheck and the fast-path commit.
        if (
            not fired
            and isinstance(obj, WorkflowRun)
            and obj.id == run_id
            and list(kwargs.get("attribute_names") or []) == ["status"]
        ):
            fired.append(True)
            _cancel_run_via_second_session(factory, run_id)
        return refreshed

    monkeypatch.setattr(db_session, "refresh", _refresh_with_concurrent_cancel)

    result = reconcile_run(db_session, run_id)

    assert fired, "the concurrent-cancel window was not constructed"
    assert result.status == "CANCELLED"
    returned_node = next(item for item in result.node_runs if item.id == node_run_id)
    assert returned_node.status == "CANCELLED"  # not resurrected to RUNNING
    db_session.expire_all()
    assert db_session.get(WorkflowRun, run_id).status == "CANCELLED"
    assert db_session.get(WorkflowNodeRun, node_run_id).status == "CANCELLED"
