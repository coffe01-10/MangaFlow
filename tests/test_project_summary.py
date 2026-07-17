from app.domain.states import JobStatus
from app.models import (
    Asset,
    Chapter,
    GenerationJob,
    MangaPage,
    Project,
    Scene,
    StyleProfile,
    StyleStatus,
    WorkflowDefinition,
)


def test_project_summary_reports_empty_project(client):
    created = client.post("/api/v1/projects", json={"name": "空项目"})
    assert created.status_code == 201

    response = client.get(f"/api/v1/projects/{created.json()['id']}/summary")

    assert response.status_code == 200
    summary = response.json()
    assert summary["chapter_count"] == 0
    assert summary["page_count"] == 0
    assert summary["asset_count"] == 0
    assert summary["section_statuses"]["source"] == "EMPTY"
    assert summary["section_statuses"]["workflow"] == "NOT_CONFIGURED"


def test_project_summary_aggregates_project_shell_state(client, db_session):
    created = client.post("/api/v1/projects", json={"name": "聚合项目"}).json()
    project_id = created["id"]
    chapter = Chapter(project_id=project_id, title="第一章", ordinal=1)
    db_session.add(chapter)
    db_session.flush()
    scene = Scene(chapter_id=chapter.id, ordinal=1)
    db_session.add(scene)
    db_session.flush()
    db_session.add_all(
        [
            MangaPage(
                chapter_id=chapter.id,
                page_number=1,
                scene_ids=[scene.id],
                beat_ids=["beat-1"],
                source_coverage={"complete": True},
            ),
            Asset(
                project_id=project_id,
                kind="STYLE_REFERENCE",
                original_name="style.png",
                storage_key="uploads/style.png",
                mime_type="image/png",
                byte_size=128,
                sha256="a" * 64,
            ),
            GenerationJob(
                project_id=project_id,
                target_type="CHAPTER",
                target_id=chapter.id,
                job_type="PARSE_SCRIPT",
                status=JobStatus.GENERATING,
            ),
            GenerationJob(
                project_id=project_id,
                target_type="CHAPTER",
                target_id=chapter.id,
                job_type="PARSE_SCRIPT",
                status=JobStatus.FAILED,
            ),
            WorkflowDefinition(project_id=project_id, name="生产流程"),
        ]
    )
    style = StyleProfile(
        project_id=project_id,
        name="黑白网点",
        status=StyleStatus.ACTIVE,
    )
    db_session.add(style)
    db_session.flush()
    project = db_session.get(Project, project_id)
    assert project is not None
    project.default_style_id = style.id
    db_session.commit()

    response = client.get(f"/api/v1/projects/{project_id}/summary")

    assert response.status_code == 200
    summary = response.json()
    assert summary["chapter_count"] == 1
    assert summary["page_count"] == 1
    assert summary["asset_count"] == 1
    assert summary["pending_job_count"] == 1
    assert summary["failed_job_count"] == 1
    assert summary["active_style_name"] == "黑白网点"
    assert summary["active_workflow_status"] == "DRAFT"
    assert summary["section_statuses"] == {
        "source": "READY",
        "assets": "READY",
        "script": "READY",
        "storyboard": "READY",
        "generate": "RUNNING",
        "library": "READY",
        "jobs": "FAILED",
        "workflow": "DRAFT",
    }


def test_project_summary_marks_legacy_pages_for_review(client, db_session):
    created = client.post("/api/v1/projects", json={"name": "旧分页项目"}).json()
    chapter = Chapter(project_id=created["id"], title="第一章", ordinal=1)
    db_session.add(chapter)
    db_session.flush()
    db_session.add(
        MangaPage(
            chapter_id=chapter.id,
            page_number=1,
            scene_ids=[],
            beat_ids=[],
            source_coverage={"complete": True},
        )
    )
    db_session.commit()

    response = client.get(f"/api/v1/projects/{created['id']}/summary")

    assert response.status_code == 200
    statuses = response.json()["section_statuses"]
    assert statuses["storyboard"] == "NEEDS_REVIEW"
    assert statuses["generate"] == "NEEDS_REVIEW"


def test_project_summary_hides_archived_projects(client):
    created = client.post("/api/v1/projects", json={"name": "归档项目"}).json()
    mismatch = client.delete(
        f"/api/v1/projects/{created['id']}", params={"confirm_name": "错误名称"}
    )
    assert mismatch.status_code == 409
    assert client.delete(
        f"/api/v1/projects/{created['id']}", params={"confirm_name": "归档项目"}
    ).status_code == 204

    response = client.get(f"/api/v1/projects/{created['id']}/summary")

    assert response.status_code == 404
