"""Regression: archiving a project cancels jobless workflow runs.

Archive used to cancel runs only transitively through active jobs: a run
parked at an approval barrier owns no active job, so it survived the archive
as PAUSED/RUNNING — and the run-scoped approve/retry routes (which do not
re-check project liveness) could then mint paid work on the archived
project. Archive now cancels every non-terminal run directly, and the run
helper fails closed on a soft-deleted project.
"""

from app.models import (
    Project,
    WorkflowDefinition,
    WorkflowRun,
    WorkflowVersion,
)
from app.services.workflow_engine.lifecycle import cancel_run


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
