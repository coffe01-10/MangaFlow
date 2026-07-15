from copy import deepcopy

from app.config import get_settings
from app.models import Chapter, MangaPage, PageCandidate, WorkflowVersion
from app.services.workflow_engine import default_graph, validate_graph


def _project(client):
    response = client.post("/api/v1/projects", json={"name": "工作流项目"})
    assert response.status_code == 201
    return response.json()


def _workflow(client, project_id: str):
    response = client.post(
        f"/api/v1/projects/{project_id}/workflows",
        json={"name": "漫画生产线", "template": "manga_default"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_default_graph_is_strict_and_valid():
    graph = default_graph()
    report = validate_graph(graph)
    assert report.valid is True
    assert report.topological_order[0] in {"chapter", "assets"}
    assert report.topological_order[-1] == "export"

    invalid = deepcopy(graph)
    invalid["edges"][0]["target_port"] = "missing"
    report = validate_graph(invalid)
    assert report.valid is False
    assert {item.code for item in report.issues} >= {"UNKNOWN_PORT", "MISSING_REQUIRED_INPUT"}

    cyclic = deepcopy(graph)
    cyclic["edges"].append(
        {
            "id": "cycle",
            "source_node": "inspect",
            "source_port": "report",
            "target_node": "parse",
            "target_port": "source",
        }
    )
    report = validate_graph(cyclic)
    assert report.valid is False
    assert {item.code for item in report.issues} >= {"PORT_TYPE_MISMATCH", "CYCLE_DETECTED"}


def test_workflow_crud_optimistic_lock_publish_restore_and_json(client, db_session):
    project = _project(client)
    workflow = _workflow(client, project["id"])

    listed = client.get(f"/api/v1/projects/{project['id']}/workflows")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [workflow["id"]]

    stale = client.patch(
        f"/api/v1/workflows/{workflow['id']}",
        json={"version": workflow["version"] + 1, "name": "冲突名称"},
    )
    assert stale.status_code == 409

    graph = workflow["draft_graph"]
    graph["nodes"][0]["position"] = {"x": 88, "y": 99}
    updated = client.patch(
        f"/api/v1/workflows/{workflow['id']}",
        json={"version": workflow["version"], "draft_graph": graph},
    )
    assert updated.status_code == 200, updated.text
    updated_workflow = updated.json()
    assert updated_workflow["draft_version"] == workflow["draft_version"] + 1

    validated = client.post(f"/api/v1/workflows/{workflow['id']}/validate")
    assert validated.status_code == 200
    assert validated.json()["valid"] is True

    published = client.post(f"/api/v1/workflows/{workflow['id']}/publish")
    assert published.status_code == 200, published.text
    immutable_graph = deepcopy(published.json()["graph"])
    version_id = published.json()["id"]

    current = client.get(f"/api/v1/workflows/{workflow['id']}").json()
    changed = deepcopy(current["draft_graph"])
    changed["nodes"][0]["name"] = "后来修改"
    saved = client.patch(
        f"/api/v1/workflows/{workflow['id']}",
        json={"version": current["version"], "draft_graph": changed},
    )
    assert saved.status_code == 200
    assert db_session.get(WorkflowVersion, version_id).graph == immutable_graph

    restored = client.post(
        f"/api/v1/workflow-versions/{version_id}/restore",
        json={"version": saved.json()["version"]},
    )
    assert restored.status_code == 200
    assert restored.json()["draft_graph"] == immutable_graph

    exported = client.get(f"/api/v1/workflows/{workflow['id']}/export")
    assert exported.status_code == 200
    assert exported.json()["schema"] == "mangaflow.workflow.v2"

    imported = client.post(
        f"/api/v1/projects/{project['id']}/workflows/import",
        json={
            "name": "复制生产线",
            "description": "JSON 导入",
            "graph": exported.json()["graph"],
        },
    )
    assert imported.status_code == 201, imported.text


def test_run_pauses_before_single_page_generation_and_reuses_jobs(client, db_session):
    project = _project(client)
    workflow = _workflow(client, project["id"])
    published = client.post(f"/api/v1/workflows/{workflow['id']}/publish")
    assert published.status_code == 200

    chapter = Chapter(project_id=project["id"], title="第一章", ordinal=1, status="PAGES_PLANNED")
    db_session.add(chapter)
    db_session.flush()
    page = MangaPage(
        chapter_id=chapter.id,
        page_number=1,
        panel_count=4,
        scene_ids=["scene"],
        beat_ids=["beat"],
        source_coverage={"complete": True},
    )
    db_session.add(page)
    db_session.commit()

    settings = get_settings()
    previous_queue = settings.queue_enabled
    settings.queue_enabled = False
    try:
        response = client.post(
            f"/api/v1/workflows/{workflow['id']}/runs",
            json={
                "scope_type": "PAGE",
                "scope_id": page.id,
                "start_node_ids": ["generate"],
                "stop_node_ids": ["generate"],
            },
        )
        assert response.status_code == 202, response.text
        run = response.json()
        assert run["status"] == "PAUSED"
        generate = next(item for item in run["node_runs"] if item["node_id"] == "generate")
        assert generate["status"] == "WAITING_APPROVAL"
        assert generate["job_id"] is None

        approved = client.post(
            f"/api/v1/workflow-runs/{run['id']}/nodes/generate/approve",
            json={},
        )
        assert approved.status_code == 200, approved.text
        generate = approved.json()["node_runs"][0]
        assert generate["status"] == "RUNNING"
        assert generate["job_id"]
        candidate = db_session.get(PageCandidate, generate["output_refs"]["candidate_id"])
        assert candidate is not None
        assert candidate.ordinal == 1

        duplicate = client.post(
            f"/api/v1/workflow-runs/{run['id']}/nodes/generate/approve",
            json={},
        )
        assert duplicate.status_code == 409
        assert db_session.query(PageCandidate).count() == 1
    finally:
        settings.queue_enabled = previous_queue


def test_node_catalog_and_soft_delete(client):
    catalog = client.get("/api/v1/workflow-node-types")
    assert catalog.status_code == 200
    types = {item["type"] for item in catalog.json()}
    assert {"source.chapter", "generator.page", "control.approval", "output.export"} <= types

    project = _project(client)
    workflow = _workflow(client, project["id"])
    deleted = client.delete(f"/api/v1/workflows/{workflow['id']}")
    assert deleted.status_code == 204
    assert client.get(f"/api/v1/workflows/{workflow['id']}").status_code == 404
