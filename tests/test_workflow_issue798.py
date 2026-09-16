"""#798 workflow engine semantics: exists/null and requires_approval.

exists must treat a present JSON null as present, and a missing path as
absent. config.requires_approval is a template hint: it cannot turn off a
type barrier, and it cannot pause a non-barrier node in place of its work.
"""

from app.config import get_settings
from app.models import (
    Project,
    WorkflowDefinition,
    WorkflowNodeRun,
    WorkflowRun,
    WorkflowVersion,
    utcnow,
)
from app.services.workflow_engine import reconcile_run
from app.services.workflow_engine.catalog import _node, graph_checksum
from app.services.workflow_engine.execution import (
    MISSING,
    _condition_matches,
    _condition_value,
)

from tests.test_condition_branch_skip import _node_run


def _seed_unvalidated(db, name: str, graph: dict):
    """Seed a run without publish-time connectivity checks so a single node
    can exercise runtime barrier vs config.requires_approval (#798)."""
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


def test_exists_treats_json_null_as_present():
    payload = {"ready": None, "nested": {"flag": None}}
    assert _condition_value(payload, "$.ready") is None
    assert _condition_matches(None, "exists", None) is True
    assert _condition_value(payload, "$.nested.flag") is None
    assert _condition_matches(None, "exists", None) is True


def test_missing_path_is_not_json_null():
    payload = {"ready": None}
    assert _condition_value(payload, "$.absent") is MISSING
    assert _condition_value({}, "$.ready") is MISSING
    assert _condition_value(payload, "$.ready.nested") is MISSING
    assert _condition_matches(MISSING, "exists", None) is False
    assert _condition_matches(MISSING, "eq", None) is False
    assert _condition_matches(None, "eq", None) is True
    assert _condition_matches(MISSING, "ne", None) is True


def test_generator_barrier_ignores_requires_approval_false(db_session, monkeypatch):
    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    graph = {
        "schema_version": 2,
        "nodes": [
            _node(
                "generate",
                "generator.page",
                "单页生成",
                0,
                0,
                model_alias=None,
                resolution="1K",
                requires_approval=False,
            ),
        ],
        "edges": [],
    }
    _project, run = _seed_unvalidated(db_session, "issue798-barrier", graph)
    _node_run(db_session, run, "generate", "generator.page")
    db_session.commit()
    snapshot = reconcile_run(db_session, run.id)
    generate = next(item for item in snapshot.node_runs if item.node_id == "generate")
    assert generate.status == "WAITING_APPROVAL"
    assert snapshot.status == "PAUSED"


def test_requires_approval_does_not_skip_non_barrier_work(db_session, monkeypatch):
    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    graph = {
        "schema_version": 2,
        "nodes": [
            _node("parse", "agent.parse", "解析", 0, 0, model_alias="auto", requires_approval=True),
        ],
        "edges": [],
    }
    _project, run = _seed_unvalidated(db_session, "issue798-hint", graph)
    item = _node_run(db_session, run, "parse", "agent.parse")
    db_session.commit()
    snapshot = reconcile_run(db_session, run.id)
    parse = next(row for row in snapshot.node_runs if row.node_id == "parse")
    assert parse.id == item.id
    assert parse.status != "WAITING_APPROVAL"
    persisted = db_session.get(WorkflowNodeRun, item.id)
    assert persisted is not None
    assert persisted.status != "WAITING_APPROVAL"
