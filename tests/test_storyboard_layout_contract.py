"""V02-30 storyboard layout data-contract tests (contract §16.1, SQLite).

Covers L2 read-path mapping, L3 geometry validation, L4 API contract,
L5 reading order, L6 limits, L7 version invalidation and L8 sound effects.
L1 (migration roundtrip) lives in test_migrations.py. Real PostgreSQL and
Playwright are NOT RUN for this task.
"""

from app.api.helpers import candidate_version_state
from app.domain.states import Resolution
from app.models import (
    Beat,
    Chapter,
    Character,
    Dialogue,
    GenerationBatch,
    MangaPage,
    PageCandidate,
    Panel,
    Project,
    Scene,
)

B5_DEFAULT_CANVAS = {
    "width_mm": 182,
    "height_mm": 257,
    "bleed_mm": 3,
    "safe_mm": 5,
    "unit": "mm",
}

LEGACY_BOUNDS = [
    {"x": 0.012, "y": 0.012, "width": 0.976, "height": 0.448},
    {"x": 0.472, "y": 0.472, "width": 0.516, "height": 0.516},
    {"x": 0.012, "y": 0.472, "width": 0.436, "height": 0.516},
]


def _storyboard_fixture(db_session, *, page_number: int = 1, sound_effects=None):
    project = Project(name="分镜布局契约测试", page_ratio="b5_portrait")
    db_session.add(project)
    db_session.flush()
    chapter = Chapter(project_id=project.id, title="第一章", ordinal=1, status="PAGES_PLANNED")
    character = Character(project_id=project.id, primary_name="荻原桜")
    db_session.add_all([chapter, character])
    db_session.flush()
    scene = Scene(
        chapter_id=chapter.id,
        ordinal=1,
        location="教室",
        source_range={"segment_ids": ["source-1"]},
    )
    db_session.add(scene)
    db_session.flush()
    beat = Beat(
        scene_id=scene.id,
        ordinal=1,
        action="荻原桜抬头",
        speaker_name="荻原桜",
        dialogue="你来了。",
        source_range={"segment_ids": ["source-1"]},
    )
    db_session.add(beat)
    db_session.flush()
    page = MangaPage(
        chapter_id=chapter.id,
        page_number=page_number,
        scene_ids=[scene.id],
        beat_ids=[beat.id],
        panel_count=3,
        estimated_text_chars=4,
        estimated_bubbles=1,
        source_coverage={
            "complete": True,
            "ranges": [
                {
                    "segment_id": "source-1",
                    "start_offset": 0,
                    "end_offset": 4,
                    "text": "荻原桜抬头。",
                }
            ],
        },
    )
    db_session.add(page)
    db_session.flush()
    panels = [
        Panel(
            page_id=page.id,
            reading_order=index + 1,
            bounds=bounds,
            characters=[character.id],
            actions={"source_text": "她抬头。"},
        )
        for index, bounds in enumerate(LEGACY_BOUNDS)
    ]
    if sound_effects is not None:
        panels[0].sound_effects = sound_effects
    db_session.add_all(panels)
    db_session.flush()
    dialogue = Dialogue(
        panel_id=panels[0].id,
        speaker_character_id=character.id,
        target_text="你来了。",
        reading_order=1,
        region={"preferred": "upper_inner"},
    )
    db_session.add(dialogue)
    db_session.commit()
    return project, chapter, page, panels, dialogue, character


def _add_candidate(db_session, project, chapter, page) -> PageCandidate:
    batch = GenerationBatch(
        project_id=project.id,
        chapter_id=chapter.id,
        page_id=page.id,
        ordinal=1,
        generation_kind="PAGE",
    )
    db_session.add(batch)
    db_session.flush()
    candidate = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=1,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        status="READY",
        based_on_storyboard_version=page.storyboard_version,
    )
    db_session.add(candidate)
    db_session.commit()
    return candidate


def _geometry_payload(db_session, page, *, request_id="replay-1", storyboard_version=None):
    panels = list(
        db_session.query(Panel).filter(Panel.page_id == page.id).order_by(Panel.reading_order)
    )
    dialogues = list(
        db_session.query(Dialogue).filter(Dialogue.panel_id.in_([p.id for p in panels]))
    )
    return {
        "request_id": request_id,
        "storyboard_version": (
            page.storyboard_version if storyboard_version is None else storyboard_version
        ),
        "panels": [
            {
                "panel_id": panel.id,
                "bounds": dict(panel.bounds),
                "reading_order": panel.reading_order,
            }
            for panel in panels
        ],
        "dialogues": [
            {"dialogue_id": dialogue.id, "reading_order": dialogue.reading_order}
            for dialogue in dialogues
        ],
    }


# --- L2: read-path normalization -------------------------------------------


def test_read_path_derives_geometry_bubble_and_canvas(client, db_session):
    _, _, page, panels, dialogue, _ = _storyboard_fixture(db_session)

    response = client.get(f"/api/v1/pages/{page.id}/storyboard")

    assert response.status_code == 200
    payload = response.json()
    assert payload["page"]["canvas"] == B5_DEFAULT_CANVAS
    for index, panel in enumerate(payload["panels"]):
        assert panel["bounds"] == LEGACY_BOUNDS[index]
        assert panel["geometry"] == {
            "type": "rect",
            "rect": LEGACY_BOUNDS[index],
            "rotation": 0,
            "z_order": index + 1,
        }
    bubble = payload["panels"][0]["dialogues"][0]["bubble"]
    assert bubble["mapped_from_legacy"] is True
    assert bubble["rotation"] == 0
    assert bubble["rect"]["x"] >= 0.5 and bubble["rect"]["y"] <= 0.5
    assert bubble["anchor"]["y"] > bubble["rect"]["y"]
    # Read-path normalization must not write anything back.
    db_session.expire_all()
    assert db_session.get(Panel, panels[0].id).geometry is None
    assert db_session.get(Dialogue, dialogue.id).bubble is None
    assert db_session.get(MangaPage, page.id).canvas is None


def test_read_path_uses_stored_values_and_falls_back_for_unmapped_region(client, db_session):
    _, _, page, panels, dialogue, _ = _storyboard_fixture(db_session)
    stored_geometry = {
        "type": "rect",
        "rect": {"x": 0.1, "y": 0.1, "width": 0.3, "height": 0.3},
        "rotation": 12.5,
        "z_order": 4,
    }
    stored_bubble = {
        "type": "ellipse",
        "rect": {"x": 0.2, "y": 0.2, "width": 0.2, "height": 0.1},
        "rotation": 0,
        "mapped_from_legacy": False,
    }
    custom_canvas = dict(B5_DEFAULT_CANVAS, width_mm=210, height_mm=297)
    panels[1].geometry = stored_geometry
    dialogue.bubble = stored_bubble
    dialogue.region = {"mood": "loud"}
    page.canvas = custom_canvas
    db_session.commit()

    response = client.get(f"/api/v1/pages/{page.id}/storyboard")

    assert response.status_code == 200
    payload = response.json()
    assert payload["page"]["canvas"] == custom_canvas
    assert payload["panels"][1]["geometry"] == stored_geometry
    assert payload["panels"][0]["dialogues"][0]["bubble"] == stored_bubble
    # region without a known preferred anchor stays a pure fallback: no bubble.
    second_dialogue = client.post(
        f"/api/v1/panels/{panels[0].id}/dialogues",
        json={
            "panel_version": db_session.get(Panel, panels[0].id).version,
            "target_text": "まだ。",
            "region": {"mood": "whisper"},
        },
    )
    assert second_dialogue.status_code == 201
    assert second_dialogue.json()["bubble"] is None


# --- L3: geometry validation ------------------------------------------------


def test_panel_rect_validation_rejects_out_of_page_and_tiny_rects(client, db_session):
    _, _, page, panels, _, _ = _storyboard_fixture(db_session)
    panel = panels[0]
    invalid_bounds = [
        {"x": -0.05, "y": 0.1, "width": 0.4, "height": 0.4},
        {"x": 0.1, "y": 0.1, "width": 0.02, "height": 0.4},
        {"x": 0.1, "y": 0.1, "width": 0.4, "height": 0.02},
        {"x": 0.5, "y": 0.1, "width": 0.6, "height": 0.4},
        {"x": 0.1, "y": 0.5, "width": 0.4, "height": 0.6},
        {"x": 1.1, "y": 0.1, "width": 0.4, "height": 0.4},
    ]
    for bounds in invalid_bounds:
        db_session.refresh(panel)
        response = client.patch(
            f"/api/v1/panels/{panel.id}",
            json={"version": panel.version, "bounds": bounds},
        )
        assert response.status_code == 422, bounds


def test_overlapping_panels_are_legal_and_z_order_decides_draw_order(client, db_session):
    _, _, page, panels, _, _ = _storyboard_fixture(db_session)

    response = client.patch(
        f"/api/v1/panels/{panels[1].id}",
        json={
            "version": panels[1].version,
            "bounds": LEGACY_BOUNDS[0],
            "geometry": {
                "type": "rect",
                "rect": LEGACY_BOUNDS[0],
                "rotation": 0,
                "z_order": 7,
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["bounds"] == LEGACY_BOUNDS[0]
    assert payload["geometry"]["z_order"] == 7
    assert payload["geometry"]["rect"] == LEGACY_BOUNDS[0]


def test_polygon_vertex_and_rotation_rules(client, db_session):
    _, _, page, panels, _, _ = _storyboard_fixture(db_session)
    panel = panels[2]
    two_vertices = [
        {"x": 0.1, "y": 0.1},
        {"x": 0.4, "y": 0.2},
    ]
    thirty_three_vertices = [
        {"x": round(0.1 + index * 0.005, 4), "y": 0.2} for index in range(33)
    ]

    db_session.refresh(panel)
    too_few = client.patch(
        f"/api/v1/panels/{panel.id}",
        json={
            "version": panel.version,
            "geometry": {"type": "polygon", "polygon": two_vertices},
        },
    )
    assert too_few.status_code == 422

    db_session.refresh(panel)
    too_many = client.patch(
        f"/api/v1/panels/{panel.id}",
        json={
            "version": panel.version,
            "geometry": {"type": "polygon", "polygon": thirty_three_vertices},
        },
    )
    assert too_many.status_code == 422

    db_session.refresh(panel)
    rotated = client.patch(
        f"/api/v1/panels/{panel.id}",
        json={
            "version": panel.version,
            "geometry": {
                "type": "polygon",
                "polygon": [
                    {"x": 0.1, "y": 0.5},
                    {"x": 0.4, "y": 0.5},
                    {"x": 0.25, "y": 0.8},
                ],
                "rotation": 5,
            },
        },
    )
    assert rotated.status_code == 422

    db_session.refresh(panel)
    valid = client.patch(
        f"/api/v1/panels/{panel.id}",
        json={
            "version": panel.version,
            "geometry": {
                "type": "polygon",
                "polygon": [
                    {"x": 0.1, "y": 0.5},
                    {"x": 0.4, "y": 0.5},
                    {"x": 0.25, "y": 0.8},
                ],
            },
        },
    )
    assert valid.status_code == 200
    assert valid.json()["geometry"]["type"] == "polygon"
    assert valid.json()["geometry"]["z_order"] == panel.reading_order
    assert valid.json()["bounds"] == LEGACY_BOUNDS[2]


def test_bubble_text_region_must_stay_inside_bubble_rect(client, db_session):
    _, _, page, panels, dialogue, _ = _storyboard_fixture(db_session)

    outside = client.patch(
        f"/api/v1/dialogues/{dialogue.id}",
        json={
            "panel_version": panels[0].version,
            "bubble": {
                "type": "rect",
                "rect": {"x": 0.5, "y": 0.1, "width": 0.2, "height": 0.14},
                "text_region": {"x": 0.8, "y": 0.12, "width": 0.1, "height": 0.1},
            },
        },
    )
    assert outside.status_code == 422

    db_session.refresh(panels[0])
    valid = client.patch(
        f"/api/v1/dialogues/{dialogue.id}",
        json={
            "panel_version": panels[0].version,
            "bubble": {
                "type": "rect",
                "rect": {"x": 0.5, "y": 0.1, "width": 0.2, "height": 0.14},
                "anchor": {"x": 0.6, "y": 0.24},
                "tail_target": {"x": 0.6, "y": 0.35},
                "text_region": {"x": 0.52, "y": 0.12, "width": 0.16, "height": 0.1},
            },
        },
    )
    assert valid.status_code == 200
    bubble = valid.json()["bubble"]
    assert bubble["mapped_from_legacy"] is False
    assert bubble["tail_target"] == {"x": 0.6, "y": 0.35}
    db_session.expire_all()
    stored = db_session.get(Dialogue, dialogue.id)
    assert stored.bubble["rect"]["width"] == 0.2
    assert stored.region == {"preferred": "upper_inner"}


# --- L4: panel optimistic lock and whole-page PUT ---------------------------


def test_panel_bounds_optimistic_lock_and_rect_sync(client, db_session):
    _, _, page, panels, _, _ = _storyboard_fixture(db_session)
    panel = panels[1]
    new_bounds = {"x": 0.2, "y": 0.3, "width": 0.4, "height": 0.3}

    stale = client.patch(
        f"/api/v1/panels/{panel.id}",
        json={"version": panel.version + 1, "bounds": new_bounds},
    )
    assert stale.status_code == 409

    geometry_write = client.patch(
        f"/api/v1/panels/{panel.id}",
        json={
            "version": panel.version,
            "geometry": {
                "type": "rect",
                "rect": new_bounds,
                "rotation": 0,
                "z_order": 2,
            },
        },
    )
    assert geometry_write.status_code == 200
    assert geometry_write.json()["bounds"] == new_bounds

    bounds_write = client.patch(
        f"/api/v1/panels/{panel.id}",
        json={
            "version": db_session.get(Panel, panel.id).version,
            "bounds": {"x": 0.3, "y": 0.3, "width": 0.4, "height": 0.3},
        },
    )
    assert bounds_write.status_code == 200
    assert bounds_write.json()["geometry"]["rect"] == bounds_write.json()["bounds"]
    assert bounds_write.json()["geometry"]["z_order"] == 2

    mismatch = client.patch(
        f"/api/v1/panels/{panel.id}",
        json={
            "version": db_session.get(Panel, panel.id).version,
            "bounds": {"x": 0.3, "y": 0.3, "width": 0.4, "height": 0.3},
            "geometry": {
                "type": "rect",
                "rect": {"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2},
            },
        },
    )
    assert mismatch.status_code == 422


def test_put_storyboard_geometry_validates_full_membership(client, db_session):
    _, _, page, panels, dialogue, _ = _storyboard_fixture(db_session)
    base = _geometry_payload(db_session, page)

    stale = client.put(
        f"/api/v1/pages/{page.id}/storyboard-geometry",
        json={**base, "storyboard_version": page.storyboard_version + 1},
    )
    assert stale.status_code == 409

    missing_panel = client.put(
        f"/api/v1/pages/{page.id}/storyboard-geometry",
        json={**base, "panels": base["panels"][:2]},
    )
    assert missing_panel.status_code == 409

    unknown_panel = client.put(
        f"/api/v1/pages/{page.id}/storyboard-geometry",
        json={
            **base,
            "panels": [
                *base["panels"],
                {
                    "panel_id": "panel-not-on-page",
                    "bounds": LEGACY_BOUNDS[0],
                    "reading_order": 4,
                },
            ],
        },
    )
    assert unknown_panel.status_code == 409

    duplicate_panel = client.put(
        f"/api/v1/pages/{page.id}/storyboard-geometry",
        json={**base, "panels": [base["panels"][0], *base["panels"]]},
    )
    assert duplicate_panel.status_code == 422

    missing_dialogue = client.put(
        f"/api/v1/pages/{page.id}/storyboard-geometry",
        json={**base, "dialogues": []},
    )
    assert missing_dialogue.status_code == 409

    foreign_dialogue = client.put(
        f"/api/v1/pages/{page.id}/storyboard-geometry",
        json={
            **base,
            "dialogues": [
                *base["dialogues"],
                {"dialogue_id": "dialogue-elsewhere", "reading_order": 2},
            ],
        },
    )
    assert foreign_dialogue.status_code == 409

    rect_mismatch = client.put(
        f"/api/v1/pages/{page.id}/storyboard-geometry",
        json={
            **base,
            "panels": [
                {
                    **base["panels"][0],
                    "geometry": {
                        "type": "rect",
                        "rect": {"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2},
                    },
                },
                *base["panels"][1:],
            ],
        },
    )
    assert rect_mismatch.status_code == 422


def test_put_request_id_replays_same_payload_and_rejects_different_payload(
    client, db_session
):
    _, _, page, panels, dialogue, _ = _storyboard_fixture(db_session)
    version_before = page.storyboard_version
    payload = _geometry_payload(db_session, page, request_id="replay-1")

    first = client.put(f"/api/v1/pages/{page.id}/storyboard-geometry", json=payload)
    assert first.status_code == 200
    version_after_save = first.json()["page"]["storyboard_version"]
    assert version_after_save == version_before + 1
    assert first.json()["page"]["selected_candidate_ack_version"] is None

    replay = client.put(f"/api/v1/pages/{page.id}/storyboard-geometry", json=payload)
    assert replay.status_code == 200
    assert replay.json()["page"]["storyboard_version"] == version_after_save

    moved = {
        **payload,
        "panels": [
            {**payload["panels"][0], "bounds": {"x": 0.2, "y": 0.2, "width": 0.4, "height": 0.3}},
            *payload["panels"][1:],
        ],
    }
    conflict = client.put(f"/api/v1/pages/{page.id}/storyboard-geometry", json=moved)
    assert conflict.status_code == 409
    assert db_session.get(MangaPage, page.id).storyboard_version == version_after_save


def test_put_replay_persists_across_process_restart_without_version_bump(
    client, db_session
):
    """§10.2: a lost-response retry replays from the persisted row, not memory."""
    _, _, page, _, _, _ = _storyboard_fixture(db_session)
    original_version = page.storyboard_version
    payload = _geometry_payload(db_session, page, request_id="replay-restart-1")

    first = client.put(f"/api/v1/pages/{page.id}/storyboard-geometry", json=payload)
    assert first.status_code == 200
    saved_version = first.json()["page"]["storyboard_version"]
    assert saved_version == original_version + 1

    # simulate a fresh process: drop every ORM instance so the replay tuple
    # can only come from the manga_pages row, not in-process memory
    db_session.expire_all()

    retry = client.put(
        f"/api/v1/pages/{page.id}/storyboard-geometry",
        json={**payload, "storyboard_version": original_version},
    )
    assert retry.status_code == 200
    assert retry.json()["page"]["storyboard_version"] == saved_version

    moved = {
        **payload,
        "panels": [
            {**payload["panels"][0], "bounds": {"x": 0.2, "y": 0.2, "width": 0.4, "height": 0.3}},
            *payload["panels"][1:],
        ],
    }
    conflict = client.put(f"/api/v1/pages/{page.id}/storyboard-geometry", json=moved)
    assert conflict.status_code == 409

    db_session.expire_all()
    stored_page = db_session.get(MangaPage, page.id)
    command = stored_page.geometry_save_command
    assert command["request_id"] == "replay-restart-1"
    assert len(command["payload_hash"]) == 64
    assert command["storyboard_version"] == saved_version
    assert stored_page.storyboard_version == saved_version


def test_put_storyboard_geometry_saves_snapshot_atomically(client, db_session):
    _, _, page, panels, dialogue, _ = _storyboard_fixture(db_session)
    version_before = page.storyboard_version
    reordered = [panels[2], panels[0], panels[1]]
    moved_bounds = {"x": 0.3, "y": 0.3, "width": 0.35, "height": 0.3}
    payload = {
        "request_id": "save-1",
        "storyboard_version": page.storyboard_version,
        "panels": [
            {
                "panel_id": panel.id,
                "bounds": moved_bounds if index == 0 else dict(panel.bounds),
                "geometry": (
                    {
                        "type": "rect",
                        "rect": moved_bounds,
                        "rotation": 0,
                        "z_order": 5,
                    }
                    if index == 0
                    else None
                ),
                "reading_order": index + 1,
            }
            for index, panel in enumerate(reordered)
        ],
        "dialogues": [
            {
                "dialogue_id": dialogue.id,
                "bubble": {
                    "type": "rect",
                    "rect": {"x": 0.4, "y": 0.2, "width": 0.2, "height": 0.14},
                },
                "reading_order": 1,
            }
        ],
    }

    response = client.put(f"/api/v1/pages/{page.id}/storyboard-geometry", json=payload)

    assert response.status_code == 200
    result = response.json()
    assert [panel["reading_order"] for panel in result["panels"]] == [1, 2, 3]
    assert result["panels"][0]["id"] == panels[2].id
    assert result["panels"][0]["bounds"] == moved_bounds
    assert result["panels"][0]["geometry"]["z_order"] == 5
    # cleared stored geometry is re-derived from bounds with z_order=reading_order
    assert result["panels"][1]["geometry"]["z_order"] == 2
    assert result["panels"][1]["bounds"] == LEGACY_BOUNDS[0]
    # the dialogue follows panels[0], which now sits at reading_order 2
    assert result["panels"][1]["dialogues"][0]["bubble"]["mapped_from_legacy"] is False
    db_session.expire_all()
    saved = db_session.get(Panel, panels[2].id)
    assert saved.geometry["z_order"] == 5
    assert db_session.get(Dialogue, dialogue.id).bubble is not None
    assert db_session.get(MangaPage, page.id).storyboard_version == version_before + 1


# --- L5: reading order ------------------------------------------------------


def test_reading_order_renumber_keeps_constraint_and_final_coordinates(
    client, db_session
):
    _, _, page, panels, _, _ = _storyboard_fixture(db_session)
    reversed_ids = [panels[2].id, panels[1].id, panels[0].id]
    version_before = page.storyboard_version

    response = client.patch(
        f"/api/v1/pages/{page.id}/reading-order", json={"order": reversed_ids}
    )

    assert response.status_code == 200
    payload = response.json()
    assert [panel["id"] for panel in payload["panels"]] == reversed_ids
    assert [panel["reading_order"] for panel in payload["panels"]] == [1, 2, 3]
    assert payload["panels"][0]["bounds"] == LEGACY_BOUNDS[2]
    assert payload["page"]["storyboard_version"] == version_before + 1

    duplicate = client.patch(
        f"/api/v1/pages/{page.id}/reading-order",
        json={"order": [panels[0].id, panels[0].id, panels[1].id]},
    )
    assert duplicate.status_code == 422

    unknown = client.patch(
        f"/api/v1/pages/{page.id}/reading-order",
        json={"order": [panels[0].id, panels[1].id, "panel-unknown"]},
    )
    assert unknown.status_code == 409

    partial = client.patch(
        f"/api/v1/pages/{page.id}/reading-order",
        json={"order": [panels[0].id]},
    )
    assert partial.status_code == 409


def test_even_page_read_path_never_remirrors_coordinates(client, db_session):
    _, _, page, panels, _, _ = _storyboard_fixture(db_session, page_number=2)
    assert page.page_number % 2 == 0

    before = client.get(f"/api/v1/pages/{page.id}/storyboard")
    assert before.status_code == 200
    assert [panel["bounds"] for panel in before.json()["panels"]] == LEGACY_BOUNDS

    reordered = client.patch(
        f"/api/v1/pages/{page.id}/reading-order",
        json={"order": [panels[2].id, panels[0].id, panels[1].id]},
    )
    assert reordered.status_code == 200
    bounds_by_id = {
        panel["id"]: panel["bounds"] for panel in reordered.json()["panels"]
    }
    assert [bounds_by_id[panel.id] for panel in panels] == LEGACY_BOUNDS


# --- L6: limits -------------------------------------------------------------


def test_layout_panel_count_supports_three_to_eight(client, db_session):
    _, _, page, panels, _, _ = _storyboard_fixture(db_session)

    eight = client.patch(
        f"/api/v1/pages/{page.id}/layout",
        json={"panel_count": 8, "layout_mode": "dynamic"},
    )
    assert eight.status_code == 200
    assert len(eight.json()["panels"]) == 8
    for panel in eight.json()["panels"]:
        assert panel["bounds"]["width"] >= 0.03
        assert round(panel["bounds"]["x"] + panel["bounds"]["width"], 4) <= 1
        assert panel["geometry"]["z_order"] == panel["reading_order"]

    balanced = client.patch(
        f"/api/v1/pages/{page.id}/layout",
        json={"panel_count": 8, "layout_mode": "balanced"},
    )
    assert balanced.status_code == 200
    assert len(balanced.json()["panels"]) == 8

    too_few = client.patch(
        f"/api/v1/pages/{page.id}/layout", json={"panel_count": 2}
    )
    assert too_few.status_code == 422

    too_many = client.patch(
        f"/api/v1/pages/{page.id}/layout", json={"panel_count": 9}
    )
    assert too_many.status_code == 422


# --- Red team #148 / #163 ---------------------------------------------------


def _paged_chapter_fixture(db_session, *, page_count: int = 10, beats_per_page: int = 1):
    project = Project(name="布局级联失效测试", page_ratio="b5_portrait")
    db_session.add(project)
    db_session.flush()
    chapter = Chapter(project_id=project.id, title="第一章", ordinal=1, status="PAGES_PLANNED")
    db_session.add(chapter)
    db_session.flush()
    scene = Scene(
        chapter_id=chapter.id,
        ordinal=1,
        location="教室",
        source_range={"segment_ids": ["source-1"]},
    )
    db_session.add(scene)
    db_session.flush()
    beats = []
    for index in range(page_count * beats_per_page):
        beat = Beat(
            scene_id=scene.id,
            ordinal=index + 1,
            action=f"荻原桜动作{index + 1}",
            speaker_name="荻原桜",
            dialogue=f"「{index + 1}」",
            source_range={"segment_ids": ["source-1"]},
        )
        db_session.add(beat)
        beats.append(beat)
    db_session.flush()
    pages = []
    for number in range(1, page_count + 1):
        page = MangaPage(
            chapter_id=chapter.id,
            page_number=number,
            scene_ids=[scene.id],
            beat_ids=[
                beat.id
                for beat in beats[(number - 1) * beats_per_page : number * beats_per_page]
            ],
            panel_count=3,
            status="FINAL_READY",
            continuity_status="PASSED",
            selected_candidate_ack_version=1,
            source_coverage={
                "complete": True,
                "ranges": [
                    {
                        "segment_id": "source-1",
                        "start_offset": 0,
                        "end_offset": 4,
                        "text": "荻原桜抬头。",
                    }
                ],
            },
        )
        db_session.add(page)
        pages.append(page)
    db_session.flush()
    for page in pages:
        for reading_order in (1, 2, 3):
            db_session.add(
                Panel(
                    page_id=page.id,
                    reading_order=reading_order,
                    bounds=LEGACY_BOUNDS[reading_order - 1],
                    characters=[],
                    actions={"source_text": "她抬头。"},
                )
            )
    db_session.commit()
    return project, chapter, pages


def test_layout_rebuild_marks_downstream_pages_for_review(client, db_session):
    """#148: a layout rebuild must invalidate every later page's continuity —
    the same chapter-wide mark_pages_for_review cascade its own undo path
    performs — instead of flagging only the rebuilt page."""
    _, chapter, pages = _paged_chapter_fixture(db_session, page_count=10)
    versions_before = {page.id: page.version for page in pages}
    storyboard_version_before = pages[1].storyboard_version

    response = client.patch(
        f"/api/v1/pages/{pages[1].id}/layout",
        json={"panel_count": 5, "layout_mode": "dynamic"},
    )

    assert response.status_code == 200, response.json()
    assert len(response.json()["panels"]) == 5
    db_session.expire_all()
    refreshed = [db_session.get(MangaPage, page.id) for page in pages]
    # Page 1 precedes the rebuilt page and keeps its PASSED continuity.
    assert refreshed[0].continuity_status == "PASSED"
    assert refreshed[0].version == versions_before[pages[0].id]
    # The rebuilt page and every later page are invalidated (pages 2-10).
    for page in refreshed[1:]:
        assert page.continuity_status == "NEEDS_REVIEW"
        assert page.version == versions_before[page.id] + 1
    assert refreshed[1].storyboard_version == storyboard_version_before + 1
    assert refreshed[1].selected_candidate_ack_version is None


def test_layout_rebuild_refuses_fewer_panels_than_beats(client, db_session):
    """#163: lowering panel_count below the page's beat count would orphan
    the excess beats (their dialogue/presence silently vanish) — refuse with
    409 before any panel is deleted."""
    from sqlalchemy import func, select

    _, _, pages = _paged_chapter_fixture(db_session, page_count=1, beats_per_page=5)

    refused = client.patch(
        f"/api/v1/pages/{pages[0].id}/layout",
        json={"panel_count": 3, "layout_mode": "dynamic"},
    )
    assert refused.status_code == 409
    assert "分格数少于情节拍数量" in refused.json()["detail"]
    db_session.expire_all()
    page = db_session.get(MangaPage, pages[0].id)
    assert page.panel_count == 3
    assert (
        db_session.scalar(
            select(func.count(Panel.id)).where(Panel.page_id == page.id)
        )
        == 3
    ), "拒绝路径不得先行删除分镜格"

    allowed = client.patch(
        f"/api/v1/pages/{page.id}/layout",
        json={"panel_count": 5, "layout_mode": "dynamic"},
    )
    assert allowed.status_code == 200, allowed.json()
    assert len(allowed.json()["panels"]) == 5
    db_session.expire_all()
    dialogue_count = db_session.scalar(
        select(func.count(Dialogue.id))
        .join(Panel, Panel.id == Dialogue.panel_id)
        .where(Panel.page_id == page.id)
    )
    assert dialogue_count == 5, "每拍都应获得对应气泡"
    assert "orphan_beat_ids" not in db_session.get(MangaPage, page.id).source_coverage


def test_readiness_surfaces_orphaned_beats_as_non_blocking_warning(
    client, db_session, tmp_path, monkeypatch
):
    """#163: pages carrying more beats than panels surface a WARNING-level
    readiness finding (visible, review-worthy) without blocking generation."""
    from test_page_readiness import _base_page, _enable_assets_style_provider

    project, page, panel, characters = _base_page(db_session)
    _enable_assets_style_provider(
        db_session, tmp_path, monkeypatch, project, page, panel, characters
    )

    baseline = client.get(f"/api/v1/pages/{page.id}/readiness").json()
    assert baseline["ready"] is True
    assert "ORPHANED_PAGE_BEATS" not in {item["code"] for item in baseline["blockers"]}

    # Structural overflow: more beats than the 4 default panels (legacy page
    # shape, no stored marker needed).
    extra_beats = [
        Beat(
            scene_id=page.scene_ids[0],
            ordinal=index,
            action=f"动作{index}",
            source_range={"segment_ids": ["segment-1"]},
        )
        for index in range(2, 6)
    ]
    db_session.add_all(extra_beats)
    db_session.flush()
    page.beat_ids = [*page.beat_ids, *[beat.id for beat in extra_beats]]
    db_session.commit()

    flagged = client.get(f"/api/v1/pages/{page.id}/readiness").json()
    orphan_findings = [
        item for item in flagged["blockers"] if item["code"] == "ORPHANED_PAGE_BEATS"
    ]
    assert len(orphan_findings) == 1
    assert orphan_findings[0]["severity"] == "WARNING"
    assert "情节拍未入板" in orphan_findings[0]["message"]
    # The warning alone must not block production readiness.
    assert flagged["ready"] is True
    assert flagged["blockers"] == orphan_findings


def test_geometry_schemas_reject_unknown_keys(client, db_session):
    _, _, page, panels, dialogue, _ = _storyboard_fixture(db_session)

    panel_extra = client.patch(
        f"/api/v1/panels/{panels[0].id}",
        json={
            "version": panels[0].version,
            "geometry": {
                "type": "rect",
                "rect": LEGACY_BOUNDS[0],
                "shear": 0.5,
            },
        },
    )
    assert panel_extra.status_code == 422

    bubble_extra = client.patch(
        f"/api/v1/dialogues/{dialogue.id}",
        json={
            "panel_version": panels[0].version,
            "bubble": {
                "type": "rect",
                "rect": {"x": 0.5, "y": 0.1, "width": 0.2, "height": 0.14},
                "mapped_from_legacy": True,
            },
        },
    )
    assert bubble_extra.status_code == 422

    payload = _geometry_payload(db_session, page)
    put_extra = client.put(
        f"/api/v1/pages/{page.id}/storyboard-geometry",
        json={**payload, "panels": [{**payload["panels"][0], "bleed": True}]},
    )
    assert put_extra.status_code == 422


def test_page_bubble_cap_stays_at_eight(client, db_session):
    _, _, page, panels, dialogue, _ = _storyboard_fixture(db_session)

    for index in range(7):
        created = client.post(
            f"/api/v1/panels/{panels[0].id}/dialogues",
            json={
                "panel_version": db_session.get(Panel, panels[0].id).version,
                "target_text": f"セリフ{index}",
            },
        )
        assert created.status_code == 201

    ninth = client.post(
        f"/api/v1/panels/{panels[0].id}/dialogues",
        json={
            "panel_version": db_session.get(Panel, panels[0].id).version,
            "target_text": "9個目",
        },
    )
    assert ninth.status_code == 422


# --- L7: version invalidation ----------------------------------------------


def test_geometry_save_invalidates_candidates_based_on_old_version(client, db_session):
    project, chapter, page, panels, dialogue, _ = _storyboard_fixture(db_session)
    candidate = _add_candidate(db_session, project, chapter, page)
    version_before = page.storyboard_version
    assert candidate_version_state(candidate, db_session.get(MangaPage, page.id)) == (
        "CURRENT",
        [],
    )

    payload = _geometry_payload(db_session, page, request_id="invalidate-1")
    saved = client.put(f"/api/v1/pages/{page.id}/storyboard-geometry", json=payload)
    assert saved.status_code == 200

    db_session.expire_all()
    page_after = db_session.get(MangaPage, page.id)
    assert page_after.storyboard_version == version_before + 1
    assert page_after.selected_candidate_ack_version is None
    assert candidate_version_state(candidate, page_after) == ("STALE", ["STORYBOARD_CHANGED"])


def test_panel_patch_also_marks_storyboard_changed(client, db_session):
    project, chapter, page, panels, dialogue, _ = _storyboard_fixture(db_session)
    _add_candidate(db_session, project, chapter, page)
    version_before = page.storyboard_version

    response = client.patch(
        f"/api/v1/panels/{panels[0].id}",
        json={
            "version": panels[0].version,
            "bounds": {"x": 0.05, "y": 0.05, "width": 0.9, "height": 0.4},
        },
    )

    assert response.status_code == 200
    db_session.expire_all()
    page_after = db_session.get(MangaPage, page.id)
    assert page_after.storyboard_version == version_before + 1
    assert page_after.selected_candidate_ack_version is None


# --- L8: sound effects ------------------------------------------------------


def test_sound_effects_legacy_strings_are_wrapped_on_read(client, db_session):
    _, _, page, panels, _, _ = _storyboard_fixture(
        db_session, sound_effects=["ドンッ", "ゴゴゴ"]
    )

    response = client.get(f"/api/v1/pages/{page.id}/storyboard")

    assert response.status_code == 200
    effects = response.json()["panels"][0]["sound_effects"]
    assert effects == [
        {"text": "ドンッ", "x": None, "y": None, "rotation": 0, "size": None},
        {"text": "ゴゴゴ", "x": None, "y": None, "rotation": 0, "size": None},
    ]


def test_sound_effects_structured_write_validation(client, db_session):
    _, _, page, panels, _, _ = _storyboard_fixture(db_session)
    panel = panels[0]

    valid = client.patch(
        f"/api/v1/panels/{panel.id}",
        json={
            "version": panel.version,
            "sound_effects": [
                {"text": "ドンッ", "x": 0.3, "y": 0.5, "rotation": -15, "size": 0.12},
                "レガシー",
            ],
        },
    )
    assert valid.status_code == 200
    # every read wraps legacy strings, including ones written in the same PATCH
    assert valid.json()["sound_effects"] == [
        {"text": "ドンッ", "x": 0.3, "y": 0.5, "rotation": -15, "size": 0.12},
        {"text": "レガシー", "x": None, "y": None, "rotation": 0, "size": None},
    ]

    db_session.expire_all()
    stored = db_session.get(Panel, panel.id).sound_effects
    assert stored[0]["rotation"] == -15

    bad_rotation = client.patch(
        f"/api/v1/panels/{panel.id}",
        json={
            "version": db_session.get(Panel, panel.id).version,
            "sound_effects": [{"text": "ドンッ", "rotation": -400}],
        },
    )
    assert bad_rotation.status_code == 422

    out_of_range = client.patch(
        f"/api/v1/panels/{panel.id}",
        json={
            "version": db_session.get(Panel, panel.id).version,
            "sound_effects": [{"text": "ドンッ", "x": 1.5}],
        },
    )
    assert out_of_range.status_code == 422

    overflow = client.patch(
        f"/api/v1/panels/{panel.id}",
        json={
            "version": db_session.get(Panel, panel.id).version,
            "sound_effects": [
                {"text": f"effect-{index}", "x": 0.1, "y": 0.1} for index in range(33)
            ],
        },
    )
    assert overflow.status_code == 422
