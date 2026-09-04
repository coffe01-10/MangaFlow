"""Fake model channel for the desktop sidecar e2e (zero provider calls).

Grown from the V02-53B PoC fixture; this module is desktop-e2e-owned
acceptance tooling, not product code. Following the
same seam the repository acceptance suite uses (``app.worker_tasks._adapter``
resolved per call through ``install_legacy_adapter_lookup``), it:

1. seeds a minimal provider profile / connection / catalog rows so the
   ``_binding`` legacy path finds an IMAGE and a TEXT catalog model, and
2. swaps ``app.worker_tasks._adapter`` for a deterministic fake that serves
   story parse, style analysis, page inspection, and image generation.

No network call ever leaves the process; images are generated with Pillow.
"""

from __future__ import annotations

from io import BytesIO
from typing import Any

from PIL import Image

from app.database import SessionLocal
from app.model_adapters.base import ModelResponse
from app.models import AIModel, ProviderConnection, ProviderKey, ProviderProfile, SourceSegment
from app.services.ai_schemas import (
    BeatDraft,
    CharacterDraft,
    InspectionItem,
    PageInspectionOutput,
    SceneDraft,
    StoryParseOutput,
    StyleAnalysisOutput,
)

_MODEL_OPERATIONS = {
    "IMAGE": ["image_generate", "image_edit", "image_asset", "image_page"],
    "TEXT": ["text_story_parse", "text_style_analyze", "text_inspection"],
}

# The alias complete-sheet resolves explicitly at the route layer.
IMAGE_LEGACY_ALIAS = "image.nano_banana_2"


def _png(color: tuple[int, int, int] = (245, 245, 240)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (64, 64), color).save(buffer, format="PNG")
    return buffer.getvalue()


class FakeChannelAdapter:
    """Deterministic adapter mirroring tests/test_full_acceptance.py."""

    def __init__(self) -> None:
        self.call_count = 0

    def _image_response(self) -> ModelResponse:
        self.call_count += 1
        return ModelResponse(
            model_id="desktop-fake-image",
            request_id=f"desktop-fake-request-{self.call_count}",
            usage={"input_tokens": 10, "output_images": 1, "fake": True},
            images=(_png(),),
        )

    def generate_structured(self, request: Any, output_schema: Any) -> Any:
        if output_schema is not StoryParseOutput:
            raise ProviderAdapterUsageError(f"unexpected structured schema {output_schema}")
        with SessionLocal() as db:
            segments = [
                segment for segment in db.query(SourceSegment).all() if segment.id in request.prompt
            ]
        if not segments:
            raise ProviderAdapterUsageError("no source segment in prompt")
        scenes = []
        for scene_index, offset in enumerate(range(0, len(segments), 4), 1):
            group = segments[offset : offset + 4]
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
                    source_segment_ids=[segment.id for segment in segments],
                ),
                CharacterDraft(
                    primary_name="顾川",
                    aliases=["小川"],
                    description="短发、神情克制的高中男生",
                    source_segment_ids=[segment.id for segment in segments],
                ),
            ],
            scenes=scenes,
        )

    def analyze_multimodal(self, request: Any, output_schema: Any) -> Any:
        if output_schema is PageInspectionOutput:
            return PageInspectionOutput(
                items=[
                    InspectionItem(
                        category=category,
                        outcome="PASS",
                        score=0.98,
                        severity="INFO",
                        details={"expected": "结构化目标", "observed": "符合目标"},
                        regions=[],
                    )
                    for category in ("SPEAKER", "CHARACTER", "OUTFIT", "PROP", "CONTINUITY")
                ]
            )
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

    def generate_page(self, request: Any) -> ModelResponse:
        return self._image_response()

    def generate_asset(self, request: Any) -> ModelResponse:
        return self._image_response()

    def edit_region(self, request: Any) -> ModelResponse:
        return self._image_response()

    def capabilities(self) -> dict[str, Any]:
        return {"fake_channel": True}


class ProviderAdapterUsageError(RuntimeError):
    pass


def _seed_catalog() -> None:
    from app.config import get_settings
    from app.services.credential_crypto import encrypt_secret

    with SessionLocal() as db:
        existing = db.query(ProviderProfile).filter_by(name="Desktop Fake Channel").one_or_none()
        if existing is not None:
            return
        profile = ProviderProfile(
            name="Desktop Fake Channel",
            category="LOCAL",
            description="Desktop e2e fake channel; never called over network",
            enabled=True,
        )
        db.add(profile)
        db.flush()
        connection = ProviderConnection(
            provider_id=profile.id,
            name="桌面假通道",
            protocol="COMPATIBLE",
            base_url="http://127.0.0.1:9/unused",
            enabled=True,
            message="Fake channel; base URL is intentionally unreachable",
        )
        db.add(connection)
        db.flush()
        # The connection must satisfy the production credential boundary:
        # an AES-GCM encrypted key under the file master key, never plaintext.
        db.add(
            ProviderKey(
                connection_id=connection.id,
                encrypted_secret=encrypt_secret(get_settings(), "desktop-fake-channel-key"),
                key_hint="••••",
            )
        )
        for model_type, display, legacy_alias in (
            ("IMAGE", "桌面假图片模型", IMAGE_LEGACY_ALIAS),
            ("TEXT", "桌面假文本模型", "desktop.fake.text"),
        ):
            db.add(
                AIModel(
                    connection_id=connection.id,
                    provider_model_id=f"desktop-fake-{model_type.lower()}",
                    display_name=display,
                    legacy_alias=legacy_alias,
                    model_type=model_type,
                    input_modalities=["IMAGE"] if model_type == "IMAGE" else ["TEXT"],
                    output_modalities=["IMAGE"] if model_type == "IMAGE" else ["TEXT"],
                    operations=_MODEL_OPERATIONS[model_type],
                )
            )
        db.commit()


def install() -> None:
    """Wire the fake channel into the worker seam; call after migrations."""
    import app.worker_tasks as worker_tasks

    _seed_catalog()
    worker_tasks._adapter = lambda _alias: FakeChannelAdapter()
