from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from app.models import GenerationJob, ModelCallAttempt, Project
from app.services.workflow_engine import canonical_graph, default_graph, validate_graph
from app.services.workflow_presentation import run_read
from app.services.workflow_templates import studio_template
from app.workflow_schemas import WorkflowGraph
from pydantic import ValidationError


def test_group_metadata_roundtrip_and_execution_unchanged(client):
    project = client.post("/api/v1/projects", json={"name": "分组项目"}).json()
    graph = default_graph()
    graph["groups"] = [{
        "id": "drafting", "name": "剧情准备", "color": "#397b68", "notes": "输入与解析",
        "node_ids": ["chapter", "parse"], "collapsed": True,
    }]
    imported = client.post(f"/api/v1/projects/{project['id']}/workflows/import", json={
        "name": "折叠流程", "graph": graph,
    })
    assert imported.status_code == 201, imported.text
    workflow = imported.json()
    assert workflow["draft_graph"]["groups"] == graph["groups"]
    published = client.post(f"/api/v1/workflows/{workflow['id']}/publish")
    assert published.status_code == 200, published.text
    assert published.json()["graph"]["groups"] == graph["groups"]
    assert validate_graph(graph).topological_order == validate_graph(default_graph()).topological_order
    assert canonical_graph(graph)["edges"] == graph["edges"]


@pytest.mark.parametrize("invalid", ["unknown", "overlap", "duplicate", "collision"])
def test_reject_invalid_groups(invalid):
    graph = studio_template("batch")
    if invalid == "unknown":
        graph["groups"][0]["node_ids"].append("missing")
    elif invalid == "overlap":
        graph["groups"][1]["node_ids"].append("parse")
    elif invalid == "duplicate":
        graph["groups"].append(deepcopy(graph["groups"][0]))
    else:
        graph["groups"][0]["id"] = "parse"
    with pytest.raises(ValidationError):
        WorkflowGraph.model_validate(graph)


@pytest.mark.parametrize(("kind", "entry", "mode"), [
    ("check", "adopt", "single"), ("batch", "generate", "batch"),
])
def test_templates_are_valid_and_preserve_approval_barriers(client, kind, entry, mode):
    response = client.get(f"/api/v1/workflow-templates/{kind}")
    assert response.status_code == 200
    graph = response.json()
    assert validate_graph(graph).valid
    assert graph["entry_node_ids"] == [entry]
    assert graph["run_mode"] == mode
    assert next(node for node in graph["nodes"] if node["id"] == "generate")["config"][
        "requires_approval"
    ]
    assert client.get("/api/v1/workflow-templates/unknown").status_code == 422


def test_run_usage_isolates_time_window_and_unknown_attempts(db_session):
    project = Project(name="用量项目")
    db_session.add(project)
    db_session.flush()
    job = GenerationJob(project_id=project.id, target_type="WORKFLOW_NODE", target_id="n",
                        job_type="WORKFLOW_NODE", status="COMPLETED")
    db_session.add(job)
    db_session.flush()
    start = datetime(2026, 9, 21, 10, tzinfo=UTC)
    end = start + timedelta(minutes=1)
    for index, seconds in enumerate([-60, 10, 20, 120]):
        db_session.add(ModelCallAttempt(
            project_id=project.id, job_id=job.id, job_attempt=1, dispatch_no=index + 1,
            provider="test", model_id="test", started_at=start + timedelta(seconds=seconds),
            input_tokens=100, output_tokens=50,
        ))
    db_session.flush()
    node = SimpleNamespace(
        id="node-run", workflow_run_id="run", node_id="n", node_type="agent.parse",
        status="COMPLETED", job_id=job.id, input_snapshot={}, output_refs={}, attempt_count=1,
        started_at=start, finished_at=end, error_code=None, error_message=None,
    )
    run = SimpleNamespace(
        id="run", workflow_id="wf", workflow_version_id="version", project_id=project.id,
        scope_type="PAGE", scope_id="page", status="COMPLETED", start_node_ids=[],
        stop_node_ids=[], started_at=start, finished_at=end, error_code=None, error_message=None,
        created_at=start, updated_at=end, version=1, node_runs=[node],
    )
    assert run_read(db_session, run).node_runs[0].total_tokens == 300
    db_session.add(ModelCallAttempt(
        project_id=project.id, job_id=job.id, job_attempt=2, dispatch_no=1,
        provider="test", model_id="test", started_at=start + timedelta(seconds=30),
        input_tokens=None, output_tokens=None,
    ))
    db_session.flush()
    assert run_read(db_session, run).node_runs[0].total_tokens is None
    node.job_id = None
    assert run_read(db_session, run).node_runs[0].total_tokens is None
