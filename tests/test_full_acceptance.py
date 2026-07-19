from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from PIL import Image

from app.config import get_settings
from app.domain.states import JobStatus
from app.model_adapters.base import ModelResponse
from app.models import (
    AssetCandidate,
    Character,
    GenerationJob,
    PageCandidate,
    SourceSegment,
    StyleProfile,
)
from app.services.ai_schemas import (
    BeatDraft,
    CharacterDraft,
    InspectionItem,
    PageInspectionOutput,
    SceneDraft,
    StoryParseOutput,
    StyleAnalysisOutput,
)
from app.worker_tasks import (
    _run_asset_generate,
    _run_inspection,
    _run_page_generate,
    _run_story_parse,
    _run_style_analyze,
)


def _png_bytes(color: tuple[int, int, int]) -> bytes:
    output = BytesIO()
    Image.new("RGB", (48, 64), color).save(output, format="PNG")
    return output.getvalue()


def _finish_job(db_session, job_id: str, runner) -> None:
    job = db_session.get(GenerationJob, job_id)
    assert job is not None
    job.status = JobStatus.PREPARING
    job.error_code = None
    job.error_message = None
    job.attempt_count += 1
    runner(db_session, job)
    job.status = JobStatus.COMPLETED
    job.progress = 100
    db_session.commit()


class FakeAcceptanceAdapter:
    def __init__(self, segments: list[SourceSegment]):
        self.segments = segments
        self.generated_image = _png_bytes((245, 245, 240))
        self.page_prompts: list[str] = []
        self.asset_prompts: list[str] = []
        self.request_index = 0
        self.inspection_index = 0

    def _response(self) -> ModelResponse:
        self.request_index += 1
        return ModelResponse(
            model_id="fake-vertex-image",
            request_id=f"fake-request-{self.request_index}",
            usage={"fake": True},
            images=(self.generated_image,),
        )

    def generate_structured(self, request, output_schema):
        assert output_schema is StoryParseOutput
        requested_segments = [
            segment for segment in self.segments if segment.id in request.prompt
        ]
        assert requested_segments
        scenes = []
        for scene_index, offset in enumerate(range(0, len(requested_segments), 4), 1):
            group = requested_segments[offset : offset + 4]
            scenes.append(
                SceneDraft(
                    ordinal=scene_index,
                    location=f"京都旧宅 · 场景 {scene_index}",
                    time_label="雨天傍晚",
                    weather="小雨",
                    purpose="忠实推进原文事件",
                    emotional_arc="压抑到理解",
                    source_segment_ids=[segment.id for segment in group],
                    beats=[
                        BeatDraft(
                            ordinal=beat_index,
                            action=segment.text,
                            speaker_name="小白" if beat_index % 2 else "顾川",
                            dialogue="我会把事情说清楚。",
                            emotion="克制",
                            subtext="两人都在隐藏担忧",
                            importance=0.8,
                            page_turn_hook=beat_index == len(group),
                            source_segment_ids=[segment.id],
                        )
                        for beat_index, segment in enumerate(group, 1)
                    ],
                )
            )
        return StoryParseOutput(
            characters=[
                CharacterDraft(
                    primary_name="苏清白",
                    aliases=["小白"],
                    description="黑色长发、右眼下有泪痣的高中女生",
                    source_segment_ids=[segment.id for segment in requested_segments],
                ),
                CharacterDraft(
                    primary_name="顾川",
                    aliases=["小川"],
                    description="短发、神情克制的高中男生",
                    source_segment_ids=[segment.id for segment in requested_segments],
                ),
            ],
            scenes=scenes,
        )

    def analyze_multimodal(self, request, output_schema):
        assert request.images
        if output_schema is StyleAnalysisOutput:
            return StyleAnalysisOutput(
                line_art="利落细线与局部粗线强调",
                screentone="低密度网点表现雨雾",
                contrast="人物高对比、背景中灰",
                panel_language="右到左日式分格，情绪格适度留白",
                character_rendering="写实比例与克制表情",
                background_rendering="京都旧宅保留建筑细节",
                lighting="阴天柔光",
                composition_rules=["关键反应使用近景", "跨场景使用留白转场"],
                negative_rules=["禁止高饱和霓虹色", "禁止复制参考页文字"],
                prompt_summary="彩色日式漫画，细线稿、低饱和色板、克制留白和右到左阅读。",
                palette={
                    "primary": ["#343746", "#69717D"],
                    "skin": "#E4C1AF",
                    "hair": "#272638",
                    "environment": ["#74858A", "#A7B3B5"],
                    "light": "#D8DEDE",
                },
                color_rules=["人物略暖，雨夜环境偏冷", "服装保持低饱和深色"],
            )
        assert output_schema is PageInspectionOutput
        self.inspection_index += 1
        categories = ["SPEAKER", "CHARACTER", "OUTFIT", "PROP", "CONTINUITY"]
        return PageInspectionOutput(
            items=[
                InspectionItem(
                    category=category,
                    outcome="PASS",
                    score=0.98,
                    severity="INFO",
                    details={
                        "expected": "结构化目标",
                        "observed": "符合目标",
                    },
                    regions=[],
                )
                for category in categories
            ]
        )

    def generate_page(self, request):
        self.page_prompts.append(request.prompt)
        return self._response()

    def generate_asset(self, request):
        assert request.reference_images
        self.asset_prompts.append(request.prompt)
        return self._response()


def test_1500_to_3000_character_full_manga_acceptance(
    client, db_session, monkeypatch
):
    with TemporaryDirectory() as directory:
        root = Path(directory)
        settings = get_settings()
        monkeypatch.setattr(settings, "queue_enabled", False)
        monkeypatch.setattr(settings, "storage_root", root / "storage")
        monkeypatch.setattr(settings, "upload_root", root / "uploads")
        monkeypatch.setattr(
            "app.api.routes.workflow.ensure_page_ready",
            lambda *_args, **_kwargs: None,
        )

        project_response = client.post(
            "/api/v1/projects",
            json={
                "name": "两千字统一闭环验收",
                "workflow_mode": "AUTO",
                "default_concurrency": 4,
            },
        )
        assert project_response.status_code == 201
        project = project_response.json()

        paragraphs = [
            (
                f"第{index}段，春雨落在京都旧宅的黑瓦上，苏清白握着父亲留下的钥匙推开纸门。"
                "她看见顾川站在昏暗走廊尽头，低声问他为什么没有离开。"
                "顾川没有立刻回答，只把沾着雨水的旧信封放在灯下，两个人都意识到今晚必须说出真相。"
            )
            for index in range(1, 17)
        ]
        source_text = "\n\n".join(paragraphs)
        assert 1500 <= len(source_text) <= 3000
        imported_response = client.post(
            f"/api/v1/projects/{project['id']}/sources/import",
            json={"title": "雨夜旧信", "text": source_text},
        )
        assert imported_response.status_code == 201
        chapter = imported_response.json()["chapters"][0]
        assert 1500 <= chapter["source_character_count"] <= 3000
        segments = (
            db_session.query(SourceSegment)
            .filter(SourceSegment.source_revision_id == chapter["current_source_revision_id"])
            .order_by(SourceSegment.ordinal)
            .all()
        )
        assert len(segments) == len(paragraphs)

        fake_adapter = FakeAcceptanceAdapter(segments)
        monkeypatch.setattr("app.worker_tasks._adapter", lambda _alias: fake_adapter)
        parse_response = client.post(f"/api/v1/chapters/{chapter['id']}/parse")
        assert parse_response.status_code == 202
        _finish_job(db_session, parse_response.json()["id"], _run_story_parse)
        script = client.get(f"/api/v1/chapters/{chapter['id']}/script").json()
        assert script["status"] == "READY"
        assert script["coverage"]["ratio"] == 1
        assert len(script["scenes"]) >= 3
        assert all(scene["beats"] for scene in script["scenes"])
        assert all(
            beat["speaker_name"] in {"苏清白", "顾川"}
            for scene in script["scenes"]
            for beat in scene["beats"]
        )

        characters = client.get(f"/api/v1/projects/{project['id']}/characters").json()
        character = next(item for item in characters if item["primary_name"] == "苏清白")
        locked = client.patch(
            f"/api/v1/characters/{character['id']}",
            json={
                "version": character["version"],
                "primary_name": "苏清白",
                "aliases": ["小白"],
                "locked_features": ["黑色长发", "右眼泪痣"],
                "forbidden_changes": ["不得改变发色", "不得改变泪痣位置"],
            },
        )
        assert locked.status_code == 200

        def upload_reference(kind: str, name: str, data: bytes) -> dict:
            response = client.post(
                "/api/v1/assets/upload",
                data={"project_id": project["id"], "kind": kind},
                files={"file": (name, data, "image/png")},
            )
            assert response.status_code == 201
            return response.json()

        character_asset = upload_reference(
            "CHARACTER_REFERENCE", "character.png", _png_bytes((250, 250, 250))
        )
        other_character = next(item for item in characters if item["id"] != character["id"])
        other_character_asset = upload_reference(
            "CHARACTER_REFERENCE", "elder.png", _png_bytes((220, 220, 220))
        )
        outfit_asset = upload_reference(
            "OUTFIT_REFERENCE", "uniform.png", _png_bytes((180, 180, 180))
        )
        style_asset = upload_reference(
            "STYLE_REFERENCE", "style.png", _png_bytes((80, 80, 80))
        )
        bound = client.post(
            f"/api/v1/characters/{character['id']}/references",
            json={"asset_id": character_asset["id"], "angle": "front", "is_canonical": True},
        )
        assert bound.status_code == 201
        assert (
            client.post(
                f"/api/v1/characters/{other_character['id']}/references",
                json={
                    "asset_id": other_character_asset["id"],
                    "angle": "front",
                    "is_canonical": True,
                },
            ).status_code
            == 201
        )
        outfit = client.post(
            f"/api/v1/projects/{project['id']}/outfits",
            json={
                "character_id": character["id"],
                "name": "深色冬季校服",
                "components": {"jacket": "深色水手服", "shoes": "黑色皮鞋"},
                "state_rules": {"rain": "外套肩部微湿"},
                "locked_fields": ["领结", "裙长", "鞋型"],
                "reference_asset_ids": [outfit_asset["id"]],
            },
        ).json()
        style = client.post(
            f"/api/v1/projects/{project['id']}/styles",
            json={
                "name": "B1 雨夜彩色漫画",
                "color_mode": "color",
                "locked_fields": ["线稿", "低饱和色板", "右到左构图"],
                "reference_asset_ids": [style_asset["id"]],
            },
        ).json()
        style_job = client.post(f"/api/v1/styles/{style['id']}/analyze")
        assert style_job.status_code == 202
        _finish_job(db_session, style_job.json()["id"], _run_style_analyze)
        analyzed_style = db_session.get(StyleProfile, style["id"])
        assert analyzed_style.profile["prompt_summary"].startswith("彩色日式漫画")
        palette = client.post(
            f"/api/v1/styles/{style['id']}/palette-approve",
            json={
                "version": analyzed_style.version,
                "palette": analyzed_style.profile["palette_draft"],
            },
        )
        assert palette.status_code == 200, palette.json()

        sheet_response = client.post(
            f"/api/v1/characters/{character['id']}/complete-sheet",
            json={"model_alias": "image.nano_banana_2", "resolution": "1K"},
        )
        assert sheet_response.status_code == 202
        queued_sheet = sheet_response.json()
        assert queued_sheet["candidate"]["variant"] == "SHEET"
        _finish_job(db_session, queued_sheet["job_id"], _run_asset_generate)
        candidate = db_session.get(AssetCandidate, queued_sheet["candidate"]["id"])
        assert candidate.status == "READY" and candidate.asset_id

        outfit_batch = client.post(
            "/api/v1/asset-generation-batches",
            json={
                "target_type": "OUTFIT",
                "target_id": outfit["id"],
                "generation_kind": "OUTFIT",
            },
        ).json()
        outfit_preview = client.post(
            f"/api/v1/asset-generation-batches/{outfit_batch['id']}/candidates",
            json={
                "model_alias": "image.nano_banana_2",
                "resolution": "1K",
                "variant": "OUTFIT",
            },
        )
        assert outfit_preview.status_code == 202
        _finish_job(db_session, outfit_preview.json()["job_id"], _run_asset_generate)

        style_batch = client.post(
            "/api/v1/asset-generation-batches",
            json={
                "target_type": "STYLE",
                "target_id": style["id"],
                "generation_kind": "STYLE_TEST",
            },
        ).json()
        style_preview = client.post(
            f"/api/v1/asset-generation-batches/{style_batch['id']}/candidates",
            json={
                "model_alias": "image.nano_banana_2",
                "resolution": "1K",
                "variant": "STYLE_TEST",
            },
        )
        assert style_preview.status_code == 202
        _finish_job(db_session, style_preview.json()["job_id"], _run_asset_generate)
        analyzed_style = db_session.get(StyleProfile, style["id"])
        approved_test = client.post(
            f"/api/v1/styles/{style['id']}/style-test-approve",
            json={
                "candidate_id": style_preview.json()["candidate"]["id"],
                "approved": True,
                "version": analyzed_style.version,
            },
        )
        assert approved_test.status_code == 200, approved_test.json()
        assert (
            client.post(
                f"/api/v1/projects/{project['id']}/styles/{style['id']}/activate"
            ).status_code
            == 200
        )
        assert len(fake_adapter.asset_prompts) == 3

        for scene in script["scenes"]:
            assigned = client.patch(
                f"/api/v1/scenes/{scene['id']}/outfits",
                json={"assignments": {character["id"]: outfit["id"]}},
            )
            assert assigned.status_code == 200

        planned_response = client.post(
            f"/api/v1/chapters/{chapter['id']}/plan",
            json={"replace_existing": True},
        )
        assert planned_response.status_code == 200
        planned = planned_response.json()
        assert planned["coverage_ratio"] == 1
        assert planned["page_count"] >= 10
        assert all(page["source_coverage"]["complete"] for page in planned["pages"])
        assert all(page["scene_ids"] and page["beat_ids"] for page in planned["pages"])
        assert all(3 <= page["panel_count"] <= 5 for page in planned["pages"])
        assert all(page["estimated_bubbles"] <= 8 for page in planned["pages"])
        assert all(page["estimated_text_chars"] <= 180 for page in planned["pages"])
        assert all(page["reading_direction"] == "rtl" for page in planned["pages"])

        selected_candidate_ids: list[str] = []
        original_first_candidate_id = ""
        for index, page in enumerate(planned["pages"]):
            batch_response = client.post(f"/api/v1/pages/{page['id']}/batches")
            assert batch_response.status_code == 201
            model_alias = "image.nano_banana_2"
            queued_response = client.post(
                f"/api/v1/batches/{batch_response.json()['id']}/candidates",
                    json={
                        "model_alias": model_alias,
                        "resolution": "1K",
                        "storyboard_version": page["storyboard_version"],
                        "reference_selections": {
                            character["id"]: {
                                "character_asset_id": character_asset["id"],
                                "outfit_id": outfit["id"],
                                "outfit_asset_id": outfit_asset["id"],
                            },
                            other_character["id"]: {
                                "character_asset_id": other_character_asset["id"],
                                "outfit_id": None,
                                "outfit_asset_id": None,
                            },
                        },
                },
            )
            assert queued_response.status_code == 202, queued_response.json()
            queued = queued_response.json()
            _finish_job(db_session, queued["job_id"], _run_page_generate)
            candidate_id = queued["candidate"]["id"]
            candidate = db_session.get(PageCandidate, candidate_id)
            assert candidate.status == "READY" and candidate.asset_id
            selected_id = candidate_id

            if index == 0:
                original_first_candidate_id = candidate_id
                favorited = client.patch(
                    f"/api/v1/candidates/{candidate_id}/favorite",
                    json={"is_favorite": True},
                )
                assert favorited.status_code == 200 and favorited.json()["is_favorite"]
                inspection_job = client.post(
                    f"/api/v1/candidates/{candidate_id}/inspect",
                    json={
                        "categories": [
                            "SPEAKER",
                            "CHARACTER",
                            "OUTFIT",
                            "PROP",
                            "CONTINUITY",
                        ]
                    },
                )
                assert inspection_job.status_code == 202
                _finish_job(db_session, inspection_job.json()["id"], _run_inspection)
                inspections = client.get(
                    f"/api/v1/candidates/{candidate_id}/inspections"
                ).json()
                assert len(inspections) == 5

            final_inspection_job = client.post(
                f"/api/v1/candidates/{selected_id}/inspect",
                json={
                    "categories": [
                        "SPEAKER",
                        "CHARACTER",
                        "OUTFIT",
                        "PROP",
                        "CONTINUITY",
                    ]
                },
            )
            assert final_inspection_job.status_code == 202
            _finish_job(
                db_session,
                final_inspection_job.json()["id"],
                _run_inspection,
            )

            selected = client.post(
                f"/api/v1/pages/{page['id']}/select-candidate",
                json={"candidate_id": selected_id, "manual_text_confirmed": True},
            )
            assert selected.status_code == 200
            assert selected.json()["selected_candidate_id"] == selected_id
            selected_candidate_ids.append(selected_id)
            if index < len(planned["pages"]) - 1:
                following = client.post(f"/api/v1/pages/{page['id']}/next")
                assert following.status_code == 200
                assert following.json()["page_number"] == page["page_number"] + 1

        assert len(fake_adapter.page_prompts) == planned["page_count"]
        assert all("从右到左" in prompt for prompt in fake_adapter.page_prompts)
        assert any("黑色长发" in prompt for prompt in fake_adapter.page_prompts)
        assert any("深色冬季校服" in prompt for prompt in fake_adapter.page_prompts)
        assert any("B1 雨夜彩色漫画" in prompt for prompt in fake_adapter.page_prompts)

        library = client.get(
            f"/api/v1/projects/{project['id']}/library",
            params={"group_by": "batch"},
        ).json()
        assert library["favorite_count"] == 1
        assert library["total_candidates"] >= planned["page_count"] + 3
        favorites = client.get(
            f"/api/v1/projects/{project['id']}/library",
            params={"group_by": "batch", "favorite": True},
        ).json()
        assert favorites["total_candidates"] == 1
        assert favorites["groups"][0]["candidates"][0]["id"] == original_first_candidate_id

        first_png = client.get(f"/api/v1/pages/{planned['pages'][0]['id']}/export.png")
        assert first_png.status_code == 200
        assert first_png.headers["content-type"].startswith("image/png")
        for export_type in ["PNG", "PDF", "JSON"]:
            exported = client.post(
                f"/api/v1/chapters/{chapter['id']}/exports",
                json={"export_type": export_type},
            )
            assert exported.status_code == 201
            downloaded = client.get(exported.json()["download_url"])
            assert downloaded.status_code == 200
            if export_type == "PNG":
                with ZipFile(BytesIO(downloaded.content)) as archive:
                    assert len(archive.namelist()) == planned["page_count"]
            elif export_type == "PDF":
                assert downloaded.content.startswith(b"%PDF")
            else:
                document = downloaded.json()
                assert len(document["pages"]) == planned["page_count"]
                assert document["asset_manifest"]
                assert [
                    page["selected_candidate"]["id"] for page in document["pages"]
                ] == selected_candidate_ids

        assert db_session.query(Character).filter_by(project_id=project["id"]).count() == 2
