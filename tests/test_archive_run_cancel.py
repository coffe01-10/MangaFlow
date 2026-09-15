"""Regression: archiving a project cancels jobless workflow runs.

Archive used to cancel runs only transitively through active jobs: a run
parked at an approval barrier owns no active job, so it survived the archive
as PAUSED/RUNNING — and the run-scoped approve/retry routes (which do not
re-check project liveness) could then mint paid work on the archived
project. Archive now cancels every non-terminal run directly, and the run
helper fails closed on a soft-deleted project.
"""

import pytest
from app.models import (
    GenerationJob,
    Project,
    WorkflowDefinition,
    WorkflowRun,
    WorkflowVersion,
)
from app.services.workflow_engine.lifecycle import cancel_run
from fastapi import HTTPException
from sqlalchemy import event


def _paused_run(db, name: str) -> tuple[Project, WorkflowRun]:
    project = Project(name=name)
    db.add(project)
    db.flush()
    workflow = WorkflowDefinition(
        project_id=project.id,
        name=name,
        draft_graph={"nodes": [], "edges": []},
        is_active=True,
    )
    db.add(workflow)
    db.flush()
    version = WorkflowVersion(
        workflow_id=workflow.id,
        revision=1,
        graph={"nodes": [], "edges": []},
        graph_checksum="0" * 64,
    )
    db.add(version)
    db.flush()
    run = WorkflowRun(
        workflow_id=workflow.id,
        workflow_version_id=version.id,
        project_id=project.id,
        status="PAUSED",
    )
    db.add(run)
    db.commit()
    return project, run


def test_archive_cancels_jobless_paused_run(client, db_session):
    project, run = _paused_run(db_session, "归档取消暂停运行")

    response = client.delete(
        f"/api/v1/projects/{project.id}", params={"confirm_name": project.name}
    )
    assert response.status_code == 204

    db_session.expire_all()
    archived_run = db_session.get(WorkflowRun, run.id)
    assert archived_run.status == "CANCELLED"
    assert archived_run.finished_at is not None


def test_run_routes_fail_closed_on_archived_project(client, db_session):
    project, run = _paused_run(db_session, "归档后路由")

    response = client.delete(
        f"/api/v1/projects/{project.id}", params={"confirm_name": project.name}
    )
    assert response.status_code == 204

    response = client.get(f"/api/v1/workflow-runs/{run.id}")
    assert response.status_code == 404

    response = client.post(f"/api/v1/workflow-runs/{run.id}/cancel")
    assert response.status_code == 404


def test_cancel_run_still_works_for_live_projects(db_session):
    project, run = _paused_run(db_session, "活项目取消")

    cancel_run(db_session, run)
    db_session.expire_all()
    assert db_session.get(WorkflowRun, run.id).status == "CANCELLED"


def test_archive_commits_run_and_standalone_job_sweeps_only_once(db_session):
    from app.api.routes.projects import archive_project

    project, run = _paused_run(db_session, "单事务归档")
    job = GenerationJob(
        project_id=project.id,
        target_type="PROJECT",
        target_id=project.id,
        job_type="SOURCE_PARSE",
        status="WAITING",
    )
    db_session.add(job)
    db_session.commit()
    commits = []

    def committed(_session):
        commits.append(1)

    event.listen(db_session, "after_commit", committed)
    try:
        archive_project(project.id, project.name, db_session)
    finally:
        event.remove(db_session, "after_commit", committed)
    assert commits == [1]
    db_session.expire_all()
    assert db_session.get(WorkflowRun, run.id).status == "CANCELLED"
    assert db_session.get(GenerationJob, job.id).status.value == "CANCELLED"
    with pytest.raises(HTTPException) as raised:
        archive_project(project.id, project.name, db_session)
    assert raised.value.status_code == 404


def test_archive_rolls_back_every_sweep_if_final_job_fails(db_session, monkeypatch):
    from app.api.routes import projects

    project, run = _paused_run(db_session, "归档失败原子回滚")
    job = GenerationJob(
        project_id=project.id,
        target_type="PROJECT",
        target_id=project.id,
        job_type="SOURCE_PARSE",
        status="WAITING",
    )
    db_session.add(job)
    db_session.commit()

    def fail(*_args):
        raise RuntimeError("injected sweep failure")

    monkeypatch.setattr(projects, "mark_job_cancelled", fail)
    with pytest.raises(RuntimeError, match="injected"):
        projects.archive_project(project.id, project.name, db_session)
    db_session.rollback()
    assert db_session.get(Project, project.id).deleted_at is None
    assert db_session.get(WorkflowRun, run.id).status == "PAUSED"


def test_archived_project_rejects_start_and_job_retry(db_session):
    from app.models import utcnow
    from app.services.job_service import reset_for_retry
    from app.services.workflow_engine.planning import create_workflow_run

    project, run = _paused_run(db_session, "归档入口守卫")
    workflow = db_session.get(WorkflowDefinition, run.workflow_id)
    project.deleted_at = utcnow()
    job = GenerationJob(
        project_id=project.id,
        target_type="PROJECT",
        target_id=project.id,
        job_type="SOURCE_PARSE",
        status="FAILED",
    )
    db_session.add(job)
    db_session.commit()
    with pytest.raises(ValueError, match="已归档"):
        create_workflow_run(
            db_session,
            workflow,
            scope_type="PROJECT",
            scope_id=None,
            start_node_ids=[],
            stop_node_ids=[],
        )
    with pytest.raises(HTTPException, match="已归档"):
        reset_for_retry(db_session, job)
    assert job.status == "FAILED"
