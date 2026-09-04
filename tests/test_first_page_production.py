from app.config import get_settings
from app.domain.states import Resolution
from app.models import (
    Asset,
    AssetCandidate,
    Chapter,
    Character,
    CharacterReference,
    GenerationBatch,
    InspectionResult,
    MangaPage,
    Outfit,
    PageCandidate,
    StyleProfile,
    Scene,
)


def _project(client, name: str) -> dict:
    response = client.post("/api/v1/projects", json={"name": name})
    assert response.status_code == 201
    return response.json()


def _asset(project_id: str, name: str, digest: str, kind: str) -> Asset:
    return Asset(
        project_id=project_id,
        kind=kind,
        original_name=name,
        storage_key=name,
        mime_type="image/png",
        byte_size=10,
        sha256=digest * 64,
        source="VERTEX_GENERATED",
        status="GENERATED",
    )


def test_concept_sheet_can_be_approved_as_character_and_outfit_reference(
    client, db_session, monkeypatch
):
    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    project = _project(client, "概念设定确认")
    character = client.post(
        f"/api/v1/projects/{project['id']}/characters",
        json={"primary_name": "我", "canonical_description": "紫黑长发的少女"},
    ).json()

    queued = client.post(
        f"/api/v1/characters/{character['id']}/complete-sheet",
        json={
            "model_alias": "image.nano_banana_2",
            "resolution": "1K",
            "generation_mode": "CONCEPT",
            "appearance_description": "紫黑长发、克制疲惫的神情",
            "outfit_name": "深色葬礼正装",
            "outfit_description": "黑色修身外套、深灰内搭、无鲜艳配饰",
        },
    )
    assert queued.status_code == 202, queued.json()
    candidate_id = queued.json()["candidate"]["id"]
    asset = _asset(project["id"], "concept.png", "a", "character")
    db_session.add(asset)
    db_session.flush()
    candidate = db_session.get(AssetCandidate, candidate_id)
    candidate.asset_id = asset.id
    candidate.status = "READY"
    db_session.commit()

    approved = client.post(
        f"/api/v1/asset-candidates/{candidate_id}/approve-reference",
        json={
            "character_id": character["id"],
            "outfit_name": "深色葬礼正装",
            "outfit_description": "黑色修身外套、深灰内搭、无鲜艳配饰",
            "outfit_locked_fields": ["颜色", "剪裁", "配饰"],
        },
    )
    assert approved.status_code == 200, approved.json()
    reference = db_session.query(CharacterReference).filter_by(
        character_id=character["id"], asset_id=asset.id
    ).one()
    outfit = db_session.get(Outfit, approved.json()["outfit_id"])
    assert reference.is_canonical is True
    assert outfit.reference_asset_ids == [asset.id]
    assert outfit.name == "深色葬礼正装"

    retracted = client.delete(
        f"/api/v1/asset-candidates/{candidate_id}/approve-reference"
    )
    assert retracted.status_code == 200, retracted.json()
    assert retracted.json()["approved"] is False
    assert (
        db_session.query(CharacterReference)
        .filter_by(character_id=character["id"], asset_id=asset.id)
        .count()
        == 0
    )
    db_session.refresh(outfit)
    assert outfit.reference_asset_ids == []
    assert outfit.status.value == "NEEDS_CONFIRMATION"


def test_character_reference_reassignment_is_exclusive(client, db_session):
    project = _project(client, "人物参考唯一绑定")
    first = client.post(
        f"/api/v1/projects/{project['id']}/characters",
        json={"primary_name": "荻原桜"},
    ).json()
    second = client.post(
        f"/api/v1/projects/{project['id']}/characters",
        json={"primary_name": "我"},
    ).json()
    asset = _asset(project["id"], "character-sheet.png", "e", "CHARACTER_REFERENCE")
    db_session.add(asset)
    db_session.commit()

    assert client.post(
        f"/api/v1/characters/{first['id']}/references",
        json={"asset_id": asset.id, "is_canonical": True},
    ).status_code == 201
    rebound = client.post(
        f"/api/v1/characters/{second['id']}/references",
        json={"asset_id": asset.id, "is_canonical": True},
    )
    assert rebound.status_code == 201, rebound.json()
    references = db_session.query(CharacterReference).filter_by(asset_id=asset.id).all()
    assert len(references) == 1
    assert references[0].character_id == second["id"]


def test_scene_outfit_can_return_to_unspecified(client, db_session):
    project = _project(client, "服装可清空")
    chapter = Chapter(project_id=project["id"], title="第一章", ordinal=1)
    character = Character(project_id=project["id"], primary_name="我")
    db_session.add_all([chapter, character])
    db_session.flush()
    scene = Scene(chapter_id=chapter.id, ordinal=1)
    outfit = Outfit(
        project_id=project["id"],
        character_id=character.id,
        name="深色葬礼正装",
    )
    db_session.add_all([scene, outfit])
    db_session.commit()

    assigned = client.patch(
        f"/api/v1/scenes/{scene.id}/outfits",
        json={"assignments": {character.id: outfit.id}},
    )
    assert assigned.status_code == 200, assigned.json()
    cleared = client.patch(
        f"/api/v1/scenes/{scene.id}/outfits",
        json={"assignments": {character.id: ""}},
    )
    assert cleared.status_code == 200, cleared.json()
    assert cleared.json()["assignments"] == {}


def test_color_style_requires_palette_and_approved_test_image(
    client, db_session, monkeypatch
):
    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    project = _project(client, "彩色风格确认")
    style_reference = _asset(project["id"], "style.png", "b", "STYLE_REFERENCE")
    db_session.add(style_reference)
    db_session.commit()
    style = client.post(
        f"/api/v1/projects/{project['id']}/styles",
        json={
            "name": "B1 彩色漫画风格",
            "color_mode": "color",
            "reference_asset_ids": [style_reference.id],
        },
    ).json()
    assert (
        client.post(
            f"/api/v1/projects/{project['id']}/styles/{style['id']}/activate"
        ).status_code
        == 409
    )
    drafted = client.post(
        f"/api/v1/styles/{style['id']}/palette-draft",
        json={"atmosphere": "低饱和、雨后京都、葬礼后的克制情绪"},
    )
    assert drafted.status_code == 202
    style_record = db_session.get(StyleProfile, style["id"])
    approved_palette = client.post(
        f"/api/v1/styles/{style['id']}/palette-approve",
        json={
            "version": style_record.version,
            "palette": {
                "primary": ["#353945", "#6E7280"],
                "skin": "#E7C6B5",
                "hair": "#25243A",
                "environment": ["#71808A", "#AAB5B8"],
                "light": "#D7DEE0",
            },
        },
    )
    assert approved_palette.status_code == 200, approved_palette.json()
    batch = client.post(
        "/api/v1/asset-generation-batches",
        json={
            "target_type": "STYLE",
            "target_id": style["id"],
            "generation_kind": "STYLE_TEST",
        },
    )
    assert batch.status_code == 201, batch.json()
    test_asset = _asset(project["id"], "style-test.png", "c", "style_test")
    candidate = AssetCandidate(
        batch_id=batch.json()["id"],
        ordinal=1,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        variant="STYLE_TEST",
        status="READY",
    )
    db_session.add_all([test_asset, candidate])
    db_session.flush()
    candidate.asset_id = test_asset.id
    db_session.commit()
    style_record = db_session.get(StyleProfile, style["id"])
    approved_test = client.post(
        f"/api/v1/styles/{style['id']}/style-test-approve",
        json={
            "candidate_id": candidate.id,
            "approved": True,
            "version": style_record.version,
        },
    )
    assert approved_test.status_code == 200, approved_test.json()
    activated = client.post(
        f"/api/v1/projects/{project['id']}/styles/{style['id']}/activate"
    )
    assert activated.status_code == 200, activated.json()
    assert activated.json()["status"] == "ACTIVE"


def test_candidate_selection_allows_manual_text_confirmation_and_blocks_severe_issues(client, db_session):
    project = _project(client, "采用门禁")
    chapter = Chapter(project_id=project["id"], ordinal=1, title="第一章")
    db_session.add(chapter)
    db_session.flush()
    page = MangaPage(
        chapter_id=chapter.id,
        page_number=1,
        source_coverage={"complete": True},
    )
    db_session.add(page)
    db_session.flush()
    batch = GenerationBatch(
        project_id=project["id"],
        chapter_id=chapter.id,
        page_id=page.id,
        ordinal=1,
        generation_kind="PAGE",
    )
    asset = _asset(project["id"], "page.png", "d", "page_candidate")
    db_session.add_all([batch, asset])
    db_session.flush()
    candidate = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=1,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        status="NEEDS_REVIEW",
        asset_id=asset.id,
        based_on_storyboard_version=page.storyboard_version,
    )
    db_session.add(candidate)
    db_session.flush()
    for category in ("CHARACTER", "OUTFIT", "CONTINUITY"):
        db_session.add(
            InspectionResult(
                candidate_id=candidate.id,
                storyboard_version=page.storyboard_version,
                category=category,
                outcome="PASS",
                score=0.99,
                severity="INFO",
            )
        )
    db_session.add(
        InspectionResult(
            candidate_id=candidate.id,
            storyboard_version=page.storyboard_version,
            category="TEXT",
            outcome="MISMATCH",
            score=0.94,
            severity="WARNING",
            details={"expected": "目标文字", "observed": "有一处错字"},
        )
    )
    db_session.commit()
    blocked = client.post(
        f"/api/v1/pages/{page.id}/select-candidate",
        json={"candidate_id": candidate.id},
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["blockers"][0]["code"] == "TEXT_REVIEW_REQUIRED"

    selected = client.post(
        f"/api/v1/pages/{page.id}/select-candidate",
        json={"candidate_id": candidate.id, "manual_text_confirmed": True},
    )
    assert selected.status_code == 200, selected.json()
    assert selected.json()["selected_candidate_id"] == candidate.id
    readiness = client.get(f"/api/v1/pages/{page.id}/production-readiness")
    assert readiness.status_code == 200
    assert readiness.json()["state"] == "NEEDS_REPAIR"
    assert readiness.json()["ready"] is False
    assert client.post(f"/api/v1/pages/{page.id}/next").status_code == 409
    blocked_export = client.post(
        f"/api/v1/chapters/{chapter.id}/exports",
        json={"export_type": "JSON"},
    )
    assert blocked_export.status_code == 409
    assert blocked_export.json()["detail"]["code"] == "PAGE_NOT_PRODUCTION_READY"


def test_select_candidate_does_not_clear_scene_change_review_flag(client, db_session):
    """Re-selecting an INSPECTED candidate after a non-storyboard change (scene
    asset rebind flags the page NEEDS_REVIEW without bumping storyboard_version)
    must not push the page to FINAL_READY/PASSED: production readiness has to
    stay blocked until a fresh inspection runs against the changed inputs."""

    project = _project(client, "场景变更后重选")
    chapter = Chapter(project_id=project["id"], ordinal=1, title="第一章")
    db_session.add(chapter)
    db_session.flush()
    page = MangaPage(
        chapter_id=chapter.id,
        page_number=1,
        source_coverage={"complete": True},
        status="FINAL_READY",
        continuity_status="PASSED",
        selected_candidate_ack_version=1,
    )
    db_session.add(page)
    db_session.flush()
    batch = GenerationBatch(
        project_id=project["id"],
        chapter_id=chapter.id,
        page_id=page.id,
        ordinal=1,
        generation_kind="PAGE",
    )
    asset = _asset(project["id"], "flagged.png", "f", "page_candidate")
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
        is_selected=True,
        based_on_storyboard_version=page.storyboard_version,
    )
    db_session.add(candidate)
    db_session.flush()
    page.selected_candidate_id = candidate.id
    # A scene-asset change marks the page for review without touching the
    # storyboard version (services.editor.mark_pages_for_review semantics).
    page.continuity_status = "NEEDS_REVIEW"
    db_session.commit()

    selected = client.post(
        f"/api/v1/pages/{page.id}/select-candidate",
        json={"candidate_id": candidate.id, "manual_text_confirmed": True},
    )
    assert selected.status_code == 200, selected.json()
    assert selected.json()["status"] == "FINAL_CHECKING"
    assert selected.json()["continuity_status"] == "NEEDS_REVIEW"

    readiness = client.get(f"/api/v1/pages/{page.id}/production-readiness")
    assert readiness.json()["ready"] is False
    assert readiness.json()["state"] == "NEEDS_REPAIR"
    assert any(
        blocker["code"] == "QUALITY_REVIEW_REQUIRED"
        for blocker in readiness.json()["blockers"]
    )
    blocked_export = client.post(
        f"/api/v1/chapters/{chapter.id}/exports",
        json={"export_type": "JSON"},
    )
    assert blocked_export.status_code == 409


def test_select_candidate_does_not_clear_upstream_recheck_flag(client, db_session):
    """A downstream page flagged NEEDS_RECHECK by an upstream re-selection
    must not reach FINAL_READY by re-selecting its INSPECTED candidate; the
    continuity inputs changed, so a fresh inspection is required."""

    project = _project(client, "上游重选复查")
    chapter = Chapter(project_id=project["id"], ordinal=1, title="第一章")
    db_session.add(chapter)
    db_session.flush()
    page = MangaPage(
        chapter_id=chapter.id,
        page_number=2,
        source_coverage={"complete": True},
        status="FINAL_READY",
        continuity_status="NEEDS_RECHECK",
        selected_candidate_ack_version=1,
    )
    db_session.add(page)
    db_session.flush()
    batch = GenerationBatch(
        project_id=project["id"],
        chapter_id=chapter.id,
        page_id=page.id,
        ordinal=1,
        generation_kind="PAGE",
    )
    asset = _asset(project["id"], "recheck.png", "e", "page_candidate")
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
        is_selected=True,
        based_on_storyboard_version=page.storyboard_version,
    )
    db_session.add(candidate)
    db_session.flush()
    page.selected_candidate_id = candidate.id
    db_session.commit()

    selected = client.post(
        f"/api/v1/pages/{page.id}/select-candidate",
        json={"candidate_id": candidate.id, "manual_text_confirmed": True},
    )
    assert selected.status_code == 200, selected.json()
    assert selected.json()["status"] == "FINAL_CHECKING"
    assert selected.json()["continuity_status"] == "NEEDS_RECHECK"

    readiness = client.get(f"/api/v1/pages/{page.id}/production-readiness")
    assert readiness.json()["ready"] is False
