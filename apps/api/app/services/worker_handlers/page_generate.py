"""PAGE_GENERATE / PAGE_REPAIR / PAGE_UPSCALE handler.

Owns storyboard guard, reference loading, prompt snapshot, the paid image
call and candidate/asset persistence for page-level jobs.  Cancellation and
lease checks stay owned by the execution shell via ``execution`` helpers.
"""

import hashlib
import json

from PIL import Image
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.domain.states import JobStatus, PageStatus
from app.model_adapters.base import ImageRequest, ProviderAdapterError
from app.models import (
    Asset,
    Chapter,
    Character,
    CharacterReference,
    GenerationJob,
    GenerationRecord,
    InspectionResult,
    MangaPage,
    Outfit,
    PageCandidate,
    Panel,
    Project,
    RepairPlan,
    Scene,
    StyleProfile,
    utcnow,
)
from app.services.media import create_thumbnails, remove_thumbnails
from app.services.model_router import model_supports_resolution
from app.services.prompt_compiler import PAGE_TEMPLATE_VERSION, compile_page_prompt
from app.services.worker_handlers import execution, provider
from app.services.worker_handlers.execution import StaleStoryboardVersionError


def _load_reference_assets(
    db,
    page: MangaPage,
    project: Project,
    reference_selections: dict[str, dict[str, str | None]] | None = None,
) -> list[Asset]:
    page_character_ids = {
        character_id
        for panel in db.scalars(select(Panel).where(Panel.page_id == page.id))
        for character_id in panel.characters
    }
    if reference_selections is not None:
        unexpected_characters = set(reference_selections) - page_character_ids
        if unexpected_characters:
            raise RuntimeError("参考图选择包含不在当前页面中的人物")
        selected_ids = {
            asset_id
            for character_id in page_character_ids
            for asset_id in (
                reference_selections.get(character_id, {}).get("character_asset_id"),
                reference_selections.get(character_id, {}).get("outfit_asset_id"),
            )
            if asset_id
        }
        references = (
            list(
                db.scalars(
                    select(Asset).where(
                        Asset.id.in_(selected_ids),
                        Asset.project_id == project.id,
                        Asset.deleted_at.is_(None),
                    )
                )
            )
            if selected_ids
            else []
        )
        loaded_ids = {asset.id for asset in references}
        missing_ids = selected_ids - loaded_ids
        if missing_ids:
            raise RuntimeError(
                "已确认的参考图已删除或失效，已在调用模型前停止任务："
                + "、".join(sorted(missing_ids))
            )
        for character_id in page_character_ids:
            selection = reference_selections.get(character_id) or {}
            character_asset_id = selection.get("character_asset_id")
            character_reference = (
                db.scalar(
                    select(CharacterReference).where(
                        CharacterReference.character_id == character_id,
                        CharacterReference.asset_id == character_asset_id,
                    )
                )
                if character_asset_id
                else None
            )
            if not character_reference:
                raise RuntimeError("人物参考图绑定已变化，已在调用模型前停止任务")
            outfit_id = selection.get("outfit_id")
            outfit_asset_id = selection.get("outfit_asset_id")
            if outfit_id:
                outfit = db.get(Outfit, outfit_id)
                if (
                    not outfit
                    or outfit.character_id != character_id
                    or outfit.project_id != project.id
                    or outfit_asset_id not in outfit.reference_asset_ids
                ):
                    raise RuntimeError("服装参考图绑定已变化，已在调用模型前停止任务")
    else:
        references = (
            list(
                db.scalars(
                    select(Asset)
                    .join(CharacterReference, CharacterReference.asset_id == Asset.id)
                    .join(Character, Character.id == CharacterReference.character_id)
                    .where(
                        Character.project_id == project.id,
                        Character.id.in_(page_character_ids),
                        Asset.deleted_at.is_(None),
                    )
                    .order_by(
                        CharacterReference.is_canonical.desc(),
                        CharacterReference.created_at,
                    )
                    .limit(10)
                )
            )
            if page_character_ids
            else []
        )
        scenes = (
            list(db.scalars(select(Scene).where(Scene.id.in_(page.scene_ids))))
            if page.scene_ids
            else []
        )
        outfit_ids = {
            outfit_id
            for scene in scenes
            for outfit_id in scene.outfit_assignments.values()
            if outfit_id
        }
        if outfit_ids:
            outfits = list(db.scalars(select(Outfit).where(Outfit.id.in_(outfit_ids))))
            outfit_reference_ids = {
                asset_id for outfit in outfits for asset_id in outfit.reference_asset_ids
            }
            if outfit_reference_ids:
                references.extend(
                    db.scalars(
                        select(Asset).where(
                            Asset.id.in_(outfit_reference_ids), Asset.deleted_at.is_(None)
                        )
                    )
                )
    style = (
        db.get(StyleProfile, page.style_id or project.default_style_id)
        if page.style_id or project.default_style_id
        else None
    )
    if style:
        style_reference_ids = style.profile.get("reference_asset_ids", [])
        if style_reference_ids:
            references.extend(
                db.scalars(
                    select(Asset).where(
                        Asset.id.in_(style_reference_ids), Asset.deleted_at.is_(None)
                    )
                )
            )
    previous = db.scalar(
        select(MangaPage).where(
            MangaPage.chapter_id == page.chapter_id,
            MangaPage.page_number == page.page_number - 1,
        )
    )
    if previous and previous.selected_candidate_id:
        candidate = db.get(PageCandidate, previous.selected_candidate_id)
        if candidate and candidate.asset_id:
            previous_asset = db.get(Asset, candidate.asset_id)
            if previous_asset:
                references.append(previous_asset)
    return list({asset.id: asset for asset in references}.values())


def _save_generated_asset(db, candidate: PageCandidate, data: bytes) -> Asset:
    settings = get_settings()
    page = db.get(MangaPage, candidate.page_id)
    chapter = db.get(Chapter, page.chapter_id)
    digest = hashlib.sha256(data).hexdigest()
    existing = db.scalar(
        select(Asset).where(
            Asset.project_id == chapter.project_id,
            Asset.sha256 == digest,
        )
    )
    if existing:
        return existing
    destination = (
        settings.storage_root
        / "generated"
        / chapter.project_id
        / candidate.batch_id
        / f"{candidate.id}.png"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    try:
        with Image.open(destination) as image:
            width, height = image.size
            mime_type = Image.MIME.get(image.format or "PNG", "image/png")
    except OSError:
        width = height = None
        mime_type = "image/png"
    try:
        with db.begin_nested():
            asset = Asset(
                project_id=chapter.project_id,
                kind="page_candidate",
                original_name=f"page-{page.page_number}-candidate-{candidate.ordinal}.png",
                storage_key=destination.relative_to(settings.storage_root).as_posix(),
                mime_type=mime_type,
                byte_size=len(data),
                sha256=digest,
                width=width,
                height=height,
                source="AI_GENERATED",
                status="GENERATED",
            )
            db.add(asset)
            db.flush()
            thumbnails = create_thumbnails(destination, settings.storage_root, asset.id)
            asset.thumbnail_320_key = thumbnails[320]
            asset.thumbnail_640_key = thumbnails[640]
        return asset
    except IntegrityError:
        destination.unlink(missing_ok=True)
        existing = db.scalar(
            select(Asset).where(
                Asset.project_id == chapter.project_id,
                Asset.sha256 == digest,
            )
        )
        if existing:
            return existing
        raise
    except Exception:
        destination.unlink(missing_ok=True)
        if "asset" in locals() and asset.id:
            remove_thumbnails(settings.storage_root, asset.id)
        raise


def _run_page_generate(db, job: GenerationJob) -> None:
    candidate = db.get(PageCandidate, job.target_id)
    if not candidate:
        raise RuntimeError("候选记录不存在")
    page = db.get(MangaPage, candidate.page_id)
    if candidate.based_on_storyboard_version != page.storyboard_version:
        raise StaleStoryboardVersionError(
            "分镜版本已变化，已在调用模型前取消本次生成；请按当前分镜重新生成"
        )
    chapter = db.get(Chapter, page.chapter_id)
    project = db.get(Project, chapter.project_id)
    if not page.scene_ids or not page.beat_ids:
        raise RuntimeError("页面缺少剧本与分镜来源，禁止生成")
    if not page.source_coverage.get("complete"):
        raise RuntimeError("页面原文覆盖不完整，禁止生成")

    reference_selections = candidate.prompt_snapshot.get("reference_selections", {})
    prompt, snapshot = compile_page_prompt(db, page, project)
    reference_bindings: list[dict[str, str | None]] = []
    for character_id, selection in reference_selections.items():
        character = db.get(Character, character_id)
        outfit = db.get(Outfit, selection.get("outfit_id")) if selection.get("outfit_id") else None
        character_asset = db.get(Asset, selection.get("character_asset_id"))
        outfit_asset = (
            db.get(Asset, selection.get("outfit_asset_id"))
            if selection.get("outfit_asset_id")
            else None
        )
        reference_bindings.append(
            {
                "character": character.primary_name if character else character_id,
                "character_reference": (
                    character_asset.original_name if character_asset else None
                ),
                "outfit": outfit.name if outfit else None,
                "outfit_reference": outfit_asset.original_name if outfit_asset else None,
            }
        )
    if reference_bindings:
        prompt += (
            "\n本页人物与参考图绑定如下，必须逐项对应，不得串脸、串服装："
            + json.dumps(reference_bindings, ensure_ascii=False, separators=(",", ":"))
        )
    candidate.status = "GENERATING"
    page.status = PageStatus.DRAFT_GENERATING
    execution._commit_owned_progress(
        db, job, status=JobStatus.UPLOADING_REFERENCES, progress=20
    )

    reference_assets = _load_reference_assets(db, page, project, reference_selections)
    reference_bytes: list[bytes] = []
    reference_types: list[str] = []
    for asset in reference_assets:
        path = provider._asset_path(asset)
        if not path.is_file():
            raise RuntimeError(f"参考图文件不存在：{asset.original_name}")
        reference_bytes.append(path.read_bytes())
        reference_types.append(asset.mime_type)

    reference_asset_ids = [asset.id for asset in reference_assets]
    if job.job_type in {"PAGE_REPAIR", "PAGE_UPSCALE"}:
        original = db.get(PageCandidate, job.request_parameters.get("original_candidate_id"))
        if not original or not original.asset_id:
            raise RuntimeError("修复或升清任务缺少原始候选图")
        original_asset = db.get(Asset, original.asset_id)
        reference_bytes.insert(0, provider._asset_path(original_asset).read_bytes())
        reference_types.insert(0, original_asset.mime_type)
        reference_asset_ids.insert(0, original_asset.id)
        if job.job_type == "PAGE_REPAIR":
            repair = db.get(RepairPlan, job.request_parameters.get("repair_plan_id"))
            inspection = db.get(InspectionResult, repair.inspection_result_id) if repair else None
            if not repair or not inspection:
                raise RuntimeError("修复任务缺少检查结果或修复计划")
            repair_context = {
                "repair_type": repair.repair_type,
                "category": inspection.category,
                "outcome": inspection.outcome,
                "severity": inspection.severity,
                "details": inspection.details,
                "target_regions": repair.target_regions,
            }
            prompt += (
                "\n这是局部修复任务。严格根据以下检查结果修复指定范围："
                f"{json.dumps(repair_context, ensure_ascii=False, separators=(',', ':'))}。"
                "不得改动范围外的人物身份、服装、背景、格线、文字、镜头与构图；"
                "修复后仍输出完整页面。"
            )
        else:
            prompt += (
                "\n这是保持结构的升清任务。原始页是第一张参考图。像素级保持原有分格、"
                "人物姿态、脸、服装、道具、背景、文字内容与位置，只提高线稿、网点和边缘清晰度；"
                "禁止重构、增删格子或重写文字。"
            )

    snapshot["operation"] = job.job_type
    snapshot["reference_selections"] = reference_selections
    snapshot["reference_bindings"] = reference_bindings
    snapshot["prompt_preview"] = prompt
    snapshot["checksum"] = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    candidate.prompt_snapshot = snapshot

    binding = provider._binding(
        db,
        operation="image_edit" if reference_bytes else "image_generate",
        project_id=project.id,
        explicit_reference=(
            candidate.catalog_model_id or job.catalog_model_id or candidate.model_alias
        ),
        task_kind=job.job_type,
    )
    candidate.catalog_model_id = binding.resolved.model.id
    job.catalog_model_id = binding.resolved.model.id
    if not model_supports_resolution(binding.resolved.model, candidate.resolution.value):
        raise ProviderAdapterError(
            "UNSUPPORTED_CAPABILITY", "所选模型不支持当前输出清晰度"
        )
    provider._validate_reference_capacity(binding, len(reference_bytes))
    provider._lease_reference_assets(db, job, reference_asset_ids)
    # Re-read every leased row after committing the guard. A concurrent delete or
    # rebinding can no longer pass silently into the paid request.
    current_assets = list(
        db.scalars(
            select(Asset).where(
                Asset.id.in_(reference_asset_ids),
                Asset.deleted_at.is_(None),
            )
        )
    )
    if {item.id for item in current_assets} != set(reference_asset_ids):
        raise RuntimeError("参考图在生成前发生变化，已停止模型调用")

    execution._commit_owned_progress(db, job, status=JobStatus.GENERATING, progress=45)
    response = provider._invoke_provider(
        db,
        binding,
        lambda adapter: adapter.generate_page(
            ImageRequest(
                prompt=prompt,
                resolution=candidate.resolution.value,
                aspect_ratio="3:4",
                reference_images=tuple(reference_bytes),
                reference_mime_types=tuple(reference_types),
            )
        )
    )
    execution._ensure_job_not_cancelled(db, job)
    # An edit may arrive while the paid request is in flight. Keep the result, but
    # refresh the page so API consumers immediately expose it as a stale candidate.
    db.refresh(page, attribute_names=["storyboard_version"])
    asset = _save_generated_asset(db, candidate, response.images[0])
    record = GenerationRecord(
        job_id=job.id,
        provider=(binding.resolved.provider.preset_key or binding.resolved.provider.name)[:32],
        model_id=response.model_id,
        catalog_model_id=binding.resolved.model.id,
        location=str(
            binding.resolved.connection.nonsecret_config.get("region", "global")
        )[:64],
        parameters={
            "resolution": candidate.resolution.value,
            "aspect_ratio": "3:4",
            "operation": job.job_type,
            "protocol": binding.resolved.connection.protocol,
            "route_reason": binding.resolved.route_reason,
            "route_score": binding.resolved.route_score,
        },
        prompt_template=PAGE_TEMPLATE_VERSION,
        prompt_version=PAGE_TEMPLATE_VERSION,
        prompt_checksum=snapshot["checksum"],
        input_versions={
            "page": page.version,
            "page_revision": page.revision_no,
            "storyboard": candidate.based_on_storyboard_version,
        },
        reference_asset_ids=list(dict.fromkeys(reference_asset_ids)),
        provider_request_id=response.request_id,
        finished_at=utcnow(),
        usage=response.usage,
        output_asset_ids=[asset.id],
        status="COMPLETED",
    )
    db.add(record)
    db.flush()
    candidate.asset_id = asset.id
    candidate.generation_record_id = record.id
    candidate.status = "READY"
    page.status = PageStatus.DRAFT_READY
    page.version += 1
    provider.stage_attempt_output(
        db,
        asset,
        quality=candidate.resolution.value,
    )
