from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from app import worker_tasks
from app.config import get_settings
from app.domain.states import JobStatus, Resolution
from app.models import (
    Asset,
    AssetCandidate,
    Dialogue,
    GenerationBatch,
    GenerationJob,
    GenerationRecord,
    MangaPage,
    Outfit,
    PageCandidate,
    Panel,
    Project,
    Scene,
    Beat,
    Chapter,
    Character,
    CharacterReference,
    InspectionResult,
    ScriptRevision,
    SourceSegment,
)
from app.services.prompt_compiler import compile_page_prompt
from app.services.ai_schemas import (
    BeatDraft,
    CharacterDraft,
    SceneDraft,
    StoryParseOutput,
)
from app.services.worker_handlers import provider
from app.services.worker_handlers.page_generate import _load_reference_assets
from app.services.worker_handlers.story_parse import (
    _merge_story_parse_outputs,
    _run_story_parse,
    _story_parse_chunks,
)


def _project(client, name="长篇测试"):
    return client.post("/api/v1/projects", json={"name": name}).json()


def _skip_page_readiness(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.page_readiness.ensure_page_ready",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.ordinal_allocator.ensure_page_ready",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.api.routes.workflow.ensure_page_ready",
        lambda *_args, **_kwargs: None,
    )


def _chapter_and_pages(client, db_session, project_id: str, repeat: int = 12):
    paragraph = "苏清白推开教室的门。她看见窗边的顾川，轻声问道：“你怎么还在这里？”"
    imported = client.post(
        f"/api/v1/projects/{project_id}/sources/import",
        json={"title": "第一章", "text": "\n\n".join([paragraph] * repeat)},
    )
    assert imported.status_code == 201
    chapter = imported.json()["chapters"][0]
    segments = (
        db_session.query(SourceSegment)
        .filter(
            SourceSegment.source_revision_id == chapter["current_source_revision_id"]
        )
        .order_by(SourceSegment.ordinal)
        .all()
    )
    for index in range(0, len(segments), 3):
        group = segments[index : index + 3]
        scene = Scene(
            chapter_id=chapter["id"],
            ordinal=index // 3 + 1,
            location=f"场景 {index // 3 + 1}",
            source_range={"segment_ids": [item.id for item in group]},
        )
        db_session.add(scene)
        db_session.flush()
        for beat_index, segment in enumerate(group, 1):
            db_session.add(
                Beat(
                    scene_id=scene.id,
                    ordinal=beat_index,
                    action=segment.text,
                    source_range={"segment_ids": [segment.id]},
                )
            )
    db_session.add(
        ScriptRevision(
            chapter_id=chapter["id"],
            source_revision_id=chapter["current_source_revision_id"],
            revision_no=1,
            status="READY",
            coverage={
                "expected": len(segments),
                "covered": len(segments),
                "ratio": 1,
                "missing_segment_ids": [],
            },
        )
    )
    chapter_record = db_session.get(Chapter, chapter["id"])
    chapter_record.status = "SCRIPT_READY"
    db_session.commit()
    planned = client.post(
        f"/api/v1/chapters/{chapter['id']}/plan",
        json={"replace_existing": True},
    )
    assert planned.status_code == 200
    return chapter, planned.json()


def test_lossless_import_and_dynamic_pagination(client, db_session):
    project = _project(client)
    short_chapter, short = _chapter_and_pages(
        client, db_session, project["id"], repeat=4
    )
    long_chapter, long = _chapter_and_pages(
        client, db_session, project["id"], repeat=16
    )

    assert short["coverage_ratio"] == 1
    assert long["coverage_ratio"] == 1
    assert long["page_count"] > short["page_count"]
    assert all(page["estimated_text_chars"] <= 180 for page in long["pages"])
    assert all(3 <= page["panel_count"] <= 5 for page in long["pages"])
    assert (
        short_chapter["source_character_count"] < long_chapter["source_character_count"]
    )
    first_page_id = long["pages"][0]["id"]
    original_second_page_id = long["pages"][1]["id"]
    replanned = client.post(
        f"/api/v1/chapters/{long_chapter['id']}/plan",
        json={"replace_existing": True, "from_page_number": 2},
    )
    assert replanned.status_code == 200
    assert replanned.json()["coverage_ratio"] == 1
    assert replanned.json()["pages"][0]["id"] == first_page_id
    assert replanned.json()["pages"][1]["id"] != original_second_page_id
    assert [page["page_number"] for page in replanned.json()["pages"]] == list(
        range(1, replanned.json()["page_count"] + 1)
    )


def test_split_source_segment_assigns_each_script_beat_to_its_page(client, db_session):
    project = _project(client, "跨页情节拍")
    source_parts = [
        "第一幕细雨落在京都的屋檐上，主人公站在窗前凝视远方，屋内只听见钟表缓慢走动的声音。",
        "第二幕他接到家里的电话，匆忙收拾行李赶往车站，沿途的阳光与沉重消息形成强烈反差。",
        "第三幕他终于跪在父亲灵牌前失声痛哭，母亲站在一旁，既悲伤又担忧地望着自己的孩子。",
        "第四幕母亲轻轻抱住他安慰，两个人在沉默里接受亲人离去的事实，随后才一起回到家中。",
    ]
    source_parts = [part + part for part in source_parts]
    imported = client.post(
        f"/api/v1/projects/{project['id']}/sources/import",
        json={"title": "第一章", "text": "".join(source_parts)},
    ).json()["chapters"][0]
    segment = (
        db_session.query(SourceSegment)
        .filter_by(source_revision_id=imported["current_source_revision_id"])
        .one()
    )
    scene = Scene(
        chapter_id=imported["id"],
        ordinal=1,
        location="京都",
        source_range={"segment_ids": [segment.id]},
    )
    db_session.add(scene)
    db_session.flush()
    beats = [
        Beat(
            scene_id=scene.id,
            ordinal=index,
            action=part,
            narration=part,
            source_range={"segment_ids": [segment.id]},
        )
        for index, part in enumerate(source_parts, 1)
    ]
    db_session.add_all(beats)
    chapter = db_session.get(Chapter, imported["id"])
    chapter.status = "SCRIPT_READY"
    db_session.commit()

    response = client.post(
        f"/api/v1/chapters/{chapter.id}/plan",
        json={"replace_existing": True},
    )
    assert response.status_code == 200
    pages = response.json()["pages"]
    assert len(pages) >= 2
    first_ids = set(pages[0]["beat_ids"])
    second_ids = set(pages[1]["beat_ids"])
    assert first_ids
    assert second_ids
    assert first_ids.isdisjoint(second_ids)
    assert first_ids | second_ids == {beat.id for beat in beats}


def test_txt_and_markdown_source_upload(client):
    project = _project(client, "文件原文导入")
    uploaded = client.post(
        f"/api/v1/projects/{project['id']}/sources/upload",
        data={"title": "上传章节"},
        files={
            "file": (
                "chapter.md",
                "# 第一章\n\n原文内容必须完整保留。".encode(),
                "text/markdown",
            )
        },
    )
    assert uploaded.status_code == 201
    assert uploaded.json()["chapters"][0]["title"] == "上传章节"
    assert uploaded.json()["total_characters"] > 0
    unsupported = client.post(
        f"/api/v1/projects/{project['id']}/sources/upload",
        data={"title": "错误格式"},
        files={"file": ("chapter.docx", b"not-a-docx", "application/octet-stream")},
    )
    assert unsupported.status_code == 415


def test_story_parse_worker_writes_complete_traceable_script(
    client, db_session, monkeypatch
):
    project = _project(client, "AI 剧本生成")
    imported = client.post(
        f"/api/v1/projects/{project['id']}/sources/import",
        json={
            "title": "第一章",
            "text": "顾川推开门。\n\n他看向窗边，轻声说：“我来了。”",
        },
    ).json()
    chapter = db_session.get(Chapter, imported["chapters"][0]["id"])
    segments = (
        db_session.query(SourceSegment)
        .filter(SourceSegment.source_revision_id == chapter.current_source_revision_id)
        .order_by(SourceSegment.ordinal)
        .all()
    )

    class FakeTextAdapter:
        def generate_structured(self, request, schema):
            assert schema is StoryParseOutput
            assert all(segment.id in request.prompt for segment in segments)
            assert request.metadata == {
                "max_output_tokens": 8192,
                "thinking_budget": 0,
            }
            return StoryParseOutput(
                characters=[CharacterDraft(primary_name="顾川", aliases=["小川"])],
                scenes=[
                    SceneDraft(
                        ordinal=1,
                        location="教室",
                        purpose="顾川登场",
                        source_segment_ids=[segment.id for segment in segments],
                        beats=[
                            BeatDraft(
                                ordinal=index,
                                action=segment.text,
                                speaker_name="小川" if index == len(segments) else "",
                                dialogue="我来了。" if index == len(segments) else "",
                                source_segment_ids=[segment.id],
                            )
                            for index, segment in enumerate(segments, 1)
                        ],
                    )
                ],
            )

    monkeypatch.setattr("app.worker_tasks._adapter", lambda alias: FakeTextAdapter())
    job = GenerationJob(
        project_id=project["id"],
        target_type="CHAPTER",
        target_id=chapter.id,
        job_type="SOURCE_PARSE",
        status="PREPARING",
        model_alias="text.fast",
    )
    db_session.add(job)
    db_session.flush()
    _run_story_parse(db_session, job)

    db_session.commit()

    db_session.refresh(chapter)
    script = db_session.query(ScriptRevision).filter_by(chapter_id=chapter.id).one()
    beats = (
        db_session.query(Beat).join(Scene).filter(Scene.chapter_id == chapter.id).all()
    )
    assert chapter.status == "SCRIPT_READY"
    assert script.coverage["ratio"] == 1
    assert script.coverage["missing_segment_ids"] == []
    assert len(beats) == len(segments)
    assert beats[-1].speaker_name == "顾川"


def test_story_parse_chunks_large_sources_and_merges_recurring_characters():
    segments = [
        SourceSegment(
            source_revision_id="revision",
            ordinal=index,
            text="字" * size,
            start_offset=0,
            end_offset=size,
            sha256=str(index),
        )
        for index, size in enumerate((500, 400, 300), 1)
    ]
    assert [len(chunk) for chunk in _story_parse_chunks(segments)] == [1, 2]

    outputs = [
        StoryParseOutput(
            characters=[CharacterDraft(primary_name="顾川", aliases=["小川"])],
            scenes=[SceneDraft(ordinal=9, beats=[])],
        ),
        StoryParseOutput(
            characters=[CharacterDraft(primary_name="小川", aliases=["顾川"])],
            scenes=[SceneDraft(ordinal=4, beats=[])],
        ),
    ]
    merged = _merge_story_parse_outputs(outputs)

    assert len(merged.characters) == 1
    assert merged.characters[0].primary_name == "顾川"
    assert [scene.ordinal for scene in merged.scenes] == [1, 2]


def test_story_parse_reuses_user_character_when_model_returns_alias(
    client, db_session, monkeypatch
):
    project = _project(client, "角色别名复用")
    canonical = Character(
        project_id=project["id"],
        primary_name="荻原桜",
        aliases=["桜"],
        aliases_normalized=["桜"],
        status="UPLOADED",
    )
    db_session.add(canonical)
    imported = client.post(
        f"/api/v1/projects/{project['id']}/sources/import",
        json={"title": "第一章", "text": "妹妹桜轻声说：“哥哥。”"},
    ).json()
    chapter = db_session.get(Chapter, imported["chapters"][0]["id"])
    segment = (
        db_session.query(SourceSegment)
        .filter_by(source_revision_id=chapter.current_source_revision_id)
        .one()
    )

    class FakeTextAdapter:
        def generate_structured(self, request, schema):
            assert schema is StoryParseOutput
            return StoryParseOutput(
                characters=[
                    CharacterDraft(
                        primary_name="桜",
                        aliases=["妹妹"],
                        description="主角的妹妹",
                    )
                ],
                scenes=[
                    SceneDraft(
                        ordinal=1,
                        location="家中",
                        source_segment_ids=[segment.id],
                        beats=[
                            BeatDraft(
                                ordinal=1,
                                action="桜开口",
                                speaker_name="妹妹",
                                dialogue="哥哥。",
                                source_segment_ids=[segment.id],
                            )
                        ],
                    )
                ],
            )

    monkeypatch.setattr("app.worker_tasks._adapter", lambda alias: FakeTextAdapter())
    job = GenerationJob(
        project_id=project["id"],
        target_type="CHAPTER",
        target_id=chapter.id,
        job_type="SOURCE_PARSE",
        status="PREPARING",
        model_alias="text.fast",
    )
    db_session.add(job)
    db_session.flush()
    _run_story_parse(db_session, job)
    db_session.commit()

    characters = db_session.query(Character).filter_by(project_id=project["id"]).all()
    beat = (
        db_session.query(Beat).join(Scene).filter(Scene.chapter_id == chapter.id).one()
    )
    assert len(characters) == 1
    assert characters[0].primary_name == "荻原桜"
    assert characters[0].aliases == ["桜", "妹妹"]
    assert characters[0].canonical_description == "主角的妹妹"
    assert beat.speaker_name == "荻原桜"


def test_page_plan_persists_rtl_storyboard_panels(client, db_session):
    project = _project(client, "分镜测试")
    _, planned = _chapter_and_pages(client, db_session, project["id"], repeat=6)
    first = planned["pages"][0]
    panels = (
        db_session.query(Panel)
        .filter(Panel.page_id == first["id"])
        .order_by(Panel.reading_order)
        .all()
    )
    assert len(panels) == first["panel_count"]
    assert [panel.reading_order for panel in panels] == list(range(1, len(panels) + 1))
    assert panels[0].bounds["x"] > panels[1].bounds["x"]
    assert (
        db_session.query(Dialogue)
        .filter(Dialogue.panel_id.in_([item.id for item in panels]))
        .count()
    )
    assert len({(item.bounds["width"], item.bounds["height"]) for item in panels}) > 1
    assert all(panel.actions["script_action"].strip() for panel in panels)
    assert len({panel.actions["script_action"] for panel in panels}) == len(panels)
    assert all(panel.background and panel.background != "延续当前场景" for panel in panels)


def test_delete_outfit_cascades_exclusive_references_generated_images_and_bindings(
    client, db_session
):
    project = _project(client, "服装档案级联删除")
    character = Character(project_id=project["id"], primary_name="我")
    exclusive_reference = Asset(
        project_id=project["id"],
        kind="OUTFIT_REFERENCE",
        original_name="exclusive.png",
        storage_key="exclusive.png",
        mime_type="image/png",
        byte_size=10,
        sha256="1" * 64,
    )
    shared_reference = Asset(
        project_id=project["id"],
        kind="OUTFIT_REFERENCE",
        original_name="shared.png",
        storage_key="shared.png",
        mime_type="image/png",
        byte_size=10,
        sha256="2" * 64,
    )
    generated_image = Asset(
        project_id=project["id"],
        kind="OUTFIT_REFERENCE",
        original_name="generated.png",
        storage_key="generated.png",
        mime_type="image/png",
        byte_size=10,
        sha256="3" * 64,
        source="GENERATED",
    )
    db_session.add_all(
        [character, exclusive_reference, shared_reference, generated_image]
    )
    db_session.flush()
    outfit = Outfit(
        project_id=project["id"],
        character_id=character.id,
        name="葬礼正装",
        reference_asset_ids=[exclusive_reference.id, shared_reference.id],
    )
    other_outfit = Outfit(
        project_id=project["id"],
        character_id=character.id,
        name="共享参考",
        reference_asset_ids=[shared_reference.id],
    )
    chapter = Chapter(project_id=project["id"], title="第一章", ordinal=1)
    db_session.add_all([outfit, other_outfit, chapter])
    db_session.flush()
    scene = Scene(
        chapter_id=chapter.id,
        ordinal=1,
        outfit_assignments={character.id: outfit.id},
    )
    page = MangaPage(chapter_id=chapter.id, page_number=1)
    batch = GenerationBatch(
        project_id=project["id"],
        target_type="OUTFIT",
        target_id=outfit.id,
        ordinal=1,
        generation_kind="OUTFIT",
    )
    db_session.add_all([scene, page, batch])
    db_session.flush()
    panel = Panel(
        page_id=page.id,
        reading_order=1,
        outfits={character.id: outfit.id},
    )
    candidate = AssetCandidate(
        batch_id=batch.id,
        ordinal=1,
        model_alias="image.fast",
        resolution=Resolution.DRAFT_1K,
        variant="OUTFIT",
        asset_id=generated_image.id,
        status="READY",
    )
    db_session.add_all([panel, candidate])
    db_session.commit()

    response = client.delete(f"/api/v1/outfits/{outfit.id}")

    assert response.status_code == 204, response.text
    assert db_session.get(Outfit, outfit.id) is None
    db_session.refresh(exclusive_reference)
    db_session.refresh(shared_reference)
    db_session.refresh(generated_image)
    db_session.refresh(candidate)
    db_session.refresh(scene)
    db_session.refresh(panel)
    assert exclusive_reference.deleted_at is not None
    assert generated_image.deleted_at is not None
    assert candidate.deleted_at is not None
    assert shared_reference.deleted_at is None
    assert scene.outfit_assignments == {}
    assert panel.outfits == {}


def test_page_generation_only_loads_references_for_characters_on_page(
    client, db_session
):
    project_data = _project(client, "页面人物参考隔离")
    _, planned = _chapter_and_pages(client, db_session, project_data["id"], repeat=1)
    project = db_session.get(Project, project_data["id"])
    page = db_session.get(MangaPage, planned["pages"][0]["id"])
    on_page = Character(project_id=project.id, primary_name="本页角色")
    off_page = Character(project_id=project.id, primary_name="后续角色")
    on_page_asset = Asset(
        project_id=project.id,
        kind="CHARACTER_REFERENCE",
        original_name="on-page.png",
        storage_key="on-page.png",
        mime_type="image/png",
        byte_size=10,
        sha256="d" * 64,
    )
    off_page_asset = Asset(
        project_id=project.id,
        kind="CHARACTER_REFERENCE",
        original_name="off-page.png",
        storage_key="off-page.png",
        mime_type="image/png",
        byte_size=10,
        sha256="e" * 64,
    )
    db_session.add_all([on_page, off_page, on_page_asset, off_page_asset])
    db_session.flush()
    db_session.add_all(
        [
            CharacterReference(
                character_id=on_page.id,
                asset_id=on_page_asset.id,
                is_canonical=True,
            ),
            CharacterReference(
                character_id=off_page.id,
                asset_id=off_page_asset.id,
                is_canonical=True,
            ),
        ]
    )
    panel = db_session.query(Panel).filter_by(page_id=page.id).first()
    panel.characters = [on_page.id]
    db_session.commit()

    reference_ids = {
        asset.id for asset in _load_reference_assets(db_session, page, project)
    }
    assert on_page_asset.id in reference_ids
    assert off_page_asset.id not in reference_ids


def test_canonical_speaker_flows_from_script_to_panel_prompt(client, db_session):
    project = _project(client, "说话人追踪")
    character = Character(
        project_id=project["id"], primary_name="顾川", aliases=["老顾"]
    )
    db_session.add(character)
    imported = client.post(
        f"/api/v1/projects/{project['id']}/sources/import",
        json={"title": "对白章", "text": "顾川看向门口，说：“你来了。”"},
    ).json()["chapters"][0]
    segment = (
        db_session.query(SourceSegment)
        .filter(
            SourceSegment.source_revision_id == imported["current_source_revision_id"]
        )
        .one()
    )
    scene = Scene(
        chapter_id=imported["id"],
        ordinal=1,
        location="教室",
        source_range={"segment_ids": [segment.id]},
    )
    db_session.add(scene)
    db_session.flush()
    db_session.add(
        Beat(
            scene_id=scene.id,
            ordinal=1,
            action="顾川看向门口",
            speaker_name="顾川",
            dialogue="你来了。",
            source_range={"segment_ids": [segment.id]},
        )
    )
    db_session.add(
        ScriptRevision(
            chapter_id=imported["id"],
            source_revision_id=imported["current_source_revision_id"],
            revision_no=1,
            status="READY",
            coverage={
                "expected": 1,
                "covered": 1,
                "ratio": 1,
                "missing_segment_ids": [],
            },
        )
    )
    chapter = db_session.get(Chapter, imported["id"])
    chapter.status = "SCRIPT_READY"
    db_session.commit()
    page_data = client.post(
        f"/api/v1/chapters/{chapter.id}/plan",
        json={"replace_existing": True},
    ).json()["pages"][0]
    dialogue = (
        db_session.query(Dialogue)
        .join(Panel, Panel.id == Dialogue.panel_id)
        .filter(Panel.page_id == page_data["id"])
        .first()
    )
    assert dialogue.speaker_character_id == character.id
    page = db_session.get(MangaPage, page_data["id"])
    project_record = db_session.get(Project, project["id"])
    _, snapshot = compile_page_prompt(db_session, page, project_record)
    assert snapshot["input"]["page"]["layout"][0]["dialogues"][0]["speaker"] == "顾川"


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
    resolved = client.patch(
        f"/api/v1/characters/{second.json()['id']}",
        json={
            "version": second.json()["version"],
            "primary_name": "白露",
            "aliases": ["露露"],
            "locked_features": ["银色短发", "右眼泪痣"],
            "forbidden_changes": ["不得改变发色"],
        },
    )
    assert resolved.status_code == 200
    assert resolved.json()["alias_conflict"] is False
    assert resolved.json()["status"] == "CANONICAL"
    assert resolved.json()["locked_features"] == ["银色短发", "右眼泪痣"]
    assert resolved.json()["forbidden_changes"] == ["不得改变发色"]


def test_page_plan_requires_complete_script(client):
    project = _project(client, "剧本门禁")
    imported = client.post(
        f"/api/v1/projects/{project['id']}/sources/import",
        json={"title": "第一章", "text": "原文不能绕过剧本直接分页。"},
    ).json()
    response = client.post(
        f"/api/v1/chapters/{imported['chapters'][0]['id']}/plan",
        json={"replace_existing": True},
    )
    assert response.status_code == 409
    assert "剧本" in response.json()["detail"]


def test_chapter_delete_restore_and_source_revision(client):
    project = _project(client, "可逆导入")
    chapter = client.post(
        f"/api/v1/projects/{project['id']}/sources/import",
        json={"title": "第一章", "text": "第一版原文。"},
    ).json()["chapters"][0]
    revised = client.post(
        f"/api/v1/chapters/{chapter['id']}/revisions",
        json={
            "title": "第一章（修订）",
            "text": "第二版完整原文。",
            "source_type": "PASTE",
        },
    )
    assert revised.status_code == 201
    assert revised.json()["revision"] == 2
    assert len(client.get(f"/api/v1/chapters/{chapter['id']}/revisions").json()) == 2
    assert client.delete(f"/api/v1/chapters/{chapter['id']}").status_code == 204
    assert client.get(f"/api/v1/projects/{project['id']}/chapters").json() == []
    assert client.post(f"/api/v1/chapters/{chapter['id']}/restore").status_code == 200


def test_batch_candidate_favorite_select_and_next(client, db_session, monkeypatch):
    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    _skip_page_readiness(monkeypatch)
    project = _project(client, "抽卡测试")
    _, plan = _chapter_and_pages(client, db_session, project["id"], repeat=12)
    first_page, second_page = plan["pages"][:2]

    batch = client.post(f"/api/v1/pages/{first_page['id']}/batches")
    assert batch.status_code == 201
    queued = client.post(
        f"/api/v1/batches/{batch.json()['id']}/candidates",
        json={
            "model_alias": "image.nano_banana_2",
            "resolution": "1K",
            "storyboard_version": first_page["storyboard_version"],
        },
    )
    assert queued.status_code == 202
    candidate = queued.json()["candidate"]
    assert candidate["model_alias"] == "image.nano_banana_2"
    assert (
        client.get(f"/api/v1/projects/{project['id']}").json()["last_image_model_alias"]
        == "image.nano_banana_2"
    )

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
    record.status = "INSPECTED"
    for category, score in [
        ("SPEAKER", 0.95),
        ("CHARACTER", 0.99),
        ("OUTFIT", 0.99),
        ("PROP", 0.99),
        ("CONTINUITY", 0.99),
    ]:
        db_session.add(
            InspectionResult(
                candidate_id=record.id,
                storyboard_version=first_page["storyboard_version"],
                category=category,
                outcome="PASS",
                score=score,
                severity="INFO",
            )
        )
    db_session.commit()

    selected = client.post(
        f"/api/v1/pages/{first_page['id']}/select-candidate",
        json={"candidate_id": candidate["id"], "manual_text_confirmed": True},
    )
    assert selected.status_code == 200
    assert selected.json()["selected_candidate_id"] == candidate["id"]
    assert (
        client.post(f"/api/v1/pages/{first_page['id']}/next").json()["id"]
        == second_page["id"]
    )

    library = client.get(
        f"/api/v1/projects/{project['id']}/library?group_by=batch&favorite=true"
    ).json()
    assert library["favorite_count"] == 1
    assert library["groups"][0]["candidates"][0]["is_selected"] is True
    filtered = client.get(
        f"/api/v1/projects/{project['id']}/library",
        params={
            "group_by": "batch",
            "model_alias": "image.nano_banana_2",
            "resolution": "1K",
            "generation_kind": "PAGE",
            "date_from": "2000-01-01T00:00:00Z",
        },
    ).json()
    assert filtered["total_candidates"] == 1
    assert (
        client.get(
            f"/api/v1/projects/{project['id']}/library",
            params={"group_by": "batch", "resolution": "2K"},
        ).json()["total_candidates"]
        == 0
    )


def test_candidate_requires_explicit_neutral_model(client, db_session, monkeypatch):
    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    _skip_page_readiness(monkeypatch)
    project = _project(client, "模型选择")
    _, plan = _chapter_and_pages(client, db_session, project["id"], repeat=2)
    batch = client.post(f"/api/v1/pages/{plan['pages'][0]['id']}/batches").json()
    response = client.post(
        f"/api/v1/batches/{batch['id']}/candidates",
        json={
            "model_alias": "auto",
            "resolution": "1K",
            "storyboard_version": plan["pages"][0]["storyboard_version"],
        },
    )
    assert response.status_code == 422
    assert "显式选择" in response.text


def test_character_concept_without_references_uses_generate_capability(
    client, db_session, monkeypatch
):
    from app.services import model_router

    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    project = _project(client, "无参考概念图")
    character = client.post(
        f"/api/v1/projects/{project['id']}/characters",
        json={"primary_name": "林澄", "aliases": []},
    ).json()
    operations: list[str] = []
    original_resolve = model_router.resolve_model

    def record_operation(*args, operation: str, **kwargs):
        operations.append(operation)
        return original_resolve(*args, operation=operation, **kwargs)

    monkeypatch.setattr("app.services.ordinal_allocator.resolve_model", record_operation)
    monkeypatch.setattr("app.services.model_router.resolve_model", record_operation)
    response = client.post(
        f"/api/v1/characters/{character['id']}/complete-sheet",
        json={
            "model_alias": "image.nano_banana_2",
            "resolution": "1K",
            "generation_mode": "CONCEPT",
            "appearance_description": "黑色短发，冷静克制",
            "outfit_name": "日常制服",
            "outfit_description": "深色简洁制服",
        },
    )

    assert response.status_code == 202, response.text
    assert operations == ["image_generate"]

    class BindingReached(Exception):
        pass

    def stop_at_binding(*args, operation: str, **kwargs):
        operations.append(operation)
        raise BindingReached

    monkeypatch.setattr(provider, "_binding", stop_at_binding)
    job = db_session.get(GenerationJob, response.json()["job_id"])
    with pytest.raises(BindingReached):
        worker_tasks._run_asset_generate(db_session, job)
    assert operations == ["image_generate", "image_generate"]


def test_asset_generation_batches_join_library(client, db_session, monkeypatch):
    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    project = _project(client, "角色补图")
    character = client.post(
        f"/api/v1/projects/{project['id']}/characters",
        json={"primary_name": "苏清白", "aliases": ["小白"]},
    ).json()
    reference = Asset(
        project_id=project["id"],
        kind="CHARACTER_REFERENCE",
        original_name="face.png",
        storage_key="face.png",
        mime_type="image/png",
        byte_size=10,
        sha256="d" * 64,
    )
    db_session.add(reference)
    db_session.commit()
    assert (
        client.post(
            f"/api/v1/characters/{character['id']}/references",
            json={"asset_id": reference.id, "is_canonical": True},
        ).status_code
        == 201
    )
    batch = client.post(
        "/api/v1/asset-generation-batches",
        json={
            "target_type": "CHARACTER",
            "target_id": character["id"],
            "generation_kind": "CHARACTER",
        },
    )
    assert batch.status_code == 201
    automatic = client.post(
        f"/api/v1/asset-generation-batches/{batch.json()['id']}/candidates",
        json={
            "model_alias": "auto",
            "resolution": "1K",
            "variant": "SIDE",
            "instruction": "",
        },
    )
    assert automatic.status_code == 422
    assert "必须显式选择" in automatic.text
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
    assert (
        client.get(f"/api/v1/projects/{project['id']}").json()["last_image_model_alias"]
        == "image.nano_banana_2"
    )
    library = client.get(
        f"/api/v1/projects/{project['id']}/library?group_by=batch"
    ).json()
    assert library["groups"][0]["batch"]["generation_kind"] == "CHARACTER"
    character_library = client.get(
        f"/api/v1/projects/{project['id']}/library",
        params={"group_by": "batch", "character_id": character["id"]},
    ).json()
    assert character_library["total_candidates"] == 1
    assert (
        client.delete(
            f"/api/v1/candidates/{candidate.json()['candidate']['id']}"
        ).status_code
        == 204
    )
    assert (
        client.get(f"/api/v1/projects/{project['id']}/library?group_by=batch").json()[
            "total_candidates"
        ]
        == 0
    )


def test_reference_profiles_scene_wardrobe_and_complete_sheet(
    client, db_session, monkeypatch
):
    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    project = _project(client, "设定闭环")
    character = client.post(
        f"/api/v1/projects/{project['id']}/characters",
        json={"primary_name": "苏清白", "aliases": ["小白"]},
    ).json()
    character_asset = Asset(
        project_id=project["id"],
        kind="CHARACTER_REFERENCE",
        original_name="char.png",
        storage_key="char.png",
        mime_type="image/png",
        byte_size=10,
        sha256="a" * 64,
    )
    outfit_asset = Asset(
        project_id=project["id"],
        kind="OUTFIT_REFERENCE",
        original_name="uniform.png",
        storage_key="uniform.png",
        mime_type="image/png",
        byte_size=10,
        sha256="b" * 64,
    )
    outfit_asset_alt = Asset(
        project_id=project["id"],
        kind="OUTFIT_REFERENCE",
        original_name="uniform-back.png",
        storage_key="uniform-back.png",
        mime_type="image/png",
        byte_size=10,
        sha256="d" * 64,
    )
    style_asset = Asset(
        project_id=project["id"],
        kind="STYLE_REFERENCE",
        original_name="style.png",
        storage_key="style.png",
        mime_type="image/png",
        byte_size=10,
        sha256="c" * 64,
    )
    db_session.add_all([character_asset, outfit_asset, outfit_asset_alt, style_asset])
    db_session.commit()
    assert (
        client.post(
            f"/api/v1/characters/{character['id']}/references",
            json={"asset_id": character_asset.id, "is_canonical": True},
        ).status_code
        == 201
    )
    outfit = client.post(
        f"/api/v1/projects/{project['id']}/outfits",
        json={
            "character_id": character["id"],
            "name": "校服",
            "reference_asset_ids": [outfit_asset.id],
        },
    ).json()
    rebound_outfit = client.patch(
        f"/api/v1/outfits/{outfit['id']}",
        json={
            "version": outfit["version"],
            "name": "校服",
            "reference_asset_ids": [outfit_asset_alt.id],
            "locked_fields": ["领结", "裙长"],
        },
    )
    assert rebound_outfit.status_code == 200
    outfit = rebound_outfit.json()
    assert outfit["reference_asset_ids"] == [outfit_asset_alt.id]
    style = client.post(
        f"/api/v1/projects/{project['id']}/styles",
        json={
            "name": "彩色漫画",
            "color_mode": "color",
            "profile": {
                "palette_confirmed": True,
                "test_image_approved": True,
            },
            "reference_asset_ids": [style_asset.id],
        },
    ).json()
    assert (
        client.post(
            f"/api/v1/projects/{project['id']}/styles/{style['id']}/activate"
        ).status_code
        == 200
    )
    current_style = client.get(f"/api/v1/projects/{project['id']}/styles").json()[0]
    color_style = client.patch(
        f"/api/v1/styles/{style['id']}",
        json={"version": current_style["version"], "color_mode": "color"},
    )
    assert color_style.status_code == 200
    assert color_style.json()["color_mode"] == "color"
    assert color_style.json()["profile"]["reference_asset_ids"] == [style_asset.id]
    assert client.post(f"/api/v1/styles/{style['id']}/analyze").status_code == 202
    sheet = client.post(
        f"/api/v1/characters/{character['id']}/complete-sheet",
        json={"model_alias": "image.nano_banana_2", "resolution": "1K"},
    )
    assert sheet.status_code == 202
    assert sheet.json()["candidate"]["variant"] == "SHEET"
    batches = client.get(
        "/api/v1/asset-generation-batches",
        params={"target_type": "CHARACTER", "target_id": character["id"]},
    )
    assert batches.status_code == 200
    assert batches.json()[0]["id"] == sheet.json()["candidate"]["batch_id"]
    candidates = client.get(
        f"/api/v1/batches/{batches.json()[0]['id']}/candidates"
    )
    assert candidates.status_code == 200
    assert candidates.json()[0]["variant"] == "SHEET"

    mismatch = client.post(
        "/api/v1/asset-generation-batches",
        json={
            "target_type": "OUTFIT",
            "target_id": outfit["id"],
            "generation_kind": "CHARACTER",
        },
    )
    assert mismatch.status_code == 422
    outfit_batch = client.post(
        "/api/v1/asset-generation-batches",
        json={
            "target_type": "OUTFIT",
            "target_id": outfit["id"],
            "generation_kind": "OUTFIT",
        },
    )
    assert outfit_batch.status_code == 201
    invalid_outfit_variant = client.post(
        f"/api/v1/asset-generation-batches/{outfit_batch.json()['id']}/candidates",
        json={
            "model_alias": "image.nano_banana_2",
            "resolution": "1K",
            "variant": "STYLE_TEST",
        },
    )
    assert invalid_outfit_variant.status_code == 422
    outfit_candidate = client.post(
        f"/api/v1/asset-generation-batches/{outfit_batch.json()['id']}/candidates",
        json={
            "model_alias": "image.nano_banana_2",
            "resolution": "1K",
            "variant": "OUTFIT",
        },
    )
    assert outfit_candidate.status_code == 202
    style_batch = client.post(
        "/api/v1/asset-generation-batches",
        json={
            "target_type": "STYLE",
            "target_id": style["id"],
            "generation_kind": "STYLE_TEST",
        },
    )
    assert style_batch.status_code == 201
    style_candidate = client.post(
        f"/api/v1/asset-generation-batches/{style_batch.json()['id']}/candidates",
        json={
            "model_alias": "image.nano_banana_2",
            "resolution": "1K",
            "variant": "STYLE_TEST",
        },
    )
    assert style_candidate.status_code == 202

    _, plan = _chapter_and_pages(client, db_session, project["id"], repeat=3)
    scene = (
        db_session.query(Scene).filter(Scene.chapter_id == plan["chapter_id"]).first()
    )
    assigned = client.patch(
        f"/api/v1/scenes/{scene.id}/outfits",
        json={"assignments": {character["id"]: outfit["id"]}},
    )
    assert assigned.status_code == 200
    page = db_session.get(MangaPage, plan["pages"][0]["id"])
    project_record = db_session.get(Project, project["id"])
    prompt, snapshot = compile_page_prompt(db_session, page, project_record)
    assert (
        snapshot["input"]["scene_outfits"][0]["assignments"][character["id"]]
        == outfit["id"]
    )
    assert snapshot["input"]["style"]["id"] == style["id"]
    assert snapshot["input"]["style"]["color_mode"] == "color"
    assert "彩色日式漫画" in prompt
    assert "稳定肤色发色与服装配色" in prompt


def test_eight_candidate_jobs_are_isolated(client, db_session, monkeypatch):
    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    _skip_page_readiness(monkeypatch)
    project = _project(client, "并发隔离")
    _, plan = _chapter_and_pages(client, db_session, project["id"], repeat=2)
    batch = client.post(f"/api/v1/pages/{plan['pages'][0]['id']}/batches").json()
    job_ids = []
    for index in range(8):
        alias = "image.nano_banana_2"
        response = client.post(
            f"/api/v1/batches/{batch['id']}/candidates",
            json={
                "model_alias": alias,
                "resolution": "1K",
                "storyboard_version": plan["pages"][0]["storyboard_version"],
            },
        )
        assert response.status_code == 202
        job_ids.append(response.json()["job_id"])
    failed = db_session.get(GenerationJob, job_ids[0])
    failed.status = JobStatus.FAILED
    failed.error_code = "FAKE_FAILURE"
    db_session.commit()
    jobs = {
        item["id"]: item
        for item in client.get(f"/api/v1/projects/{project['id']}/jobs").json()
    }
    assert jobs[job_ids[0]]["status"] == "FAILED"
    assert all(jobs[job_id]["status"] == "WAITING" for job_id in job_ids[1:])


def test_completed_image_job_exposes_clickable_result(client, db_session):
    project = _project(client, "任务结果入口")
    chapter, plan = _chapter_and_pages(client, db_session, project["id"], repeat=2)
    page = db_session.get(MangaPage, plan["pages"][0]["id"])
    batch = GenerationBatch(
        project_id=project["id"],
        chapter_id=chapter["id"],
        page_id=page.id,
        ordinal=1,
        generation_kind="PAGE",
        status="CLOSED",
    )
    asset = Asset(
        project_id=project["id"],
        kind="page_candidate",
        original_name="job-result.png",
        storage_key="generated/job-result.png",
        mime_type="image/png",
        byte_size=10,
        sha256="9" * 64,
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
        status="READY",
        asset_id=asset.id,
    )
    db_session.add(candidate)
    db_session.flush()
    job = GenerationJob(
        project_id=project["id"],
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_GENERATE",
        status=JobStatus.COMPLETED,
    )
    db_session.add(job)
    db_session.flush()
    candidate.job_id = job.id
    db_session.commit()

    listed = client.get(f"/api/v1/projects/{project['id']}/jobs")

    assert listed.status_code == 200
    result = next(item for item in listed.json() if item["id"] == job.id)["result"]
    assert result == {
        "kind": "IMAGE",
        "label": "页面候选 1 · 1K",
        "candidate_id": candidate.id,
        "page_id": page.id,
        "content_url": f"/api/v1/assets/{asset.id}/content",
        "thumbnail_url": f"/api/v1/assets/{asset.id}/thumbnail/640",
    }


def test_job_history_archive_restore_and_safe_delete(client, db_session):
    project = _project(client, "任务历史")
    completed = GenerationJob(
        project_id=project["id"],
        target_type="PROJECT",
        target_id=project["id"],
        job_type="SOURCE_PARSE",
        status=JobStatus.COMPLETED,
    )
    failed_unreferenced = GenerationJob(
        project_id=project["id"],
        target_type="PROJECT",
        target_id=project["id"],
        job_type="SOURCE_PARSE",
        status=JobStatus.FAILED,
    )
    failed_referenced = GenerationJob(
        project_id=project["id"],
        target_type="PROJECT",
        target_id=project["id"],
        job_type="PAGE_GENERATE",
        status=JobStatus.FAILED,
    )
    running = GenerationJob(
        project_id=project["id"],
        target_type="PROJECT",
        target_id=project["id"],
        job_type="SOURCE_PARSE",
        status=JobStatus.WAITING,
    )
    db_session.add_all([completed, failed_unreferenced, failed_referenced, running])
    db_session.flush()
    db_session.add(
        GenerationRecord(
            job_id=failed_referenced.id,
            model_id="fake-image-model",
            location="global",
            parameters={},
            prompt_template="test",
            prompt_version="1",
            prompt_checksum="0" * 64,
            input_versions={},
        )
    )
    db_session.commit()

    archived = client.post(f"/api/v1/jobs/{completed.id}/archive")
    assert archived.status_code == 200
    assert archived.json()["archived_at"] is not None
    assert client.post(f"/api/v1/jobs/{running.id}/archive").status_code == 409

    cleared = client.post(
        f"/api/v1/projects/{project['id']}/jobs/archive-completed"
    )
    assert cleared.status_code == 200
    assert cleared.json()["archived_count"] == 2
    recent_ids = {
        item["id"]
        for item in client.get(f"/api/v1/projects/{project['id']}/jobs").json()
    }
    assert recent_ids == {running.id}
    history_ids = {
        item["id"]
        for item in client.get(
            f"/api/v1/projects/{project['id']}/jobs?archived=true"
        ).json()
    }
    assert history_ids == {
        completed.id,
        failed_unreferenced.id,
        failed_referenced.id,
    }

    restored = client.post(f"/api/v1/jobs/{completed.id}/restore")
    assert restored.status_code == 200
    assert restored.json()["archived_at"] is None
    assert client.delete(f"/api/v1/jobs/{completed.id}").status_code == 409
    assert client.delete(f"/api/v1/jobs/{failed_referenced.id}").status_code == 409
    assert client.delete(f"/api/v1/jobs/{failed_unreferenced.id}").status_code == 204


def test_inspection_repair_escalation_and_upscale_jobs(client, db_session, monkeypatch):
    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    _skip_page_readiness(monkeypatch)
    project = _project(client, "检查修复升清")
    _, plan = _chapter_and_pages(client, db_session, project["id"], repeat=2)
    page = plan["pages"][0]
    batch = client.post(f"/api/v1/pages/{page['id']}/batches").json()
    candidate_data = client.post(
        f"/api/v1/batches/{batch['id']}/candidates",
        json={
            "model_alias": "image.nano_banana_2",
            "resolution": "1K",
            "storyboard_version": page["storyboard_version"],
        },
    ).json()["candidate"]
    asset = Asset(
        project_id=project["id"],
        kind="page_candidate",
        original_name="review.png",
        storage_key="generated/review.png",
        mime_type="image/png",
        byte_size=10,
        sha256="d" * 64,
        source="VERTEX_GENERATED",
        status="GENERATED",
    )
    db_session.add(asset)
    db_session.flush()
    candidate = db_session.get(PageCandidate, candidate_data["id"])
    candidate.asset_id = asset.id
    candidate.status = "READY"
    inspection = InspectionResult(
        candidate_id=candidate.id,
        storyboard_version=db_session.get(MangaPage, candidate.page_id).storyboard_version,
        category="CHARACTER",
        outcome="MISMATCH",
        score=0.4,
        severity="ERROR",
        details={"expected": "角色面部一致", "observed": "角色特征偏离"},
        regions=[{"x": 0.6, "y": 0.1, "width": 0.2, "height": 0.2}],
    )
    db_session.add(inspection)
    db_session.commit()

    checked = client.post(
        f"/api/v1/candidates/{candidate.id}/inspect",
        json={"categories": ["CHARACTER", "SPEAKER"]},
    )
    assert checked.status_code == 202
    assert checked.json()["job_type"] == "PAGE_INSPECT"

    for repair_type in ["BUBBLE_REGION", "PANEL", "PAGE"]:
        repaired = client.post(
            f"/api/v1/candidates/{candidate.id}/repairs",
            json={
                "inspection_result_id": inspection.id,
                "repair_type": repair_type,
                "target_regions": [],
                "target_fields": [],
                "model_alias": "image.nano_banana_2",
                "resolution": "1K",
            },
        )
        assert repaired.status_code == 202
        repair_job = db_session.get(GenerationJob, repaired.json()["job_id"])
        assert repair_job.job_type == "PAGE_REPAIR"

    blocked = client.post(
        f"/api/v1/candidates/{candidate.id}/repairs",
        json={
            "inspection_result_id": inspection.id,
            "repair_type": "PAGE",
            "model_alias": "image.nano_banana_2",
            "resolution": "1K",
        },
    )
    assert blocked.status_code == 409
    assert "最大自动修复次数" in blocked.json()["detail"]

    upscaled = client.post(
        f"/api/v1/candidates/{candidate.id}/upscale",
        json={"model_alias": "image.nano_banana_pro", "resolution": "4K"},
    )
    assert upscaled.status_code == 202
    upscale_job = db_session.get(GenerationJob, upscaled.json()["job_id"])
    assert upscale_job.job_type == "PAGE_UPSCALE"
    upscale_batch = db_session.get(
        GenerationBatch, upscaled.json()["candidate"]["batch_id"]
    )
    assert upscale_batch.generation_kind == "UPSCALE"
    assert (
        client.get(f"/api/v1/projects/{project['id']}").json()["last_image_model_alias"]
        == "image.nano_banana_pro"
    )

    invalid = client.post(
        f"/api/v1/candidates/{candidate.id}/upscale",
        json={"model_alias": "image.nano_banana_2", "resolution": "1K"},
    )
    assert invalid.status_code == 422


def test_project_json_export_uses_selected_page_versions(
    client, db_session, monkeypatch
):
    project = _project(client, "导出测试")
    chapter, plan = _chapter_and_pages(client, db_session, project["id"], repeat=2)
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
            status="INSPECTED",
            asset_id=asset.id,
            is_selected=True,
        )
        db_session.add(candidate)
        db_session.flush()
        page.selected_candidate_id = candidate.id
        page.selected_candidate_ack_version = page.storyboard_version
        page.continuity_status = "PASSED"
        for category in ("SPEAKER", "CHARACTER", "OUTFIT", "PROP", "CONTINUITY"):
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
        assert len(downloaded.json()["asset_manifest"]) == len(plan["pages"])
        first_page = db_session.get(MangaPage, plan["pages"][0]["id"])
        first_candidate = db_session.get(
            PageCandidate, first_page.selected_candidate_id
        )
        first_asset = db_session.get(Asset, first_candidate.asset_id)
        page_path = Path(directory) / first_asset.storage_key
        page_path.parent.mkdir(parents=True, exist_ok=True)
        page_path.write_bytes(b"PNG_PAGE")
        page_download = client.get(f"/api/v1/pages/{first_page.id}/export.png")
        assert page_download.status_code == 200
        assert page_download.content == b"PNG_PAGE"
    assert not Path(directory).exists()
