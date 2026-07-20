from app.config import get_settings
from app.domain.states import CharacterPresence, Resolution
from app.models import (
    AppSetting,
    Asset,
    Beat,
    Chapter,
    Character,
    CharacterReference,
    MangaPage,
    Outfit,
    Panel,
    Project,
    ProviderHealth,
    Scene,
    StyleProfile,
)
from app.services.content_workflow import _resolve_panel_cast


def _base_page(db_session):
    project = Project(name="首张彩色页")
    db_session.add(project)
    db_session.flush()
    chapter = Chapter(
        project_id=project.id,
        title="第一章",
        ordinal=1,
        status="PAGES_PLANNED",
    )
    db_session.add(chapter)
    db_session.flush()
    scene = Scene(chapter_id=chapter.id, ordinal=1, location="灵前")
    db_session.add(scene)
    db_session.flush()
    beat = Beat(
        scene_id=scene.id,
        ordinal=1,
        action="我跪在爸爸的灵牌前。",
        narration="妈妈曾说，要好好送别爸爸。",
        source_range={"segment_ids": ["segment-1"]},
    )
    db_session.add(beat)
    db_session.flush()
    page = MangaPage(
        chapter_id=chapter.id,
        page_number=1,
        resolution=Resolution.DRAFT_1K,
        scene_ids=[scene.id],
        beat_ids=[beat.id],
        source_coverage={"complete": True, "ranges": [{"segment_id": "segment-1"}]},
    )
    db_session.add(page)
    db_session.flush()
    characters = {
        name: Character(project_id=project.id, primary_name=name)
        for name in ("我", "妈妈", "爸爸")
    }
    db_session.add_all(characters.values())
    db_session.flush()
    panel = Panel(
        page_id=page.id,
        reading_order=1,
        characters=[characters["我"].id],
        character_presence={
            characters["我"].id: CharacterPresence.VISIBLE.value,
            characters["妈妈"].id: CharacterPresence.MENTIONED.value,
        },
        props=["爸爸的灵牌"],
        actions={"source_text": "我跪在爸爸的灵牌前。"},
    )
    db_session.add(panel)
    db_session.add(AppSetting(key="runtime", value={"queue_mode": "LOCAL"}, version=1))
    db_session.commit()
    return project, page, panel, characters


def _enable_assets_style_provider(
    db_session, tmp_path, monkeypatch, project, page, panel, characters
):
    character_asset = Asset(
        project_id=project.id,
        kind="CHARACTER_REFERENCE",
        original_name="me.png",
        storage_key="me.png",
        mime_type="image/png",
        byte_size=1,
        sha256="a" * 64,
    )
    outfit_asset = Asset(
        project_id=project.id,
        kind="OUTFIT_REFERENCE",
        original_name="mourning.png",
        storage_key="mourning.png",
        mime_type="image/png",
        byte_size=1,
        sha256="b" * 64,
    )
    db_session.add_all([character_asset, outfit_asset])
    db_session.flush()
    db_session.add(
        CharacterReference(
            character_id=characters["我"].id,
            asset_id=character_asset.id,
            is_canonical=True,
        )
    )
    outfit = Outfit(
        project_id=project.id,
        character_id=characters["我"].id,
        name="深色葬礼正装",
        reference_asset_ids=[outfit_asset.id],
        status="CANONICAL",
    )
    style = StyleProfile(
        project_id=project.id,
        name="B1 彩色漫画风格",
        color_mode="color",
        profile={
            "palette_confirmed": True,
            "test_image_approved": True,
        },
        status="ACTIVE",
    )
    db_session.add_all([outfit, style])
    db_session.flush()
    panel.outfits = {characters["我"].id: outfit.id}
    page.style_id = style.id
    project.default_style_id = style.id

    credentials = tmp_path / "vertex.json"
    credentials.write_text("{}", encoding="utf-8")
    settings = get_settings()
    monkeypatch.setattr(settings, "google_cloud_project", "test-project")
    monkeypatch.setattr(settings, "google_application_credentials", credentials)
    db_session.add(
        ProviderHealth(
            provider="vertex-ai",
            configured=True,
            credential_file_present=True,
            health_state="HEALTHY",
            text_model_access="GRANTED",
            image_model_access={"image.nano_banana_2": "GRANTED"},
        )
    )
    db_session.commit()


def test_first_page_funeral_semantics_exclude_father_actor_reference(db_session):
    project, page, _, characters = _base_page(db_session)
    beat = db_session.get(Beat, page.beat_ids[0])
    presence, props = _resolve_panel_cast(
        page=page,
        text="我跪在爸爸的灵牌前，想起妈妈的话。",
        beat=beat,
        characters=list(characters.values()),
    )

    assert presence[characters["我"].id] == "VISIBLE"
    assert presence[characters["妈妈"].id] == "MENTIONED"
    assert characters["爸爸"].id not in presence
    assert "爸爸的灵牌" in props
    assert project.id


def test_panel_presence_edit_derives_visible_characters(client, db_session):
    _, _, panel, characters = _base_page(db_session)
    response = client.patch(
        f"/api/v1/panels/{panel.id}",
        json={
            "version": panel.version,
            "character_presence": {
                characters["我"].id: "VISIBLE",
                characters["妈妈"].id: "MENTIONED",
                characters["爸爸"].id: "OFFSCREEN",
            },
            "props": [" 爸爸的灵牌 ", "爸爸的灵牌"],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["characters"] == [characters["我"].id]
    assert payload["character_presence"][characters["妈妈"].id] == "MENTIONED"
    assert payload["props"] == ["爸爸的灵牌"]


def test_readiness_only_requires_visible_cast_and_blockers_disappear(
    client, db_session, tmp_path, monkeypatch
):
    project, page, panel, characters = _base_page(db_session)
    settings = get_settings()
    monkeypatch.setattr(settings, "google_cloud_project", None)
    monkeypatch.setattr(settings, "google_application_credentials", None)

    initial = client.get(f"/api/v1/pages/{page.id}/readiness")
    assert initial.status_code == 200
    initial_payload = initial.json()
    blocker_codes = {item["code"] for item in initial_payload["blockers"]}
    assert "MISSING_CHARACTER_REFERENCE" in blocker_codes
    assert "IMAGE_MODEL_UNAVAILABLE" in blocker_codes
    assert len(initial_payload["visible_characters"]) == 1
    assert initial_payload["visible_characters"][0]["primary_name"] == "我"
    assert {item["primary_name"] for item in initial_payload["mentioned_characters"]} == {
        "妈妈"
    }
    assert all(item["target_id"] != characters["妈妈"].id for item in initial_payload["blockers"])
    assert all(item["target_id"] != characters["爸爸"].id for item in initial_payload["blockers"])
    assert "爸爸的灵牌" in initial_payload["props"]

    blocked_batch = client.post(f"/api/v1/pages/{page.id}/batches")
    assert blocked_batch.status_code == 409
    assert blocked_batch.json()["detail"]["code"] == "PAGE_NOT_READY"

    _enable_assets_style_provider(
        db_session, tmp_path, monkeypatch, project, page, panel, characters
    )
    ready = client.get(f"/api/v1/pages/{page.id}/readiness")
    assert ready.status_code == 200
    assert ready.json()["ready"] is True
    assert ready.json()["blockers"] == []
    assert ready.json()["provider"]["usable_image_model_count"] >= 1

    batch = client.post(f"/api/v1/pages/{page.id}/batches")
    assert batch.status_code == 201
    rejected_model = client.post(
        f"/api/v1/batches/{batch.json()['id']}/candidates",
        json={"model_alias": "image.nano_banana_pro", "resolution": "1K"},
    )
    assert rejected_model.status_code == 422
    rejected_resolution = client.post(
        f"/api/v1/batches/{batch.json()['id']}/candidates",
        json={"model_alias": "image.nano_banana_2", "resolution": "2K"},
    )
    assert rejected_resolution.status_code == 422


def test_readiness_allows_pages_without_visible_characters(
    client, db_session, tmp_path, monkeypatch
):
    project, page, panel, characters = _base_page(db_session)
    _enable_assets_style_provider(
        db_session, tmp_path, monkeypatch, project, page, panel, characters
    )
    panel.characters = []
    panel.character_presence = {
        characters["妈妈"].id: CharacterPresence.OFFSCREEN.value,
    }
    panel.outfits = {}
    db_session.commit()

    response = client.get(f"/api/v1/pages/{page.id}/readiness")

    assert response.status_code == 200
    assert response.json()["ready"] is True
    assert response.json()["visible_characters"] == []
    assert "VISIBLE_CAST_EMPTY" not in {
        item["code"] for item in response.json()["blockers"]
    }
