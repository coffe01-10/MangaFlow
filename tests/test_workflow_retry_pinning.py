"""retry_run must re-execute the failed run's pinned workflow version (#153),
DELETE /workflows/{id} must refuse while runs are still live, and retry must
refuse once the definition is soft-deleted (#139).

The chapter-scoped source.chapter → agent.parse graph keeps everything
offline: with the queue disabled the minted SOURCE_PARSE job stays WAITING,
so no paid provider call ever runs. FAILED/CANCELLED runs are seeded directly
(the run-guard regression style) because a source-only run would
reconcile-complete instantly.
"""

from copy import deepcopy

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models import (
    Chapter,
    GenerationJob,
    Project,
    SourceRevision,
    WorkflowDefinition,
    WorkflowRun,
    utcnow,
)
from app.services.workflow_engine import (
    create_workflow_run,
    default_graph,
    publish_workflow,
    retry_run,
)


def _chapter_with_revision(db) -> Chapter:
    project = Project(name="重试固定版本")
    db.add(project)
    db.flush()
    chapter = Chapter(project_id=project.id, title="第一章", ordinal=1)
    db.add(chapter)
    db.flush()
    revision = SourceRevision(
        chapter_id=chapter.id,
        revision=1,
        source_type="PASTE",
        original_text="顾川推开门。",
        sha256="a" * 64,
        character_count=6,
    )
    db.add(revision)
    db.flush()
    chapter.current_source_revision_id = revision.id
    return chapter


def _parse_workflow(db, project_id, *, name="章节解析流程") -> WorkflowDefinition:
    graph = default_graph()
    trimmed = {
        "schema_version": graph.get("schema_version", 2),
        "nodes": [node for node in graph["nodes"] if node["id"] in {"chapter", "parse"}],
        "edges": [
            edge
            for edge in graph["edges"]
            if edge["source_node"] == "chapter" and edge["target_node"] == "parse"
        ],
    }
    workflow = WorkflowDefinition(project_id=project_id, name=name, draft_graph=trimmed)
    db.add(workflow)
    db.commit()
    publish_workflow(db, workflow)
    db.refresh(workflow)
    return workflow


def _failed_run(db, workflow: WorkflowDefinition, chapter: Chapter) -> WorkflowRun:
    run = WorkflowRun(
        workflow_id=workflow.id,
        workflow_version_id=workflow.published_version_id,
        project_id=workflow.project_id,
        scope_type="CHAPTER",
        scope_id=chapter.id,
        status="FAILED",
        start_node_ids=["parse"],
        stop_node_ids=[],
        started_at=utcnow(),
        finished_at=utcnow(),
    )
    db.add(run)
    db.commit()
    return run


def _republish_changed_parse_prompt(db, workflow: WorkflowDefinition) -> None:
    draft = deepcopy(workflow.draft_graph)
    parse_node = next(node for node in draft["nodes"] if node["id"] == "parse")
    parse_node["config"]["prompt_template"] = "v2 提示词"
    workflow.draft_graph = draft
    db.commit()
    publish_workflow(db, workflow)
    db.refresh(workflow)


def _parse_job_ids(db, chapter_id: str) -> list[str]:
    return list(
        db.scalars(
            select(GenerationJob.id).where(
                GenerationJob.job_type == "SOURCE_PARSE",
                GenerationJob.target_id == chapter_id,
            )
        )
    )


def test_retry_clones_failed_run_on_its_pinned_version(db_session, monkeypatch):
    """#153 (failing-first): republish a changed payload after the run failed;
    retry must execute the failed run's version, not the fresh revision."""
    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    chapter = _chapter_with_revision(db_session)
    workflow = _parse_workflow(db_session, chapter.project_id)
    v1_id = workflow.published_version_id
    failed = _failed_run(db_session, workflow, chapter)

    _republish_changed_parse_prompt(db_session, workflow)
    v2_id = workflow.published_version_id
    assert v2_id != v1_id

    retried = retry_run(db_session, failed)
    assert retried.workflow_version_id == v1_id
    assert retried.id != failed.id
    assert _parse_job_ids(db_session, chapter.id)


def test_retry_survives_republish_removing_the_start_node(db_session, monkeypatch):
    """#153 shape (a) (failing-first): when v2 drops the old start node the
    unpinned retry 409s forever (「运行范围包含不存在的节点」); the pinned
    retry keeps executing the failed run's own graph."""
    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    chapter = _chapter_with_revision(db_session)
    workflow = _parse_workflow(db_session, chapter.project_id)
    v1_id = workflow.published_version_id
    failed = _failed_run(db_session, workflow, chapter)

    draft = deepcopy(workflow.draft_graph)
    draft["nodes"] = [node for node in draft["nodes"] if node["id"] == "chapter"]
    draft["edges"] = []
    workflow.draft_graph = draft
    db_session.commit()
    publish_workflow(db_session, workflow)
    db_session.refresh(workflow)
    assert workflow.published_version_id != v1_id

    retried = retry_run(db_session, failed)
    assert retried.workflow_version_id == v1_id
    assert _parse_job_ids(db_session, chapter.id)


def test_fresh_start_still_resolves_the_latest_published_version(
    db_session, monkeypatch
):
    """#153 guard rail: only retry pins; a fresh start keeps using the current
    published version."""
    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    chapter = _chapter_with_revision(db_session)
    workflow = _parse_workflow(db_session, chapter.project_id)
    v1_id = workflow.published_version_id
    _republish_changed_parse_prompt(db_session, workflow)
    v2_id = workflow.published_version_id
    assert v2_id != v1_id

    started = create_workflow_run(
        db_session,
        workflow,
        scope_type="CHAPTER",
        scope_id=chapter.id,
        start_node_ids=["parse"],
        stop_node_ids=[],
    )
    assert started.workflow_version_id == v2_id


def test_retry_route_pins_version_after_republish(client, db_session, monkeypatch):
    """#153 through the route: POST /workflow-runs/{id}/retry answers 202 with
    the pinned version id."""
    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    chapter = _chapter_with_revision(db_session)
    workflow = _parse_workflow(db_session, chapter.project_id)
    v1_id = workflow.published_version_id
    failed = _failed_run(db_session, workflow, chapter)
    _republish_changed_parse_prompt(db_session, workflow)

    response = client.post(f"/api/v1/workflow-runs/{failed.id}/retry")
    assert response.status_code == 202, response.text
    assert response.json()["workflow_version_id"] == v1_id


def test_retry_run_refuses_after_workflow_soft_delete(db_session, monkeypatch):
    """#139 (failing-first): the start route blocks soft-deleted definitions
    via _workflow; retry used to bypass that and resurrect runs."""
    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    chapter = _chapter_with_revision(db_session)
    workflow = _parse_workflow(db_session, chapter.project_id)
    failed = _failed_run(db_session, workflow, chapter)
    workflow.deleted_at = utcnow()
    db_session.commit()

    with pytest.raises(ValueError, match="工作流不存在或已删除"):
        retry_run(db_session, failed)
    db_session.rollback()
    assert (
        db_session.scalars(
            select(WorkflowRun).where(WorkflowRun.workflow_id == workflow.id)
        ).all()
        == [failed]
    )


def _published_workflow_with_version(client, project_id: str) -> tuple[dict, str]:
    created = client.post(
        f"/api/v1/projects/{project_id}/workflows",
        json={"name": "删除防护线", "template": "manga_default"},
    )
    assert created.status_code == 201, created.text
    workflow = created.json()
    published = client.post(f"/api/v1/workflows/{workflow['id']}/publish")
    assert published.status_code == 200, published.text
    return workflow, published.json()["id"]


@pytest.mark.parametrize("live_status", ["RUNNING", "PAUSED"])
def test_delete_workflow_refuses_while_run_is_live(
    client, db_session, live_status
):
    """#139 (failing-first): soft-deleting a definition with a non-terminal run
    orphaned a live run whose paid jobs kept executing; delete must 409."""
    project = client.post("/api/v1/projects", json={"name": "活跃运行禁止删除"}).json()
    workflow, version_id = _published_workflow_with_version(client, project["id"])
    db_session.add(
        WorkflowRun(
            workflow_id=workflow["id"],
            workflow_version_id=version_id,
            project_id=project["id"],
            scope_type="PROJECT",
            scope_id=project["id"],
            status=live_status,
            started_at=utcnow(),
        )
    )
    db_session.commit()

    response = client.delete(f"/api/v1/workflows/{workflow['id']}")
    assert response.status_code == 409
    assert "工作流仍有进行中的运行" in response.json()["detail"]
    db_session.expire_all()
    assert db_session.get(WorkflowDefinition, workflow["id"]).deleted_at is None


@pytest.mark.parametrize("terminal", ["COMPLETED", "CANCELLED", "FAILED"])
def test_delete_workflow_allows_terminal_runs(client, db_session, terminal):
    """#139 guard rail: terminal runs never block the delete."""
    project = client.post("/api/v1/projects", json={"name": "终态运行可删除"}).json()
    workflow, version_id = _published_workflow_with_version(client, project["id"])
    db_session.add(
        WorkflowRun(
            workflow_id=workflow["id"],
            workflow_version_id=version_id,
            project_id=project["id"],
            scope_type="PROJECT",
            scope_id=project["id"],
            status=terminal,
            started_at=utcnow(),
            finished_at=utcnow(),
        )
    )
    db_session.commit()

    response = client.delete(f"/api/v1/workflows/{workflow['id']}")
    assert response.status_code == 204, response.text
    db_session.expire_all()
    assert db_session.get(WorkflowDefinition, workflow["id"]).deleted_at is not None
