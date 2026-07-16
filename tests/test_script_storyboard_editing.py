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
from app.domain.states import Resolution


def _editable_story(db_session):
    project = Project(name="编辑台测试")
    db_session.add(project)
    db_session.flush()
    chapter = Chapter(project_id=project.id, title="第一章", ordinal=1, status="PAGES_PLANNED")
    character = Character(project_id=project.id, primary_name="荻原桜", aliases=["桜", "妹妹"])
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
    pages = [
        MangaPage(
            chapter_id=chapter.id,
            page_number=number,
            scene_ids=[scene.id],
            beat_ids=[beat.id],
            panel_count=3,
            estimated_text_chars=5,
            estimated_bubbles=1,
            source_coverage={
                "complete": True,
                "ranges": [
                    {
                        "segment_id": "source-1",
                        "start_offset": 0,
                        "end_offset": 5,
                        "text": "荻原桜抬头。",
                    }
                ],
            },
        )
        for number in (1, 2)
    ]
    db_session.add_all(pages)
    db_session.flush()
    panel = Panel(
        page_id=pages[0].id,
        reading_order=1,
        characters=[character.id],
        actions={"source_text": "她抬头。", "script_action": "荻原桜抬头"},
        expressions={character.id: "惊讶"},
        background="教室",
    )
    db_session.add(panel)
    db_session.flush()
    dialogue = Dialogue(
        panel_id=panel.id,
        speaker_character_id=character.id,
        target_text="你来了。",
        reading_order=1,
    )
    batch = GenerationBatch(
        project_id=project.id,
        chapter_id=chapter.id,
        page_id=pages[0].id,
        ordinal=1,
        generation_kind="PAGE",
    )
    db_session.add_all([dialogue, batch])
    db_session.flush()
    db_session.add(
        PageCandidate(
            batch_id=batch.id,
            page_id=pages[0].id,
            ordinal=1,
            model_alias="image.nano_banana_2",
            resolution=Resolution.DRAFT_1K,
            status="READY",
        )
    )
    db_session.commit()
    return project, chapter, character, scene, beat, pages, panel, dialogue


def test_script_edits_are_versioned_and_preserve_generated_candidates(client, db_session):
    _, _, _, scene, beat, pages, _, _ = _editable_story(db_session)

    scene_response = client.patch(
        f"/api/v1/scenes/{scene.id}",
        json={"version": scene.version, "location": "放学后的教室", "purpose": "确认重逢"},
    )
    assert scene_response.status_code == 200
    assert scene_response.json()["location"] == "放学后的教室"
    assert scene_response.json()["source_range"] == {"segment_ids": ["source-1"]}

    beat_response = client.patch(
        f"/api/v1/beats/{beat.id}",
        json={
            "version": beat.version,
            "speaker_name": "妹妹",
            "dialogue": "你终于来了。",
            "emotion": "松了一口气",
        },
    )
    assert beat_response.status_code == 200
    assert beat_response.json()["speaker_name"] == "荻原桜"
    assert beat_response.json()["dialogue"] == "你终于来了。"
    assert beat_response.json()["source_range"] == {"segment_ids": ["source-1"]}
    assert all(db_session.get(MangaPage, page.id).continuity_status == "NEEDS_REVIEW" for page in pages)
    assert db_session.query(PageCandidate).count() == 1

    stale = client.patch(
        f"/api/v1/beats/{beat.id}",
        json={"version": 1, "dialogue": "冲突修改"},
    )
    assert stale.status_code == 409


def test_storyboard_panel_and_dialogue_are_editable(client, db_session):
    _, _, character, _, _, pages, panel, dialogue = _editable_story(db_session)

    storyboard = client.get(f"/api/v1/pages/{pages[0].id}/storyboard")
    assert storyboard.status_code == 200
    assert storyboard.json()["panels"][0]["dialogues"][0]["target_text"] == "你来了。"

    panel_response = client.patch(
        f"/api/v1/panels/{panel.id}",
        json={
            "version": panel.version,
            "shot_type": "close_up",
            "camera_angle": "low_angle",
            "characters": [character.id],
            "actions": {"source_text": "篡改原文", "script_action": "荻原桜猛地回头"},
            "background": "夕阳照进空教室",
        },
    )
    assert panel_response.status_code == 200
    panel_data = panel_response.json()
    assert panel_data["shot_type"] == "close_up"
    assert panel_data["actions"]["script_action"] == "荻原桜猛地回头"
    assert panel_data["actions"]["source_text"] == "她抬头。"

    dialogue_response = client.patch(
        f"/api/v1/dialogues/{dialogue.id}",
        json={
            "panel_version": panel_data["version"],
            "target_text": "你终于来了。",
            "speaker_character_id": character.id,
            "text_direction": "horizontal",
        },
    )
    assert dialogue_response.status_code == 200
    assert dialogue_response.json()["target_text"] == "你终于来了。"
    page = db_session.get(MangaPage, pages[0].id)
    assert page.estimated_text_chars == 6
    assert page.continuity_status == "NEEDS_REVIEW"
    assert db_session.get(MangaPage, pages[1].id).continuity_status == "NEEDS_REVIEW"
    assert db_session.query(PageCandidate).count() == 1

    current_panel = db_session.get(Panel, panel.id)
    added = client.post(
        f"/api/v1/panels/{panel.id}/dialogues",
        json={
            "panel_version": current_panel.version,
            "target_text": "雨还在下。",
            "speaker_character_id": None,
            "text_direction": "vertical",
            "rewrite_forbidden": True,
        },
    )
    assert added.status_code == 201
    current_panel = db_session.get(Panel, panel.id)
    removed = client.request(
        "DELETE",
        f"/api/v1/dialogues/{added.json()['id']}",
        json={"panel_version": current_panel.version},
    )
    assert removed.status_code == 204

    invalid = client.patch(
        f"/api/v1/panels/{panel.id}",
        json={"version": db_session.get(Panel, panel.id).version, "characters": ["missing"]},
    )
    assert invalid.status_code == 409


def test_storyboard_layout_can_reflow_three_to_five_panels_from_script(client, db_session):
    _, _, _, _, _, pages, _, _ = _editable_story(db_session)

    response = client.patch(
        f"/api/v1/pages/{pages[0].id}/layout",
        json={"panel_count": 5, "layout_mode": "dynamic"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["page"]["panel_count"] == 5
    assert payload["page"]["source_coverage"]["layout_mode"] == "dynamic"
    assert len(payload["panels"]) == 5
    assert len({tuple(panel["bounds"].values()) for panel in payload["panels"]}) == 5
    assert db_session.query(PageCandidate).count() == 1

    balanced = client.patch(
        f"/api/v1/pages/{pages[0].id}/layout",
        json={"panel_count": 3, "layout_mode": "balanced"},
    )
    assert balanced.status_code == 200
    assert balanced.json()["page"]["source_coverage"]["layout_mode"] == "balanced"
    assert len(balanced.json()["panels"]) == 3
