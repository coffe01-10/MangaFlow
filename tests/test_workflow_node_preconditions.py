"""Deterministic workflow-node failures must be non-retryable and actionable.

The engine raises WorkflowNodeExecutionError for precondition failures
(missing scope/input, page not production-ready, unsupported node): they
cannot change outcome on retry, and their messages state the blocking
condition in user terms. They used to fall into worker_tasks' unclassified
branch — retried max_attempts times and masked as「未分类异常」. Also pins
the node_run.attempt_count mirror of job-level scheduling attempts.
"""

import pytest
from app.models import GenerationJob, Project, WorkflowNodeRun
from app.services.workflow_engine.catalog import _edge, _node
from app.services.workflow_engine.execution import (
    WorkflowNodeExecutionError,
    execute_workflow_node,
)
from app.services.workflow_engine.validation import validate_graph

from tests.test_condition_branch_skip import _node_job, _node_run, _seed_graph_run


@pytest.fixture
def condition_chain(db_session):
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
                condition={"path": "$.value", "operator": "eq", "value": True},
            ),
        ],
        "edges": [
            _edge("src", "source", "parse", "source"),
            _edge("parse", "story", "cond", "value"),
        ],
    }
    report = validate_graph(graph)
    assert report.valid, [issue.message for issue in report.issues]
    project, run = _seed_graph_run(db_session, "执行前置条件", graph)
    _node_run(
        db_session,
        run,
        "src",
        "source.chapter",
        status="COMPLETED",
        output_refs={"kind": "source"},
    )
    _node_run(
        db_session,
        run,
        "parse",
        "agent.parse",
        status="COMPLETED",
        output_refs={"value": True},
    )
    return project, run


def test_node_attempt_count_mirrors_job_attempts(db_session, condition_chain):
    project, run = condition_chain
    cond = _node_run(db_session, run, "cond", "control.condition")
    job = _node_job(db_session, project, run, cond)
    # A reclaimed/retried job re-claims with attempt_count + 1; the node's
    # exposed count must follow instead of staying pinned at planning-time 1.
    job.attempt_count = 3
    db_session.commit()

    execute_workflow_node(db_session, job)

    db_session.expire_all()
    node_run = db_session.get(WorkflowNodeRun, cond.id)
    assert node_run.attempt_count == 3
    assert node_run.status == "RUNNING"
    assert node_run.output_refs["matched"] is True
    assert node_run.output_refs["selected_port"] == "true"


def test_output_page_without_page_scope_is_non_retryable(db_session, condition_chain):
    project, run = condition_chain
    # The fixture's run is CHAPTER-scoped and its graph never validated an
    # output.page; execute_workflow_node reads only the run's scope for this
    # branch — a page-less scope is a deterministic precondition failure.
    node_run = _node_run(db_session, run, "out", "output.page")
    job = _node_job(db_session, project, run, node_run)
    db_session.commit()

    with pytest.raises(WorkflowNodeExecutionError) as excinfo:
        execute_workflow_node(db_session, job)

    assert excinfo.value.error_code == "NODE_PRECONDITION_FAILED"
    assert "单页成品节点需要页面运行范围" in str(excinfo.value)


def test_worker_classifies_node_preconditions_as_non_retryable(tmp_path, monkeypatch):
    """The worker branch keeps the actionable message and never retries.

    Mirrors test_worker_error_sanitized's isolated-DB harness: the engine's
    deterministic failure must land as FAILED on attempt 1 with its own error
    code and the actionable message (which page is not ready), not retry
    max_attempts times behind the sanitized WORKER_ERROR text.
    """
    from app import worker_tasks
    from app.database import Base
    from app.services import workflow_engine
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(f"sqlite:///{(tmp_path / 'worker.db').as_posix()}")
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(worker_tasks, "SessionLocal", testing_session)

    actionable = "PAGE_NOT_PRODUCTION_READY: 第 3 页尚未达到生产通过状态"

    def refusing_handler(_db, _job):
        raise WorkflowNodeExecutionError(actionable)

    monkeypatch.setattr(workflow_engine, "execute_workflow_node", refusing_handler)

    with testing_session() as db:
        project = Project(name="node-precondition-worker")
        db.add(project)
        db.flush()
        job = GenerationJob(
            project_id=project.id,
            target_type="WORKFLOW_NODE",
            target_id="node-run-1",
            job_type="WORKFLOW_NODE",
            status="QUEUED",
            max_attempts=3,
        )
        db.add(job)
        db.commit()
        job_id = job.id

    with pytest.raises(WorkflowNodeExecutionError, match="第 3 页"):
        worker_tasks.execute_job(job_id)

    with testing_session() as db:
        loaded = db.get(GenerationJob, job_id)
        # Non-retryable: terminal FAILED on the first attempt, not requeued.
        assert loaded.status.value == "FAILED"
        assert loaded.attempt_count == 1
        assert loaded.error_code == "NODE_PRECONDITION_FAILED"
        assert loaded.error_message == actionable
