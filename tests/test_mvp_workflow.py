from app.config import get_settings
from pathlib import Path
from tempfile import TemporaryDirectory

from app.domain.states import JobStatus, Resolution
from app.models import (
    Asset,
    Dialogue,
    GenerationBatch,
    GenerationJob,
    MangaPage,
    PageCandidate,
    Panel,
)


def _project(client, name="长篇测试"):
    return client.post("/api/v1/projects", json={"name": name}).json()


def _chapter_and_pages(client, project_id: str, repeat: int = 12):
    paragraph = "苏清白推开教室的门。她看见窗边的顾川，轻声问道：“你怎么还在这里？”"
    imported = client.post(
        f"/api/v1/projects/{project_id}/sources/import",
        json={"title": "第一章", "text": "\n\n".join([paragraph] * repeat)},
    )
    assert imported.status_code == 201
    chapter = imported.json()["chapters"][0]
    planned = client.post(
        f"/api/v1/chapters/{chapter['id']}/plan",
        json={"replace_existing": True},
    )
    assert planned.status_code == 200
    return chapter, planned.json()


def test_lossless_import_and_dynamic_pagination(client):
    project = _project(client)
    short_chapter, short = _chapter_and_pages(client, project["id"], repeat=4)
    long_chapter, long = _chapter_and_pages(client, project["id"], repeat=16)

    assert short["coverage_ratio"] == 1
    assert long["coverage_ratio"] == 1
    assert long["page_count"] > short["page_count"]
    assert all(page["estimated_text_chars"] <= 180 for page in long["pages"])
    assert all(3 <= page["panel_count"] <= 7 for page in long["pages"])
    assert short_chapter["source_character_count"] < long_chapter["source_character_count"]


def test_page_plan_persists_rtl_storyboard_panels(client, db_session):
    project = _project(client, "分镜测试")
    _, planned = _chapter_and_pages(client, project["id"], repeat=6)
    first = planned["pages"][0]
    panels = (
        db_session.query(Panel)
        .filter(Panel.page_id == first["id"])
        .order_by(Panel.reading_order)
        .all()
    )
    assert len(panels) == first["panel_count"]
    assert [panel.reading_order for panel in panels] == list(range(1, len(panels) + 1))
    assert panels[0].bounds["x"] == 0.5
    assert db_session.query(Dialogue).filter(Dialogue.panel_id.in_([item.id for item in panels])).count()


def test_character_alias_conflict_and_reference_binding(client):
    project = _project(client, "角色测试")
    first = client.post(
        f"/api/v1/projects/{project['id']}/characters",
        json={"primary_name": "苏清白", "aliases": ["小白", "班长"]},
    )
    assert first.status_code == 201
    assert first.json()["alias_conflict"] is False

    second = client.post(
        f"/api/v1/projects/{project['id']}/characters",
        json={"primary_name": "白露", "aliases": ["小白"]},
    )
    assert second.status_code == 201
    assert second.json()["alias_conflict"] is True


def test_batch_candidate_favorite_select_and_next(client, db_session, monkeypatch):
    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    project = _project(client, "抽卡测试")
    _, plan = _chapter_and_pages(client, project["id"], repeat=12)
    first_page, second_page = plan["pages"][:2]

    batch = client.post(f"/api/v1/pages/{first_page['id']}/batches")
    assert batch.status_code == 201
    queued = client.post(
        f"/api/v1/batches/{batch.json()['id']}/candidates",
        json={"model_alias": "image.nano_banana_pro", "resolution": "1K"},
    )
    assert queued.status_code == 202
    candidate = queued.json()["candidate"]
    assert candidate["model_alias"] == "image.nano_banana_pro"

    favorite = client.patch(
        f"/api/v1/candidates/{candidate['id']}/favorite",
        json={"is_favorite": True},
    )
    assert favorite.json()["is_favorite"] is True

    asset = Asset(
        project_id=project["id"],
        kind="page_candidate",
        original_name="candidate.png",
        storage_key="generated/test.png",
        mime_type="image/png",
        byte_size=10,
        sha256="a" * 64,
        width=100,
        height=150,
        source="VERTEX_GENERATED",
        status="GENERATED",
    )
    db_session.add(asset)
    db_session.flush()
    record = db_session.get(PageCandidate, candidate["id"])
    record.asset_id = asset.id
    record.status = "READY"
    db_session.commit()

    selected = client.post(
        f"/api/v1/pages/{first_page['id']}/select-candidate",
        json={"candidate_id": candidate["id"]},
    )
    assert selected.status_code == 200
    assert selected.json()["selected_candidate_id"] == candidate["id"]
    assert client.post(f"/api/v1/pages/{first_page['id']}/next").json()["id"] == second_page["id"]

    library = client.get(
        f"/api/v1/projects/{project['id']}/library?group_by=batch&favorite=true"
    ).json()
    assert library["favorite_count"] == 1
    assert library["groups"][0]["candidates"][0]["is_selected"] is True


def test_candidate_requires_explicit_neutral_model(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    project = _project(client, "模型选择")
    _, plan = _chapter_and_pages(client, project["id"], repeat=2)
    batch = client.post(f"/api/v1/pages/{plan['pages'][0]['id']}/batches").json()
    response = client.post(
        f"/api/v1/batches/{batch['id']}/candidates",
        json={"model_alias": "image.fast", "resolution": "1K"},
    )
    assert response.status_code == 422


def test_asset_generation_batches_join_library(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    project = _project(client, "角色补图")
    character = client.post(
        f"/api/v1/projects/{project['id']}/characters",
        json={"primary_name": "苏清白", "aliases": ["小白"]},
    ).json()
    batch = client.post(
        "/api/v1/asset-generation-batches",
        json={
            "target_type": "CHARACTER",
            "target_id": character["id"],
            "generation_kind": "CHARACTER",
        },
    )
    assert batch.status_code == 201
    candidate = client.post(
        f"/api/v1/asset-generation-batches/{batch.json()['id']}/candidates",
        json={
            "model_alias": "image.nano_banana_2",
            "resolution": "1K",
            "variant": "SIDE",
            "instruction": "",
        },
    )
    assert candidate.status_code == 202
    assert candidate.json()["candidate"]["page_id"] is None
    library = client.get(f"/api/v1/projects/{project['id']}/library?group_by=batch").json()
    assert library["groups"][0]["batch"]["generation_kind"] == "CHARACTER"


def test_eight_candidate_jobs_are_isolated(client, db_session, monkeypatch):
    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    project = _project(client, "并发隔离")
    _, plan = _chapter_and_pages(client, project["id"], repeat=2)
    batch = client.post(f"/api/v1/pages/{plan['pages'][0]['id']}/batches").json()
    job_ids = []
    for index in range(8):
        alias = "image.nano_banana_2" if index % 2 == 0 else "image.nano_banana_pro"
        response = client.post(
            f"/api/v1/batches/{batch['id']}/candidates",
            json={"model_alias": alias, "resolution": "1K"},
        )
        assert response.status_code == 202
        job_ids.append(response.json()["job_id"])
    failed = db_session.get(GenerationJob, job_ids[0])
    failed.status = JobStatus.FAILED
    failed.error_code = "FAKE_FAILURE"
    db_session.commit()
    jobs = {item["id"]: item for item in client.get(f"/api/v1/projects/{project['id']}/jobs").json()}
    assert jobs[job_ids[0]]["status"] == "FAILED"
    assert all(jobs[job_id]["status"] == "WAITING" for job_id in job_ids[1:])


def test_project_json_export_uses_selected_page_versions(client, db_session, monkeypatch):
    project = _project(client, "导出测试")
    chapter, plan = _chapter_and_pages(client, project["id"], repeat=2)
    for page_data in plan["pages"]:
        page = db_session.get(MangaPage, page_data["id"])
        batch = GenerationBatch(
            project_id=project["id"],
            chapter_id=chapter["id"],
            page_id=page.id,
            ordinal=page.page_number,
            generation_kind="PAGE",
            status="CLOSED",
        )
        db_session.add(batch)
        db_session.flush()
        asset = Asset(
            project_id=project["id"],
            kind="page_candidate",
            original_name=f"page-{page.page_number}.png",
            storage_key=f"generated/page-{page.page_number}.png",
            mime_type="image/png",
            byte_size=10,
            sha256=f"{page.page_number:064d}",
            source="VERTEX_GENERATED",
            status="GENERATED",
        )
        db_session.add(asset)
        db_session.flush()
        candidate = PageCandidate(
            batch_id=batch.id,
            page_id=page.id,
            ordinal=1,
            model_alias="image.nano_banana_2",
            resolution=Resolution.DRAFT_1K,
            status="READY",
            asset_id=asset.id,
            is_selected=True,
        )
        db_session.add(candidate)
        db_session.flush()
        page.selected_candidate_id = candidate.id
    db_session.commit()

    with TemporaryDirectory() as directory:
        monkeypatch.setattr(get_settings(), "storage_root", Path(directory))
        response = client.post(
            f"/api/v1/chapters/{chapter['id']}/exports",
            json={"export_type": "JSON"},
        )
        assert response.status_code == 201
        exported = response.json()
        assert exported["page_count"] == len(plan["pages"])
        downloaded = client.get(exported["download_url"])
        assert downloaded.status_code == 200
        assert downloaded.json()["chapter"]["title"] == chapter["title"]
    assert not Path(directory).exists()
