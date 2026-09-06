"""retry_run + old-run job retry must not yield two ACTIVE runs on one scope.

retry_run clones a fresh RUNNING run from a FAILED one and leaves the FAILED
run's kept cause-of-failure job in the jobs list with a live Retry button.
Retrying that job revived the old run to a second RUNNING run on the same
(workflow, scope) — two concurrent paid PAGE_GENERATE workflows — because the
FAILED-run revival branch of reset_for_retry never checked for a sibling
non-terminal run. SOURCE_PARSE is already covered by the job-type mutex;
workflow PAGE_GENERATE jobs are not.
"""

import pytest
from fastapi import HTTPException

from app.domain.states import JobStatus
from app.models import (
    AppSetting,
    Chapter,
    GenerationJob,
    Project,
    WorkflowDefinition,
    WorkflowNodeRun,
    WorkflowRun,
    WorkflowVersion,
)
from app.services import job_service


def _set_queue_mode(db, mode: str) -> None:
    db.add(AppSetting(key="runtime", value={"queue_mode": mode}, version=1))


def _seed_failed_run_with_job(db):
    project = Project(name="重试兄弟运行")
    db.add(project)
    db.flush()
    chapter = Chapter(project_id=project.id, title="第一章", ordinal=1)
    db.add(chapter)
    db.flush()
    graph = {
        "schema_version": 2,
        "nodes": [
            {
                "id": "page",
                "type": "generator.page",
                "name": "单页生成",
                "x": 0,
                "y": 0,
                "config": {"model_alias": None, "resolution": "1K"},
            }
        ],
        "edges": [],
    }
    workflow = WorkflowDefinition(
        project_id=project.id, name="重试兄弟工作流", draft_graph=graph
    )
    db.add(workflow)
    db.flush()
    version = WorkflowVersion(
        workflow_id=workflow.id,
        revision=1,
        graph=graph,
        graph_checksum="sibling-run-guard",
    )
    db.add(version)
    db.flush()
    workflow.published_version_id = version.id
    run = WorkflowRun(
        workflow_id=workflow.id,
        workflow_version_id=version.id,
        project_id=project.id,
        scope_type="CHAPTER",
        scope_id=chapter.id,
        status="FAILED",
        start_node_ids=["page"],
        stop_node_ids=[],
        started_at=utcnow_stub(),
        finished_at=utcnow_stub(),
    )
    db.add(run)
    db.flush()
    node_run = WorkflowNodeRun(
        workflow_run_id=run.id,
        node_id="page",
        node_type="generator.page",
        status="FAILED",
    )
    db.add(node_run)
    db.flush()
    job = GenerationJob(
        project_id=project.id,
        target_type="WORKFLOW_NODE",
        target_id=node_run.id,
        job_type="PAGE_GENERATE",
        status=JobStatus.FAILED,
        error_code="UPSTREAM",
        request_parameters={"workflow_run_id": run.id},
    )
    db.add(job)
    db.flush()
    node_run.job_id = job.id
    db.commit()
    return chapter, workflow, run, job


def utcnow_stub():
    from app.models import utcnow

    return utcnow()


def test_reset_for_retry_refuses_when_sibling_run_is_active(db_session):
    chapter, workflow, failed_run, job = _seed_failed_run_with_job(db_session)

    # The user retries the failed run: a fresh RUNNING run R2 on the scope
    # (retry_run's clone semantics are pinned by test_workflow_retry_pinning;
    # seeded directly here so this test isolates the revival guard).
    retried = WorkflowRun(
        workflow_id=workflow.id,
        workflow_version_id=workflow.published_version_id,
        project_id=workflow.project_id,
        scope_type="CHAPTER",
        scope_id=chapter.id,
        status="RUNNING",
        started_at=utcnow_stub(),
    )
    db_session.add(retried)
    db_session.commit()

    # The old run's FAILED job still offers Retry; the revival must refuse
    # while R2 is the scope's active run.
    with pytest.raises(HTTPException) as exc_info:
        job_service.reset_for_retry(db_session, job)
    assert exc_info.value.status_code == 409

    db_session.expire_all()
    assert db_session.get(WorkflowRun, failed_run.id).status == "FAILED"
    assert db_session.get(GenerationJob, job.id).status == JobStatus.FAILED
    assert db_session.get(WorkflowRun, retried.id).status == "RUNNING"
