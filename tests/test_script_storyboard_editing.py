import pytest
from fastapi import HTTPException

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
    ScriptRevision,
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


def test_generated_script_can_be_deleted_before_pagination(client, db_session):
    project = Project(name="删除剧本测试")
    db_session.add(project)
    db_session.commit()
    imported = client.post(
        f"/api/v1/projects/{project.id}/sources/import",
        json={"title": "第一章", "text": "雨夜里，她推开旧书店的门。", "source_type": "PASTE"},
    )
    assert imported.status_code == 201
    chapter_id = imported.json()["chapters"][0]["id"]
    chapter = db_session.get(Chapter, chapter_id)
    scene = Scene(chapter_id=chapter_id, ordinal=1, location="旧书店")
    db_session.add_all(
        [
            scene,
            ScriptRevision(
                chapter_id=chapter_id,
                source_revision_id=chapter.current_source_revision_id,
                revision_no=1,
                status="READY",
                coverage={"ratio": 1},
            ),
        ]
    )
    chapter.status = "SCRIPT_READY"
    db_session.commit()
    db_session.add(Beat(scene_id=scene.id, ordinal=1, action="她推门而入"))
    db_session.commit()
    previous_version = chapter.version

    response = client.delete(f"/api/v1/chapters/{chapter_id}/script")

    assert response.status_code == 204
    db_session.expire_all()
    chapter = db_session.get(Chapter, chapter_id)
    assert chapter.status == "IMPORTED"
    assert chapter.version == previous_version + 1
    assert db_session.query(Scene).filter_by(chapter_id=chapter_id).count() == 0
    assert db_session.query(ScriptRevision).filter_by(chapter_id=chapter_id).count() == 0
    script = client.get(f"/api/v1/chapters/{chapter_id}/script")
    assert script.status_code == 200
    assert script.json()["status"] == "NOT_CREATED"


def test_generated_script_delete_cascades_pagination_and_candidates(client, db_session):
    _, chapter, _, _, _, pages, _, _ = _editable_story(db_session)

    response = client.delete(f"/api/v1/chapters/{chapter.id}/script")

    assert response.status_code == 204
    db_session.expire_all()
    assert all(db_session.get(MangaPage, page.id) is None for page in pages)
    assert db_session.query(GenerationBatch).filter_by(chapter_id=chapter.id).count() == 0
    assert db_session.query(PageCandidate).count() == 0
    assert db_session.query(Scene).filter_by(chapter_id=chapter.id).count() == 0
    assert db_session.get(Chapter, chapter.id).status == "IMPORTED"


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


def test_panel_patch_claim_is_atomic_across_sessions(tmp_path):
    """Two writers validating the same panel version must not both succeed:
    the conditional claim UPDATE makes the loser get 409 instead of silently
    overwriting the winner's edit (read-then-compare left both passing)."""

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database import Base
    from app.models import Chapter, MangaPage, Project

    engine = create_engine(
        f"sqlite:///{(tmp_path / 'panel-cas.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    with factory() as db:
        project = Project(name="CAS 并发")
        db.add(project)
        db.flush()
        chapter = Chapter(project_id=project.id, ordinal=1, title="第一章")
        db.add(chapter)
        db.flush()
        page = MangaPage(
            chapter_id=chapter.id,
            page_number=1,
            source_coverage={"complete": True},
        )
        db.add(page)
        db.flush()
        panel = Panel(page_id=page.id, reading_order=1)
        db.add(panel)
        db.commit()
        panel_id, expected = panel.id, panel.version

    from app.api.routes.workflow.storyboard import _claim_panel_version

    with factory() as first, factory() as second:
        first_panel = first.get(Panel, panel_id)
        second_panel = second.get(Panel, panel_id)
        _claim_panel_version(first, first_panel, expected)
        first.commit()

        with pytest.raises(HTTPException) as conflict:
            _claim_panel_version(second, second_panel, expected)
        assert conflict.value.status_code == 409

        first.rollback()
        second.rollback()
    engine.dispose()


def test_revise_source_rejected_while_parse_is_running(client, db_session):
    """Revising text under a running SOURCE_PARSE leaves dangling segment
    references and an uncoverable new revision; the guard must 409."""

    from app.models import GenerationJob

    project = Project(name="解析中改稿")
    db_session.add(project)
    db_session.flush()
    chapter = Chapter(project_id=project.id, ordinal=1, title="第一章")
    db_session.add(chapter)
    db_session.commit()

    db_session.add(
        GenerationJob(
            project_id=project.id,
            target_type="CHAPTER",
            target_id=chapter.id,
            job_type="SOURCE_PARSE",
            status="GENERATING",
        )
    )
    db_session.commit()

    rejected = client.post(
        f"/api/v1/chapters/{chapter.id}/revisions",
        json={"text": "改过的原文", "source_type": "PASTE"},
    )
    assert rejected.status_code == 409
    assert "解析任务正在执行" in rejected.json()["detail"]
