import pytest
from sqlalchemy import text

from app.config import get_settings
from app.domain.states import JobStatus, PageStatus, Resolution
from app.models import (
    Asset,
    Chapter,
    Character,
    Dialogue,
    GenerationBatch,
    GenerationJob,
    InspectionResult,
    MangaPage,
    Outfit,
    PageCandidate,
    Panel,
    Project,
    StyleProfile,
)
from app.worker_tasks import StaleStoryboardVersionError, _run_page_generate


def _project(client, name: str = "稳定化项目") -> dict:
    response = client.post("/api/v1/projects", json={"name": name})
    assert response.status_code == 201
    return response.json()


def test_dashboard_returns_real_counts_review_state_and_next_action(client, db_session):
    project = _project(client)
    chapter = Chapter(project_id=project["id"], title="第一章", ordinal=1)
    db_session.add(chapter)
    db_session.flush()
    pages = [
        MangaPage(chapter_id=chapter.id, page_number=1, storyboard_version=2),
        MangaPage(chapter_id=chapter.id, page_number=2),
    ]
    db_session.add_all(pages)
    db_session.flush()
    batch = GenerationBatch(
        project_id=project["id"], chapter_id=chapter.id, page_id=pages[0].id, ordinal=1
    )
    db_session.add(batch)
    db_session.flush()
    candidate = PageCandidate(
        batch_id=batch.id,
        page_id=pages[0].id,
        ordinal=1,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        based_on_storyboard_version=1,
        is_selected=True,
    )
    db_session.add(candidate)
    db_session.flush()
    pages[0].selected_candidate_id = candidate.id
    db_session.add(
        GenerationJob(
            project_id=project["id"],
            target_type="PAGE",
            target_id=pages[1].id,
            job_type="PAGE_GENERATE",
            status=JobStatus.GENERATING,
        )
    )
    db_session.commit()

    response = client.get("/api/v1/projects/dashboard")

    assert response.status_code == 200
    dashboard = response.json()
    assert dashboard["totals"] == {
        "project_count": 1,
        "page_count": 2,
        "selected_page_count": 1,
        "review_page_count": 1,
        "pending_job_count": 1,
    }
    item = dashboard["projects"][0]
    assert (item["chapter_count"], item["page_count"], item["candidate_count"]) == (1, 2, 1)
    assert item["stale_selected_page_count"] == 1
    assert item["next_action"]["section"] == "storyboard"


def test_candidate_version_states_and_keep_old_selection(client, db_session):
    project = _project(client, "候选版本")
    chapter = Chapter(project_id=project["id"], title="第一章", ordinal=1)
    db_session.add(chapter)
    db_session.flush()
    page = MangaPage(chapter_id=chapter.id, page_number=1, storyboard_version=3)
    db_session.add(page)
    db_session.flush()
    batch = GenerationBatch(
        project_id=project["id"], chapter_id=chapter.id, page_id=page.id, ordinal=1
    )
    db_session.add(batch)
    db_session.flush()
    candidates = [
        PageCandidate(
            batch_id=batch.id,
            page_id=page.id,
            ordinal=ordinal,
            model_alias="image.nano_banana_2",
            resolution=Resolution.DRAFT_1K,
            based_on_storyboard_version=based_on,
            is_selected=ordinal == 3,
        )
        for ordinal, based_on in [(1, 3), (2, 2), (3, 2), (4, None)]
    ]
    db_session.add_all(candidates)
    db_session.flush()
    page.selected_candidate_id = candidates[2].id
    page.selected_candidate_ack_version = 3
    db_session.commit()

    response = client.get(f"/api/v1/batches/{batch.id}/candidates")

    assert response.status_code == 200
    states = {item["ordinal"]: item["version_state"] for item in response.json()}
    assert states == {4: "LEGACY_UNKNOWN", 3: "STALE_ACCEPTED", 2: "STALE", 1: "CURRENT"}

    page.selected_candidate_ack_version = None
    db_session.commit()
    kept = client.post(
        f"/api/v1/pages/{page.id}/selected-candidate/keep",
        json={
            "candidate_id": candidates[2].id,
            "storyboard_version": 3,
            "manual_text_confirmed": True,
        },
    )
    assert kept.status_code == 200
    assert kept.json()["selected_candidate_ack_version"] == 3


def test_accepting_stale_inspected_candidate_still_requires_fresh_inspection(
    client, db_session
):
    project = _project(client, "旧候选复检")
    chapter = Chapter(project_id=project["id"], title="第一章", ordinal=1)
    db_session.add(chapter)
    db_session.flush()
    page = MangaPage(chapter_id=chapter.id, page_number=1, storyboard_version=2)
    db_session.add(page)
    db_session.flush()
    batch = GenerationBatch(
        project_id=project["id"], chapter_id=chapter.id, page_id=page.id, ordinal=1
    )
    asset = Asset(
        project_id=project["id"],
        kind="page_candidate",
        original_name="stale.png",
        storage_key="generated/stale.png",
        mime_type="image/png",
        byte_size=10,
        sha256="s" * 64,
        source="VERTEX_GENERATED",
        status="GENERATED",
    )
    db_session.add_all([batch, asset])
    db_session.flush()
    candidate = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=1,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        status="INSPECTED",
        asset_id=asset.id,
        based_on_storyboard_version=1,
    )
    db_session.add(candidate)
    db_session.flush()
    for category in ("CHARACTER", "OUTFIT", "CONTINUITY"):
        db_session.add(
            InspectionResult(
                candidate_id=candidate.id,
                category=category,
                outcome="PASS",
                score=0.99,
                severity="INFO",
            )
        )
    db_session.commit()

    selected = client.post(
        f"/api/v1/pages/{page.id}/select-candidate",
        json={
            "candidate_id": candidate.id,
            "manual_text_confirmed": True,
            "accept_stale": True,
        },
    )

    assert selected.status_code == 200, selected.json()
    assert selected.json()["status"] == "FINAL_CHECKING"
    assert selected.json()["continuity_status"] == "NOT_CHECKED"
    readiness = client.get(f"/api/v1/pages/{page.id}/production-readiness")
    assert readiness.json()["state"] == "AWAITING_INSPECTION"
    assert readiness.json()["ready"] is False


def test_keep_stale_candidate_invalidates_inspection_idempotency_key(
    client, db_session, monkeypatch
):
    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    project = _project(client, "旧质检任务失效")
    chapter = Chapter(project_id=project["id"], title="第一章", ordinal=1)
    db_session.add(chapter)
    db_session.flush()
    page = MangaPage(
        chapter_id=chapter.id,
        page_number=1,
        storyboard_version=2,
        selected_candidate_ack_version=None,
    )
    db_session.add(page)
    db_session.flush()
    batch = GenerationBatch(
        project_id=project["id"], chapter_id=chapter.id, page_id=page.id, ordinal=1
    )
    asset = Asset(
        project_id=project["id"],
        kind="page_candidate",
        original_name="stale-recheck.png",
        storage_key="generated/stale-recheck.png",
        mime_type="image/png",
        byte_size=10,
        sha256="t" * 64,
        source="VERTEX_GENERATED",
        status="GENERATED",
    )
    db_session.add_all([batch, asset])
    db_session.flush()
    candidate = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=1,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        status="INSPECTED",
        asset_id=asset.id,
        based_on_storyboard_version=1,
        is_selected=True,
    )
    db_session.add(candidate)
    db_session.flush()
    page.selected_candidate_id = candidate.id
    old_job = GenerationJob(
        project_id=project["id"],
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_INSPECT",
        status=JobStatus.COMPLETED,
        idempotency_key=f"inspect:{candidate.id}:{candidate.version}",
    )
    db_session.add(old_job)
    db_session.commit()
    old_version = candidate.version

    kept = client.post(
        f"/api/v1/pages/{page.id}/selected-candidate/keep",
        json={
            "candidate_id": candidate.id,
            "storyboard_version": page.storyboard_version,
            "manual_text_confirmed": True,
        },
    )
    assert kept.status_code == 200, kept.json()
    db_session.refresh(candidate)
    assert candidate.version == old_version + 1

    inspection = client.post(
        f"/api/v1/candidates/{candidate.id}/inspect",
        json={"categories": ["CONTINUITY"]},
    )
    assert inspection.status_code == 202, inspection.json()
    assert inspection.json()["id"] != old_job.id
    new_job = db_session.get(GenerationJob, inspection.json()["id"])
    assert new_job.idempotency_key == f"inspect:{candidate.id}:{candidate.version}"


def test_retract_selected_candidate_preserves_candidate_and_marks_following_page(client, db_session):
    project = _project(client, "撤回采用")
    chapter = Chapter(project_id=project["id"], title="第一章", ordinal=1)
    db_session.add(chapter)
    db_session.flush()
    page = MangaPage(
        chapter_id=chapter.id,
        page_number=1,
        status=PageStatus.APPROVED,
        selected_candidate_ack_version=2,
        storyboard_version=2,
    )
    following = MangaPage(
        chapter_id=chapter.id,
        page_number=2,
        status=PageStatus.APPROVED,
        continuity_status="CHECKED",
    )
    db_session.add_all([page, following])
    db_session.flush()
    batch = GenerationBatch(
        project_id=project["id"], chapter_id=chapter.id, page_id=page.id, ordinal=1
    )
    following_batch = GenerationBatch(
        project_id=project["id"], chapter_id=chapter.id, page_id=following.id, ordinal=2
    )
    db_session.add_all([batch, following_batch])
    db_session.flush()
    candidate = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=1,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        based_on_storyboard_version=2,
        is_selected=True,
    )
    following_candidate = PageCandidate(
        batch_id=following_batch.id,
        page_id=following.id,
        ordinal=1,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        is_selected=True,
    )
    db_session.add_all([candidate, following_candidate])
    db_session.flush()
    page.selected_candidate_id = candidate.id
    following.selected_candidate_id = following_candidate.id
    db_session.commit()

    response = client.delete(f"/api/v1/pages/{page.id}/selected-candidate")

    assert response.status_code == 200
    assert response.json()["selected_candidate_id"] is None
    assert response.json()["selected_candidate_ack_version"] is None
    assert response.json()["status"] == "REVIEW_REQUIRED"
    db_session.refresh(candidate)
    db_session.refresh(following)
    assert candidate.is_selected is False
    assert candidate.deleted_at is None
    assert following.continuity_status == "NEEDS_RECHECK"

    repeated = client.delete(f"/api/v1/pages/{page.id}/selected-candidate")
    assert repeated.status_code == 409


def test_text_checks_and_text_repairs_are_retired(client, db_session):
    project = _project(client, "人工文字校对")
    chapter = Chapter(project_id=project["id"], title="第一章", ordinal=1)
    db_session.add(chapter)
    db_session.flush()
    page = MangaPage(chapter_id=chapter.id, page_number=1)
    db_session.add(page)
    db_session.flush()
    batch = GenerationBatch(
        project_id=project["id"], chapter_id=chapter.id, page_id=page.id, ordinal=1
    )
    db_session.add(batch)
    db_session.flush()
    candidate = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=1,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
    )
    db_session.add(candidate)
    db_session.commit()

    response = client.post(
        f"/api/v1/candidates/{candidate.id}/inspect", json={"categories": ["TEXT"]}
    )

    assert response.status_code == 422
    assert "人工校对" in response.text


def test_stale_storyboard_stops_before_model_call(db_session, monkeypatch):
    project = Project(name="竞态保护")
    db_session.add(project)
    db_session.flush()
    chapter = Chapter(project_id=project.id, title="第一章", ordinal=1)
    db_session.add(chapter)
    db_session.flush()
    page = MangaPage(
        chapter_id=chapter.id,
        page_number=1,
        storyboard_version=2,
        scene_ids=["scene"],
        beat_ids=["beat"],
        source_coverage={"complete": True},
    )
    db_session.add(page)
    db_session.flush()
    batch = GenerationBatch(
        project_id=project.id, chapter_id=chapter.id, page_id=page.id, ordinal=1
    )
    db_session.add(batch)
    db_session.flush()
    candidate = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=1,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        based_on_storyboard_version=1,
    )
    db_session.add(candidate)
    db_session.flush()
    job = GenerationJob(
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_GENERATE",
        status=JobStatus.PREPARING,
    )
    db_session.add(job)
    db_session.commit()
    calls = []
    monkeypatch.setattr("app.worker_tasks._adapter", lambda alias: calls.append(alias))

    with pytest.raises(StaleStoryboardVersionError):
        _run_page_generate(db_session, job)

    assert calls == []


def test_foreign_keys_cascade_content_but_keep_generated_assets(db_session):
    assert db_session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
    project = Project(name="级联删除")
    db_session.add(project)
    db_session.flush()
    chapter = Chapter(project_id=project.id, title="第一章", ordinal=1)
    asset = Asset(
        project_id=project.id,
        kind="page_candidate",
        original_name="kept.png",
        storage_key="generated/kept.png",
        mime_type="image/png",
        byte_size=1,
        sha256="f" * 64,
    )
    db_session.add_all([chapter, asset])
    db_session.flush()
    page = MangaPage(chapter_id=chapter.id, page_number=1)
    db_session.add(page)
    db_session.flush()
    panel = Panel(page_id=page.id, reading_order=1)
    batch = GenerationBatch(
        project_id=project.id, chapter_id=chapter.id, page_id=page.id, ordinal=1
    )
    db_session.add_all([panel, batch])
    db_session.flush()
    dialogue = Dialogue(panel_id=panel.id, target_text="保留素材，删除结构", reading_order=1)
    candidate = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=1,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        asset_id=asset.id,
    )
    db_session.add_all([dialogue, candidate])
    db_session.commit()
    page_id = page.id
    panel_id = panel.id
    dialogue_id = dialogue.id
    candidate_id = candidate.id
    asset_id = asset.id

    db_session.delete(chapter)
    db_session.commit()
    db_session.expire_all()

    assert db_session.get(MangaPage, page_id) is None
    assert db_session.get(Panel, panel_id) is None
    assert db_session.get(Dialogue, dialogue_id) is None
    assert db_session.get(PageCandidate, candidate_id) is None
    assert db_session.get(Asset, asset_id) is not None


def test_generated_and_uploaded_assets_support_display_names(client, db_session):
    project = Project(name="素材命名")
    db_session.add(project)
    db_session.flush()
    assets = [
        Asset(
            project_id=project.id,
            kind="CHARACTER_REFERENCE",
            original_name="generated-character.png",
            storage_key="generated/generated-character.png",
            mime_type="image/png",
            byte_size=1,
            sha256="1" * 64,
            source="GENERATED",
        ),
        Asset(
            project_id=project.id,
            kind="STYLE_REFERENCE",
            original_name="uploaded-style.png",
            storage_key="uploads/uploaded-style.png",
            mime_type="image/png",
            byte_size=1,
            sha256="2" * 64,
            source="USER_UPLOAD",
        ),
    ]
    db_session.add_all(assets)
    db_session.commit()

    for asset, display_name in zip(assets, ["荻原桜人物正面", "葬礼场景色彩参考"], strict=True):
        response = client.patch(
            f"/api/v1/assets/{asset.id}", json={"display_name": display_name}
        )
        assert response.status_code == 200, response.text
        assert response.json()["display_name"] == display_name
        assert response.json()["original_name"] == asset.original_name

    cleared = client.patch(
        f"/api/v1/assets/{assets[0].id}", json={"display_name": None}
    )
    assert cleared.status_code == 200
    assert cleared.json()["display_name"] is None
    assert client.patch(
        f"/api/v1/assets/{assets[0].id}", json={"display_name": "   "}
    ).status_code == 422


def test_deleting_reference_detaches_outfit_and_style_bindings(client, db_session):
    project = Project(name="引用清理")
    db_session.add(project)
    db_session.flush()
    character = Character(project_id=project.id, primary_name="角色")
    asset = Asset(
        project_id=project.id,
        kind="OUTFIT_REFERENCE",
        original_name="reference.png",
        storage_key="reference.png",
        mime_type="image/png",
        byte_size=1,
        sha256="3" * 64,
    )
    db_session.add_all([character, asset])
    db_session.flush()
    outfit = Outfit(
        project_id=project.id,
        character_id=character.id,
        name="服装",
        reference_asset_ids=[asset.id],
        status="CANONICAL",
    )
    style = StyleProfile(
        project_id=project.id,
        name="风格",
        profile={
            "reference_asset_ids": [asset.id],
            "palette_confirmed": True,
            "test_image_approved": True,
        },
        status="ACTIVE",
    )
    db_session.add_all([outfit, style])
    db_session.commit()

    response = client.delete(f"/api/v1/assets/{asset.id}")

    assert response.status_code == 204
    db_session.expire_all()
    assert db_session.get(Outfit, outfit.id).reference_asset_ids == []
    stored_style = db_session.get(StyleProfile, style.id)
    assert stored_style.profile["reference_asset_ids"] == []
    assert stored_style.profile["palette_confirmed"] is False
    assert stored_style.profile["test_image_approved"] is False
    assert client.post(
        "/api/v1/asset-generation-batches",
        json={
            "target_type": "OUTFIT",
            "target_id": outfit.id,
            "generation_kind": "OUTFIT",
        },
    ).status_code == 409
    assert client.post(
        "/api/v1/asset-generation-batches",
        json={
            "target_type": "STYLE",
            "target_id": style.id,
            "generation_kind": "STYLE_TEST",
        },
    ).status_code == 409


def test_bulk_archive_only_accepts_project_terminal_jobs(client, db_session):
    project = _project(client, "批量归档")
    other = _project(client, "其他项目")
    jobs = [
        GenerationJob(
            project_id=project["id"],
            target_type="PROJECT",
            target_id=project["id"],
            job_type="TEST",
            status=status,
        )
        for status in [
            JobStatus.COMPLETED,
            JobStatus.FAILED,
            JobStatus.NEEDS_REVIEW,
            JobStatus.GENERATING,
        ]
    ]
    foreign_job = GenerationJob(
        project_id=other["id"],
        target_type="PROJECT",
        target_id=other["id"],
        job_type="TEST",
        status=JobStatus.COMPLETED,
    )
    db_session.add_all([*jobs, foreign_job])
    db_session.commit()

    rejected = client.post(
        f"/api/v1/projects/{project['id']}/jobs/bulk-archive",
        json={"job_ids": [jobs[0].id, jobs[3].id]},
    )
    assert rejected.status_code == 409
    foreign = client.post(
        f"/api/v1/projects/{project['id']}/jobs/bulk-archive",
        json={"job_ids": [foreign_job.id]},
    )
    assert foreign.status_code == 404
    archived = client.post(
        f"/api/v1/projects/{project['id']}/jobs/bulk-archive",
        json={"job_ids": [jobs[0].id, jobs[1].id, jobs[2].id]},
    )
    assert archived.status_code == 200
    assert archived.json()["archived_count"] == 3
