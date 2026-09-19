"""Fixed acceptance dataset (NUI67-DS1) for NUI-6 side-by-side verification.

Seeds ONE deterministic dataset into a target database + storage root so the
web dev stack and the WPF client's isolated user-data directory see identical
data. Rows are created directly through SQLAlchemy models (the same approach
as scripts/e2e_fixtures.py) because the seeder must run before a server owns
the database. Read-back verification against the live API happens separately
in the orchestrator's page flows, not here.

Every seeded name carries the NUI67 prefix so evidence screenshots can never
be confused with user data.
"""

from __future__ import annotations

import hashlib
import sys
import zipfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

# Standalone usage (python scripts/nui67_seed.py ...) needs the API package;
# the orchestrator also inserts these before importing this module.
_REPO = Path(__file__).resolve().parents[1]
for _path in (str(_REPO / "apps" / "api"), str(_REPO / "scripts")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.domain.states import Resolution
from app.models import (
    AIModel,
    AppSetting,
    Asset,
    Beat,
    Chapter,
    Character,
    CharacterReference,
    Dialogue,
    ExportBundle,
    GenerationBatch,
    GenerationJob,
    GenerationRecord,
    InspectionResult,
    MangaPage,
    ModelCallAttempt,
    ModelPricingVersion,
    Outfit,
    PageCandidate,
    Panel,
    Project,
    ProviderConnection,
    ProviderProfile,
    ProviderUsageReconciliation,
    Scene,
    SceneAsset,
    SceneAssetReference,
    SceneAssetVariant,
    ScriptRevision,
    SourceRevision,
    SourceSegment,
    StyleProfile,
    WorkflowDefinition,
)

DATASET_TAG = "NUI67"

PNG_SIZES = {
    "ref": (512, 640),
    "page": (768, 1024),
    "cover": (400, 300),
}


def _png(path: Path, size: tuple[int, int], rgb: tuple[int, int, int]) -> bytes:
    """Deterministic solid-color PNG (Pillow is already a runtime dependency)."""
    from PIL import Image

    Image.new("RGB", size, rgb).save(path, "PNG")
    return path.read_bytes()


def _asset(
    session: Session,
    project: Project,
    storage_root: Path,
    *,
    name: str,
    kind: str,
    size: tuple[int, int],
    rgb: tuple[int, int, int],
) -> Asset:
    from app.services.media import create_thumbnails

    file_key = f"nui67-{name}.png"
    target = storage_root / file_key
    payload = _png(target, size, rgb)
    digest = hashlib.sha256(payload).hexdigest()
    asset = Asset(
        project_id=project.id,
        kind=kind,
        original_name=file_key,
        display_name=name,
        storage_key=file_key,
        mime_type="image/png",
        byte_size=len(payload),
        sha256=digest,
        width=size[0],
        height=size[1],
        # Files land in the caller-provided root; USER_UPLOAD assets are served
        # from upload_root, GENERATED ones from storage_root (uploads.py:689).
        source="GENERATED",
        status="UPLOADED",
    )
    session.add(asset)
    session.flush()
    thumbs = create_thumbnails(target, storage_root, asset.id)
    asset.thumbnail_320_key = thumbs[320]
    asset.thumbnail_640_key = thumbs[640]
    session.flush()
    return asset


def seed_fixed_dataset(db_url: str, storage_root: Path, upload_root: Path | None = None) -> dict[str, str]:
    storage_root.mkdir(parents=True, exist_ok=True)
    upload_root = upload_root or storage_root
    upload_root.mkdir(parents=True, exist_ok=True)
    engine = create_engine(db_url)
    ids: dict[str, str] = {}
    now = datetime.now(UTC)
    with Session(engine) as session:
        main = Project(name=f"{DATASET_TAG} 并排对照主项目")
        other = Project(name=f"{DATASET_TAG} 切换测试项目")
        session.add_all([main, other])
        session.flush()

        # --- 原作/剧本/分镜:三章 + 段落 + 场景/节拍 + 页面/面板/对白 ---
        chapter_texts = [
            ("第一章 天台的风", "放学后的天台没有别人。林晚靠着栏杆,手里捏着一张未完成的线稿。\n"
             "陈默推门上来,脚步声让她转过身。两人隔着半个天台对视。"),
            ("第二章 未完成的线稿", "线稿从指间滑落,在天台的水泥地上翻了个面。\n"
             "陈默捡起来,看见画上是自己——却是他从未见过的姿势。"),
            ("第三章 电梯", "电梯在四楼与五楼之间停了三十秒。应急灯亮起。林晚说:其实我一直想画完它。"),
        ]
        chapters = []
        for ordinal, (title, text) in enumerate(chapter_texts, start=1):
            chapter = Chapter(project_id=main.id, title=title, ordinal=ordinal, status="IMPORTED")
            session.add(chapter)
            session.flush()
            revision = SourceRevision(
                chapter_id=chapter.id,
                revision=1,
                source_type="PASTE",
                original_text=text,
                sha256=f"{ordinal:064x}",
                character_count=len(text),
            )
            session.add(revision)
            session.flush()
            chapter.current_source_revision_id = revision.id
            offset = 0
            for seg_no, paragraph in enumerate(text.split("\n"), start=1):
                session.add(
                    SourceSegment(
                        source_revision_id=revision.id,
                        ordinal=seg_no,
                        text=paragraph,
                        start_offset=offset,
                        end_offset=offset + len(paragraph),
                        sha256=hashlib.sha256(paragraph.encode("utf-8")).hexdigest(),
                    )
                )
                offset += len(paragraph) + 1
            chapters.append((chapter, revision))
        chapter1, revision1 = chapters[0]

        # Script: scene + beats + READY revision (chapter 1)
        scene = Scene(
            chapter_id=chapter1.id,
            ordinal=1,
            location="学校天台",
            time_label="黄昏",
            weather="微风",
            purpose="重逢与伏笔",
            emotional_arc="平静→动摇",
        )
        session.add(scene)
        session.flush()
        session.add_all(
            [
                Beat(
                    scene_id=scene.id,
                    ordinal=1,
                    action="林晚靠在栏杆上,捏着线稿",
                    narration="天台的风把她的头发吹向一侧",
                    importance=0.7,
                ),
                Beat(
                    scene_id=scene.id,
                    ordinal=2,
                    action="陈默推门而入",
                    speaker_name="陈默",
                    dialogue="你还在画?",
                    emotion="犹豫",
                    importance=0.9,
                ),
                Beat(
                    scene_id=scene.id,
                    ordinal=3,
                    action="线稿滑落",
                    speaker_name="林晚",
                    dialogue="别看!",
                    emotion="慌张",
                    must_visualize=True,
                    importance=1.0,
                ),
            ]
        )
        session.add(
            ScriptRevision(
                chapter_id=chapter1.id,
                source_revision_id=revision1.id,
                revision_no=1,
                status="READY",
                coverage={"expected": 3, "covered": 3, "ratio": 1, "missing_segment_ids": []},
            )
        )

        # --- 资产参考图(PNG 落入 storage 根,含缩略图) ---
        ref_linwan = _asset(session, main, storage_root, name="林晚-参考", kind="CHARACTER_REF",
                            size=PNG_SIZES["ref"], rgb=(240, 228, 214))
        ref_chenmo = _asset(session, main, storage_root, name="陈默-参考", kind="CHARACTER_REF",
                            size=PNG_SIZES["ref"], rgb=(214, 222, 240))
        ref_scene = _asset(session, main, storage_root, name="天台-场景", kind="SCENE_REF",
                           size=PNG_SIZES["cover"], rgb=(228, 240, 214))
        ref_page1 = _asset(session, main, storage_root, name="第1页-候选", kind="PAGE_CANDIDATE",
                           size=PNG_SIZES["page"], rgb=(250, 240, 200))
        ref_page1b = _asset(session, main, storage_root, name="第1页-候选B", kind="PAGE_CANDIDATE",
                            size=PNG_SIZES["page"], rgb=(200, 250, 240))
        ref_page2 = _asset(session, main, storage_root, name="第2页-候选", kind="PAGE_CANDIDATE",
                           size=PNG_SIZES["page"], rgb=(230, 200, 250))

        # --- 人物 / 服装 ---
        linwan = Character(
            project_id=main.id,
            primary_name="林晚",
            aliases=["小晚"],
            aliases_normalized=["小晚"],
            canonical_description="高三美术生,习惯把情绪藏进线稿;左腕缠着一圈黑色发绳。",
            locked_features=["左腕黑色发绳", "微卷齐肩发"],
            forbidden_changes=["瞳色不得改为暖色"],
            status="CANONICAL",
        )
        chenmo = Character(
            project_id=main.id,
            primary_name="陈默",
            aliases=[],
            aliases_normalized=[],
            canonical_description="同班转学生,寡言,随身带速写本。",
            locked_features=["额前碎发"],
            forbidden_changes=[],
            status="CANONICAL",
        )
        session.add_all([linwan, chenmo])
        session.flush()
        session.add_all(
            [
                CharacterReference(character_id=linwan.id, asset_id=ref_linwan.id, angle="front", is_canonical=True),
                CharacterReference(character_id=chenmo.id, asset_id=ref_chenmo.id, angle="front", is_canonical=True),
            ]
        )

        outfit_uniform = Outfit(
            project_id=main.id,
            character_id=linwan.id,
            name="林晚·春季校服",
            components={"top": "白衬衫", "outer": "针织开衫", "bottom": "深色百褶裙"},
            state_rules={"雨": "外加透明伞"},
            locked_fields=["top"],
            reference_asset_ids=[ref_linwan.id],
            status="CANONICAL",
        )
        outfit_work = Outfit(
            project_id=main.id,
            character_id=chenmo.id,
            name="陈默·日常外套",
            components={"top": "灰色T恤", "outer": "深蓝外套"},
            state_rules={},
            locked_fields=[],
            reference_asset_ids=[ref_chenmo.id],
            status="UPLOADED",
        )
        session.add_all([outfit_uniform, outfit_work])
        session.flush()

        # --- 场景资产 / 风格 ---
        scene_asset = SceneAsset(
            project_id=main.id,
            name="学校天台",
            normalized_name="学校天台",
            description="围栏式天台,黄昏时西侧有长影。",
            location_hint="教学楼顶层",
            structured={"floor": "顶楼", "props": ["围栏", "水箱"]},
            status="CANONICAL",
        )
        session.add(scene_asset)
        session.flush()
        session.add(SceneAssetReference(scene_asset_id=scene_asset.id, asset_id=ref_scene.id, role="main", is_canonical=True))
        variant = SceneAssetVariant(scene_asset_id=scene_asset.id, name="黄昏", is_canonical=True)
        session.add(variant)
        session.flush()
        scene.scene_asset_id = scene_asset.id
        scene.scene_asset_variant_id = variant.id

        style = StyleProfile(
            project_id=main.id,
            name="灰调水墨",
            color_mode="monochrome",
            profile={"line": "湿笔", "tone": "灰阶三层", "screentone": "少量网点"},
            locked_fields=["tone"],
            status="ACTIVE",
        )
        session.add(style)
        session.flush()
        main.default_style_id = style.id

        # --- 分镜页:第一章 3 页,面板 + 对白 ---
        page_specs = [
            (1, 4, 2, "PLANNED"),
            (2, 3, 1, "PLANNED"),
            (3, 2, 1, "PLANNED"),
        ]
        pages: list[MangaPage] = []
        for number, panel_count, sb_version, status in page_specs:
            page = MangaPage(
                chapter_id=chapter1.id,
                page_number=number,
                revision_no=1,
                page_function="dialogue",
                panel_count=panel_count,
                resolution=Resolution.DRAFT_1K,
                status=status,
                scene_ids=[scene.id],
                source_coverage={"complete": True},
                storyboard_version=sb_version,
            )
            session.add(page)
            session.flush()
            for order in range(1, panel_count + 1):
                panel = Panel(
                    page_id=page.id,
                    reading_order=order,
                    bounds={"x": 0.05 + 0.1 * order, "y": 0.05 * order, "w": 0.4, "h": 0.3},
                    shot_type="medium_close_up",
                    background="天台" if order % 2 else "走廊",
                    characters=[linwan.primary_name] if order == 1 else [chenmo.primary_name],
                    character_presence={
                        linwan.primary_name: "VISIBLE" if order == 1 else "OFFSCREEN",
                        chenmo.primary_name: "OFFSCREEN" if order == 1 else "VISIBLE",
                    },
                    outfits={"林晚": "林晚·春季校服"} if order == 1 else {},
                    actions={"主": "执笔凝视"},
                    expressions={"主": "认真"},
                )
                session.add(panel)
                session.flush()
                if order == 1:
                    session.add(
                        Dialogue(
                            panel_id=panel.id,
                            speaker_character_id=chenmo.id if number != 1 else linwan.id,
                            target_text="你还在画?" if number == 1 else "该走了。",
                            reading_order=1,
                            region={"x": 0.6, "y": 0.1, "w": 0.3, "h": 0.2},
                        )
                    )
            pages.append(page)

        # --- 生成批次/候选:page1 两个候选(已选+质检全过)、page2 一个候选 ---
        batch1 = GenerationBatch(
            project_id=main.id, chapter_id=chapter1.id, page_id=pages[0].id,
            ordinal=1, generation_kind="PAGE", status="CLOSED",
        )
        batch2 = GenerationBatch(
            project_id=main.id, chapter_id=chapter1.id, page_id=pages[1].id,
            ordinal=2, generation_kind="PAGE", status="OPEN",
        )
        session.add_all([batch1, batch2])
        session.flush()
        candidate1 = PageCandidate(
            batch_id=batch1.id, page_id=pages[0].id, ordinal=1,
            model_alias="image.nano_banana_2", resolution=Resolution.DRAFT_1K,
            status="INSPECTED", asset_id=ref_page1.id, based_on_storyboard_version=2,
            is_selected=True,
        )
        candidate1b = PageCandidate(
            batch_id=batch1.id, page_id=pages[0].id, ordinal=2,
            model_alias="image.nano_banana_2", resolution=Resolution.DRAFT_1K,
            status="COMPLETED", asset_id=ref_page1b.id, based_on_storyboard_version=2,
            is_selected=False,
        )
        candidate2 = PageCandidate(
            batch_id=batch2.id, page_id=pages[1].id, ordinal=1,
            model_alias="image.nano_banana_pro", resolution=Resolution.STANDARD_2K,
            status="NEEDS_REVIEW", asset_id=ref_page2.id, based_on_storyboard_version=1,
            is_selected=False,
        )
        session.add_all([candidate1, candidate1b, candidate2])
        session.flush()
        pages[0].selected_candidate_id = candidate1.id
        pages[0].selected_candidate_ack_version = 2
        categories = ("SPEAKER", "CHARACTER", "OUTFIT", "PROP", "CONTINUITY")
        for category in categories:
            session.add(
                InspectionResult(
                    candidate_id=candidate1.id, storyboard_version=2, category=category,
                    outcome="PASS", score=1.0,
                )
            )
        session.add(
            InspectionResult(
                candidate_id=candidate2.id, storyboard_version=1, category="CONTINUITY",
                outcome="FAIL", score=0.2,
            )
        )

        # --- 任务页:完成/失败任务 + 生成记录 + 调用尝试 ---
        job_done = GenerationJob(
            project_id=main.id, target_type="PAGE", target_id=pages[0].id,
            job_type="PAGE_GENERATION", status="COMPLETED",
            attempt_count=1, model_alias="image.nano_banana_2",
            idempotency_key=f"{DATASET_TAG.lower()}-job-done",
            scheduled_at=now - timedelta(days=1), started_at=now - timedelta(days=1) + timedelta(seconds=1),
            finished_at=now - timedelta(days=1) + timedelta(seconds=6),
        )
        job_failed = GenerationJob(
            project_id=main.id, target_type="PAGE", target_id=pages[1].id,
            job_type="PAGE_GENERATION", status="FAILED",
            attempt_count=2, model_alias="image.nano_banana_pro",
            idempotency_key=f"{DATASET_TAG.lower()}-job-failed",
            error_code="PROVIDER_RATE_LIMIT", error_message="429 rate limited (redacted)",
            started_at=now - timedelta(hours=5), finished_at=now - timedelta(hours=5) + timedelta(seconds=3),
        )
        session.add_all([job_done, job_failed])
        session.flush()
        session.add(
            GenerationRecord(
                job_id=job_done.id, provider=f"{DATASET_TAG.lower()}-provider",
                model_id=f"{DATASET_TAG.lower()}-image", location="us",
                parameters={"resolution": "1K"}, prompt_template="page", prompt_version="v1",
                prompt_checksum="0" * 64, input_versions={}, reference_asset_ids=[ref_linwan.id],
                started_at=now - timedelta(days=1), finished_at=now - timedelta(days=1) + timedelta(seconds=5),
                usage={"output_images": 1}, output_asset_ids=[ref_page1.id], status="COMPLETED",
            )
        )
        session.add_all(
            [
                ModelCallAttempt(
                    project_id=main.id, job_id=job_done.id, job_attempt=1, dispatch_no=1,
                    outcome="SUCCEEDED", channel="HTTP_API", provider=f"{DATASET_TAG.lower()}-provider",
                    model_id=f"{DATASET_TAG.lower()}-image", request_id=f"{DATASET_TAG.lower()}-req-1",
                    started_at=now - timedelta(days=1), duration_ms=4120,
                    usage={"output_images": 1}, usage_status="COMPLETE",
                    usage_source="PROVIDER_REPORTED", unit_kind="IMAGES", output_images=1,
                ),
                ModelCallAttempt(
                    project_id=main.id, job_id=job_failed.id, job_attempt=1, dispatch_no=1,
                    outcome="FAILED", channel="HTTP_API", provider=f"{DATASET_TAG.lower()}-provider",
                    model_id=f"{DATASET_TAG.lower()}-image", request_id=f"{DATASET_TAG.lower()}-req-0",
                    started_at=now - timedelta(hours=5), duration_ms=1800,
                    error_code="PROVIDER_RATE_LIMIT", error_message="429 rate limited (redacted)",
                ),
                ModelCallAttempt(
                    project_id=main.id, job_id=job_failed.id, job_attempt=2, dispatch_no=1,
                    outcome="FAILED", channel="CLI", provider=f"{DATASET_TAG.lower()}-cli",
                    model_id=f"{DATASET_TAG.lower()}-cli-image", request_id=f"{DATASET_TAG.lower()}-req-2",
                    started_at=now - timedelta(hours=4), duration_ms=56000,
                    error_code="CLI_TIMEOUT", error_message="runner exceeded 120s",
                ),
            ]
        )

        # --- 用量定价与对账 ---
        session.add_all(
            [
                ModelPricingVersion(
                    provider=f"{DATASET_TAG.lower()}-provider", model_id=f"{DATASET_TAG.lower()}-image",
                    pricing_version=f"{DATASET_TAG.lower()}-v1", currency="CNY",
                    effective_from=now - timedelta(days=60),
                    request_each=Decimal("0.01"), output_image_each=Decimal("0.12"),
                ),
                ModelPricingVersion(
                    provider=f"{DATASET_TAG.lower()}-cli", model_id=f"{DATASET_TAG.lower()}-cli-image",
                    pricing_version=f"{DATASET_TAG.lower()}-v1", currency="USD",
                    effective_from=now - timedelta(days=60), request_each=Decimal("0.02"),
                ),
                ProviderUsageReconciliation(
                    provider=f"{DATASET_TAG.lower()}-provider", model_id=f"{DATASET_TAG.lower()}-image",
                    channel="HTTP_API", billing_account_id=f"{DATASET_TAG.lower()}-billing",
                    import_batch_id=f"{DATASET_TAG.lower()}-batch-1", idempotency_key=f"{DATASET_TAG.lower()}-line-1",
                    period_start=now - timedelta(days=29), period_end=now - timedelta(days=1),
                    currency="CNY", billed_amount=Decimal("66.00"),
                    source_note="NUI67 离线对账样例,非真实账单", entered_by="nui67-operator",
                ),
            ]
        )

        # --- 工作流:草稿图(定义自带 draft_graph,draft 状态无需 version 行) ---
        session.add(
            WorkflowDefinition(
                project_id=main.id, name=f"{DATASET_TAG} 默认流程", description="验收用两节点流程",
                draft_graph={
                    "nodes": [
                        {"id": "n1", "type": "SOURCE_INPUT", "label": "原作输入", "position": {"x": 80, "y": 80}, "params": {}},
                        {"id": "n2", "type": "SCRIPT", "label": "剧本解析", "position": {"x": 320, "y": 80}, "params": {}},
                    ],
                    "edges": [{"id": "e1", "source": "n1", "target": "n2"}],
                },
                draft_version=1,
            )
        )

        # --- 导出门禁样例 ---
        # NUI-8 P1-3：这里过去只插一行元数据，storage_key 指向的文件从未写过，
        # byte_size/sha256 也是编的，所以素材库「下载导出包」必然 404、不落盘，
        # 导出下载路径整条无法验收。现在真打一个 zip，并按实际字节算大小与摘要。
        bundle_key = f"exports/{DATASET_TAG.lower()}-chapter1.zip"
        bundle_path = storage_root / bundle_key
        bundle_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as bundle:
            for page_number, page_asset in enumerate((ref_page1, ref_page2, ref_page1b), start=1):
                bundle.write(storage_root / page_asset.storage_key,
                             f"chapter1/page-{page_number:03d}.png")
        bundle_bytes = bundle_path.read_bytes()
        session.add(
            ExportBundle(
                project_id=main.id, chapter_id=chapter1.id, export_type="PNG_ZIP",
                storage_key=bundle_key,
                byte_size=len(bundle_bytes),
                sha256=hashlib.sha256(bundle_bytes).hexdigest(),
                page_count=3,
            )
        )

        # --- 设置页:供应商档案 + 连接 + 模型目录(无真实凭据,连接禁用) ---
        profile = ProviderProfile(
            name=f"{DATASET_TAG.lower()}-provider", description="NUI67 验收用本地假供应商",
            category="CUSTOM", built_in=False, enabled=True, risk_label="UNOFFICIAL",
        )
        session.add(profile)
        session.flush()
        connection = ProviderConnection(
            provider_id=profile.id, name="默认连接", protocol="OPENAI",
            base_url="http://127.0.0.1:9/v1", enabled=False,
            health_state="UNCONFIGURED",
        )
        session.add(connection)
        session.flush()
        session.add_all(
            [
                AIModel(
                    connection_id=connection.id, provider_model_id=f"{DATASET_TAG.lower()}-image",
                    display_name="NUI67 图像模型", model_type="IMAGE",
                    input_modalities=["TEXT"], output_modalities=["IMAGE"],
                    legacy_alias="image.nano_banana_2",
                ),
                AIModel(
                    connection_id=connection.id, provider_model_id=f"{DATASET_TAG.lower()}-text",
                    display_name="NUI67 文本模型", model_type="TEXT",
                    legacy_alias="text.gemini_flash",
                ),
            ]
        )

        # --- 全局设置标记行(可核对种子版本) ---
        session.add(AppSetting(key=f"{DATASET_TAG.lower()}-marker", value={"seeded": True}))

        # --- 第二项目:单章单页(项目切换/首页卡片/空态) ---
        other_chapter = Chapter(project_id=other.id, title="序章", ordinal=1)
        session.add(other_chapter)
        session.flush()
        session.add(MangaPage(chapter_id=other_chapter.id, page_number=1, panel_count=2))
        _asset(session, other, storage_root, name="序章-候选", kind="PAGE_CANDIDATE",
               size=PNG_SIZES["page"], rgb=(240, 240, 240))

        session.commit()
        ids = {
            "main_project": main.id,
            "other_project": other.id,
            "character_linwan": linwan.id,
            "character_chenmo": chenmo.id,
            "page1": pages[0].id,
        }
    engine.dispose()
    return ids


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: nui67_seed.py <database_url> <storage_root>")
        return 2
    ids = seed_fixed_dataset(sys.argv[1], Path(sys.argv[2]))
    print(ids)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
