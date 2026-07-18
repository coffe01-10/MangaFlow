import hashlib
import json
from pathlib import Path
from threading import Lock

from fastapi import HTTPException
from PIL import Image
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.database import SessionLocal
from app.domain.states import JobStatus, PageStatus
from app.model_adapters.base import (
    ImageRequest,
    MultimodalRequest,
    ProviderAdapterError,
    StructuredRequest,
)
from app.models import (
    AIModel,
    Asset,
    AssetCandidate,
    Beat,
    Chapter,
    Character,
    CharacterReference,
    GenerationBatch,
    GenerationJob,
    GenerationRecord,
    InspectionResult,
    JobAssetReference,
    MangaPage,
    Outfit,
    PageCandidate,
    Panel,
    Project,
    ProviderConnection,
    ProviderProfile,
    RepairPlan,
    Scene,
    ScriptRevision,
    SourceRevision,
    SourceSegment,
    StyleProfile,
    utcnow,
)
from app.services.ai_schemas import PageInspectionOutput, StoryParseOutput, StyleAnalysisOutput
from app.services.credential_crypto import mark_key_failure, mark_key_success
from app.services.media import create_thumbnails, remove_thumbnails
from app.services.model_router import (
    AdapterBinding,
    ResolvedModel,
    bind_adapter,
    get_catalog_model,
    model_supports_resolution,
)
from app.services.prompt_compiler import PAGE_TEMPLATE_VERSION, compile_page_prompt
from app.services.provider_presets import ensure_provider_presets

ACTIVE_STATUSES = {
    JobStatus.PREPARING,
    JobStatus.UPLOADING_REFERENCES,
    JobStatus.GENERATING,
    JobStatus.OCR_CHECKING,
    JobStatus.CONSISTENCY_CHECKING,
    JobStatus.REPAIRING,
}
EXECUTION_RESERVATION_LOCK = Lock()


class StaleStoryboardVersionError(RuntimeError):
    """Stop a queued image call when its storyboard input has already changed."""


class JobCancelledError(RuntimeError):
    """Stop persisting provider output after a concurrent cancellation."""


def _ensure_job_not_cancelled(db, job: GenerationJob) -> None:
    db.refresh(job, attribute_names=["status", "cancelled_at"])
    if job.status == JobStatus.CANCELLED:
        raise JobCancelledError("任务已取消，模型返回结果不再写入")


def _normalize_name(value: str) -> str:
    return "".join(value.split()).casefold()


def _character_tokens(primary_name: str, aliases: list[str]) -> set[str]:
    return {
        normalized for value in [primary_name, *aliases] if (normalized := _normalize_name(value))
    }


def _match_existing_character(
    characters: list[Character],
    primary_name: str,
    aliases: list[str],
    claimed_ids: set[str],
) -> Character | None:
    """Prefer user-curated characters when the model returns one of their aliases."""

    incoming = _character_tokens(primary_name, aliases)
    matches = [
        character
        for character in characters
        if character.id not in claimed_ids
        and incoming & _character_tokens(character.primary_name, character.aliases)
    ]
    if not matches:
        return None

    status_priority = {
        "CANONICAL": 0,
        "UPLOADED": 1,
        "NEEDS_CONFIRMATION": 2,
        "ANALYZED": 3,
    }
    normalized_primary = _normalize_name(primary_name)

    def rank(character: Character) -> tuple[int, int, str]:
        status = getattr(character.status, "value", character.status)
        return (
            status_priority.get(str(status), 4),
            0 if _normalize_name(character.primary_name) == normalized_primary else 1,
            character.created_at.isoformat() if character.created_at else "",
        )

    return min(matches, key=rank)


def _asset_path(asset: Asset) -> Path:
    settings = get_settings()
    root = settings.upload_root if asset.source == "USER_UPLOAD" else settings.storage_root
    path = (root / asset.storage_key).resolve()
    if not path.is_relative_to(root.resolve()):
        raise RuntimeError("素材路径越界")
    return path


def _adapter(_alias: str):
    """Legacy test seam retained while production calls use catalog bindings."""

    return None


def _binding(
    db,
    *,
    operation: str,
    project_id: str,
    explicit_reference: str | None,
    task_kind: str,
) -> AdapterBinding:
    legacy_adapter = _adapter(explicit_reference or "auto")
    if legacy_adapter is not None:
        settings = get_settings()
        ensure_provider_presets(db, settings)
        model = get_catalog_model(db, explicit_reference or "")
        if model is None:
            model = db.scalar(
                select(AIModel).where(
                    AIModel.model_type == ("IMAGE" if operation.startswith("image_") else "TEXT")
                )
            )
        if model is None:
            raise ProviderAdapterError("MODEL_ROUTE_UNAVAILABLE", "测试模型目录不存在")
        connection = db.get(ProviderConnection, model.connection_id)
        provider = db.get(ProviderProfile, connection.provider_id) if connection else None
        if not connection or not provider:
            raise ProviderAdapterError("MODEL_ROUTE_UNAVAILABLE", "测试模型连接不存在")
        return AdapterBinding(
            resolved=ResolvedModel(model=model, connection=connection, provider=provider),
            adapter=legacy_adapter,
            selected_key=None,
        )
    try:
        return bind_adapter(
            db,
            get_settings(),
            operation=operation,
            explicit_reference=explicit_reference,
            project_id=project_id,
            task_kind=task_kind,
        )
    except HTTPException as error:
        detail = error.detail if isinstance(error.detail, str) else "模型路由配置无效"
        raise ProviderAdapterError("MODEL_ROUTE_UNAVAILABLE", detail) from error


def _invoke_provider(db, binding: AdapterBinding, callback):
    try:
        result = callback(binding.adapter)
    except ProviderAdapterError as error:
        if binding.selected_key:
            mark_key_failure(
                db,
                binding.selected_key.row,
                error.code,
                retry_after_seconds=error.retry_after_seconds,
            )
            if error.code in {"AUTHENTICATION", "PERMISSION", "RATE_LIMIT"}:
                try:
                    replacement = bind_adapter(
                        db,
                        get_settings(),
                        operation=binding.resolved.model.operations[0],
                        explicit_reference=binding.resolved.model.id,
                    )
                except HTTPException:
                    replacement = None
                if (
                    replacement
                    and replacement.selected_key
                    and replacement.selected_key.row.id != binding.selected_key.row.id
                ):
                    try:
                        result = callback(replacement.adapter)
                    except ProviderAdapterError as retry_error:
                        mark_key_failure(
                            db,
                            replacement.selected_key.row,
                            retry_error.code,
                            retry_after_seconds=retry_error.retry_after_seconds,
                        )
                        raise
                    mark_key_success(db, replacement.selected_key.row)
                    return result
        raise
    if binding.selected_key:
        mark_key_success(db, binding.selected_key.row)
    return result


def _text_model_reference(job: GenerationJob, project: Project) -> str | None:
    if job.catalog_model_id:
        return job.catalog_model_id
    if job.model_alias and job.model_alias != "text.fast":
        return job.model_alias
    return project.default_text_model_id or project.text_model_alias or job.model_alias


def _validate_reference_capacity(binding: AdapterBinding, count: int) -> None:
    configured = (binding.resolved.model.capabilities or {}).get("max_reference_images")
    if configured is not None and count > int(configured):
        raise ProviderAdapterError(
            "UNSUPPORTED_CAPABILITY",
            f"所选模型最多接收 {configured} 张参考图，本任务需要 {count} 张",
        )


def _lease_reference_assets(db, job: GenerationJob, asset_ids: list[str]) -> None:
    unique_ids = list(dict.fromkeys(asset_ids))
    active_ids = set(
        db.scalars(
            select(Asset.id).where(
                Asset.id.in_(unique_ids),
                Asset.project_id == job.project_id,
                Asset.deleted_at.is_(None),
            )
        )
    )
    if active_ids != set(unique_ids):
        raise RuntimeError("参考图已删除、失效或不属于当前项目，已停止模型调用")
    db.execute(delete(JobAssetReference).where(JobAssetReference.job_id == job.id))
    for asset_id in unique_ids:
        db.add(JobAssetReference(job_id=job.id, asset_id=asset_id))
    parameters = dict(job.request_parameters or {})
    parameters["reference_asset_ids"] = unique_ids
    job.request_parameters = parameters
    db.commit()


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


def _save_asset_candidate(db, candidate: AssetCandidate, project_id: str, data: bytes) -> Asset:
    settings = get_settings()
    digest = hashlib.sha256(data).hexdigest()
    existing = db.scalar(
        select(Asset).where(
            Asset.project_id == project_id,
            Asset.sha256 == digest,
        )
    )
    if existing:
        return existing
    destination = (
        settings.storage_root
        / "generated"
        / project_id
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
    batch = db.get(GenerationBatch, candidate.batch_id)
    try:
        with db.begin_nested():
            asset = Asset(
                project_id=project_id,
                kind=batch.generation_kind.lower(),
                original_name=(
                    f"{batch.generation_kind.lower()}-{candidate.variant.lower()}-"
                    f"{candidate.ordinal}.png"
                ),
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
                Asset.project_id == project_id,
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
    job.status = JobStatus.UPLOADING_REFERENCES
    job.progress = 20
    db.commit()

    reference_assets = _load_reference_assets(db, page, project, reference_selections)
    reference_bytes: list[bytes] = []
    reference_types: list[str] = []
    for asset in reference_assets:
        path = _asset_path(asset)
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
        reference_bytes.insert(0, _asset_path(original_asset).read_bytes())
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

    binding = _binding(
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
    _validate_reference_capacity(binding, len(reference_bytes))
    _lease_reference_assets(db, job, reference_asset_ids)
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

    job.status = JobStatus.GENERATING
    job.progress = 45
    db.commit()
    response = _invoke_provider(
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
    _ensure_job_not_cancelled(db, job)
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


def _run_story_parse(db, job: GenerationJob) -> None:
    chapter = db.get(Chapter, job.target_id)
    if not chapter or not chapter.current_source_revision_id:
        raise RuntimeError("章节原文不存在")
    revision = db.get(SourceRevision, chapter.current_source_revision_id)
    segments = list(
        db.scalars(
            select(SourceSegment)
            .where(SourceSegment.source_revision_id == revision.id)
            .order_by(SourceSegment.ordinal)
        )
    )
    source_payload = [
        {"id": item.id, "ordinal": item.ordinal, "text": item.text} for item in segments
    ]
    project = db.get(Project, chapter.project_id)
    mode_instruction = {
        "AUTO": (
            "自动模式：主动补充可视化动作、表情、环境、转场、潜台词和翻页悬念，但不得改变剧情。"
        ),
        "DIRECTOR": (
            "导演模式：只结构化原文明确给出的内容，不新增关键动作；无法判断的细节留空供用户指定。"
        ),
        "SEMI_AUTO": "半自动模式：补充镜头所需的动作、表情和环境细节，但不新增人物动机与剧情事实。",
    }[project.workflow_mode.value]
    prompt = f"""逐段把以下中文小说改写成可直接分镜的完整漫画剧本，禁止总结、删除或合并关键内容。
{mode_instruction}
提取角色主要姓名与绰号、场景地点/时间/天气/目的/情绪线，以及逐拍动作、原文对白、旁白、潜台词、情绪、重要度、
是否必须画出、能否和相邻拍合并、是否适合作为翻页悬念。
每个情节拍必须输出 character_presence：只有画面中实际可见的人物标记 VISIBLE，
画外说话标记 OFFSCREEN，仅在对白或叙述中被提及标记 MENTIONED；另把灵牌、遗像、
墓碑等场景物件写入 props，不能把物件代表的人物误标为 VISIBLE。
所有场景和情节拍必须携带输入中的 source_segment_ids 并覆盖全部输入；
剧本人物称呼必须使用 primary_name；每个有对白的情节拍必须把说话人的 primary_name
写入 speaker_name，旁白留空。
输入：{json.dumps(source_payload, ensure_ascii=False)}"""
    binding = _binding(
        db,
        operation="structured_text",
        project_id=project.id,
        explicit_reference=_text_model_reference(job, project),
        task_kind=job.job_type,
    )
    job.catalog_model_id = binding.resolved.model.id
    output = _invoke_provider(
        db,
        binding,
        lambda adapter: adapter.generate_structured(
            StructuredRequest(
                prompt=prompt,
                system_instruction="你是忠实的漫画剧本结构化编辑，原文覆盖率优先于篇幅。",
                temperature=0.15,
            ),
            StoryParseOutput,
        ),
    )
    _ensure_job_not_cancelled(db, job)
    project_id = chapter.project_id
    all_aliases: dict[str, str] = {}
    existing_characters = list(
        db.scalars(
            select(Character)
            .where(Character.project_id == project_id)
            .order_by(Character.created_at)
        )
    )
    claimed_character_ids: set[str] = set()
    for draft in output.characters:
        character = _match_existing_character(
            existing_characters,
            draft.primary_name,
            draft.aliases,
            claimed_character_ids,
        )
        primary_name = character.primary_name if character else draft.primary_name.strip()
        aliases = list(
            dict.fromkeys(
                item.strip()
                for item in [
                    *(character.aliases if character else []),
                    draft.primary_name,
                    *draft.aliases,
                ]
                if item.strip() and _normalize_name(item) != _normalize_name(primary_name)
            )
        )
        normalized = [_normalize_name(item) for item in aliases]
        normalized_primary = _normalize_name(primary_name)
        conflict = any(
            token in all_aliases and all_aliases[token] != normalized_primary
            for token in [normalized_primary, *normalized]
        )
        for token in [normalized_primary, *normalized]:
            all_aliases.setdefault(token, normalized_primary)
        if character:
            character.aliases = aliases
            character.aliases_normalized = normalized
            character.alias_conflict = conflict
            character.canonical_description = draft.description or character.canonical_description
            character.version += 1
            claimed_character_ids.add(character.id)
        else:
            character = Character(
                project_id=project_id,
                primary_name=primary_name,
                aliases=aliases,
                aliases_normalized=normalized,
                alias_conflict=conflict,
                canonical_description=draft.description,
                status="NEEDS_CONFIRMATION" if conflict else "ANALYZED",
            )
            db.add(character)
            db.flush()
            existing_characters.append(character)
            claimed_character_ids.add(character.id)
    db.flush()
    character_map: dict[str, Character] = {}
    for character in db.scalars(select(Character).where(Character.project_id == project_id)):
        character_map[_normalize_name(character.primary_name)] = character
        for alias in character.aliases:
            character_map[_normalize_name(alias)] = character
    db.execute(delete(Scene).where(Scene.chapter_id == chapter.id))
    db.execute(delete(ScriptRevision).where(ScriptRevision.chapter_id == chapter.id))
    db.flush()
    covered_segment_ids: set[str] = set()
    for scene_draft in output.scenes:
        covered_segment_ids.update(scene_draft.source_segment_ids)
        scene = Scene(
            chapter_id=chapter.id,
            ordinal=scene_draft.ordinal,
            location=scene_draft.location,
            time_label=scene_draft.time_label,
            weather=scene_draft.weather,
            purpose=scene_draft.purpose,
            emotional_arc=scene_draft.emotional_arc,
            source_range={"segment_ids": scene_draft.source_segment_ids},
        )
        db.add(scene)
        db.flush()
        for beat_draft in scene_draft.beats:
            covered_segment_ids.update(beat_draft.source_segment_ids)
            speaker_name = beat_draft.speaker_name.strip()
            if speaker_name:
                speaker = character_map.get(_normalize_name(speaker_name))
                speaker_name = speaker.primary_name if speaker else speaker_name
            db.add(
                Beat(
                    scene_id=scene.id,
                    ordinal=beat_draft.ordinal,
                    action=beat_draft.action,
                    speaker_name=speaker_name,
                    dialogue=beat_draft.dialogue,
                    narration=beat_draft.narration,
                    subtext=beat_draft.subtext,
                    emotion=beat_draft.emotion,
                    importance=beat_draft.importance,
                    must_visualize=beat_draft.must_visualize,
                    mergeable=beat_draft.mergeable,
                    page_turn_hook=beat_draft.page_turn_hook,
                    source_range={
                        "segment_ids": beat_draft.source_segment_ids,
                        "character_presence": {
                            key: value.value
                            for key, value in beat_draft.character_presence.items()
                        },
                        "props": beat_draft.props,
                    },
                )
            )
    expected_segment_ids = {item.id for item in segments}
    missing_segment_ids = sorted(expected_segment_ids - covered_segment_ids)
    script = ScriptRevision(
        chapter_id=chapter.id,
        source_revision_id=revision.id,
        revision_no=1,
        status="READY" if not missing_segment_ids else "INCOMPLETE",
        coverage={
            "expected": len(expected_segment_ids),
            "covered": len(expected_segment_ids) - len(missing_segment_ids),
            "ratio": round(
                (len(expected_segment_ids) - len(missing_segment_ids)) / len(expected_segment_ids),
                4,
            )
            if expected_segment_ids
            else 1,
            "missing_segment_ids": missing_segment_ids,
        },
    )
    db.add(script)
    chapter.status = "SCRIPT_READY" if not missing_segment_ids else "SCRIPT_INCOMPLETE"
    chapter.version += 1


def _run_asset_generate(db, job: GenerationJob) -> None:
    candidate = db.get(AssetCandidate, job.target_id)
    if not candidate:
        raise RuntimeError("资产候选不存在")
    batch = db.get(GenerationBatch, candidate.batch_id)
    if not batch:
        raise RuntimeError("资产生成批次不存在")
    references: list[Asset] = []
    if batch.target_type == "CHARACTER":
        character = db.get(Character, batch.target_id)
        if not character:
            raise RuntimeError("角色档案不存在")
        references = list(
            db.scalars(
                select(Asset)
                .join(CharacterReference, CharacterReference.asset_id == Asset.id)
                .where(
                    CharacterReference.character_id == character.id,
                    Asset.deleted_at.is_(None),
                    Asset.project_id == batch.project_id,
                    Asset.kind == "CHARACTER_REFERENCE",
                )
                .order_by(CharacterReference.is_canonical.desc())
            )
        )
        subject = {
            "primary_name": character.primary_name,
            "aliases": character.aliases,
            "description": character.canonical_description,
            "locked_features": character.locked_features,
        }
    elif batch.target_type == "OUTFIT":
        outfit = db.get(Outfit, batch.target_id)
        if not outfit:
            raise RuntimeError("服装档案不存在")
        character = db.get(Character, outfit.character_id)
        if not character:
            raise RuntimeError("服装所属角色不存在")
        character_references = list(
            db.scalars(
                select(Asset)
                .join(CharacterReference, CharacterReference.asset_id == Asset.id)
                .where(
                    CharacterReference.character_id == character.id,
                    Asset.deleted_at.is_(None),
                    Asset.project_id == batch.project_id,
                    Asset.kind == "CHARACTER_REFERENCE",
                )
            )
        )
        outfit_references = (
            list(
                db.scalars(
                    select(Asset).where(
                        Asset.id.in_(outfit.reference_asset_ids),
                        Asset.project_id == batch.project_id,
                        Asset.deleted_at.is_(None),
                        Asset.kind == "OUTFIT_REFERENCE",
                    )
                )
            )
            if outfit.reference_asset_ids
            else []
        )
        references = [*character_references, *outfit_references]
        subject = {
            "character": character.primary_name,
            "outfit": outfit.name,
            "components": outfit.components,
            "state_rules": outfit.state_rules,
            "locked_fields": outfit.locked_fields,
        }
    elif batch.target_type == "STYLE":
        style = db.get(StyleProfile, batch.target_id)
        if not style:
            raise RuntimeError("风格档案不存在")
        reference_ids = style.profile.get("reference_asset_ids", [])
        references = (
            list(
                db.scalars(
                    select(Asset).where(
                        Asset.id.in_(reference_ids),
                        Asset.project_id == batch.project_id,
                        Asset.deleted_at.is_(None),
                        Asset.kind == "STYLE_REFERENCE",
                    )
                )
            )
            if reference_ids
            else []
        )
        subject = {
            "name": style.name,
            "color_mode": style.color_mode,
            "profile": style.profile,
            "locked_fields": style.locked_fields,
        }
    else:
        raise RuntimeError("资产生成目标类型无效")
    if batch.target_type == "CHARACTER" and not references:
        raise RuntimeError("角色参考图已失效，请重新绑定后再生成")
    if batch.target_type == "OUTFIT":
        if not character_references:
            raise RuntimeError("服装所属角色的参考图已失效，请重新绑定后再生成")
        if not outfit_references:
            raise RuntimeError("服装参考图已失效，请重新绑定后再生成")
    if batch.target_type == "STYLE" and not references:
        raise RuntimeError("漫画风格参考图已失效，请重新绑定后再生成")
    prompt_payload = {
        "target_type": batch.target_type,
        "variant": candidate.variant,
        "instruction": candidate.instruction,
        "subject": subject,
    }
    asset_color_mode = subject.get("color_mode", "reference")
    task_instruction = (
        "在同一张角色设定页中同时展示正面、侧面、背面、代表性表情和关键局部细节；"
        "所有视图必须是同一个角色，版面清楚但不生成任何文字标签。"
        if candidate.variant == "SHEET"
        else (
            "在同一张服装角色设定页中展示角色穿着指定服装的正面、侧面、背面、"
            "代表性表情和服装关键局部；同时服从人物与服装参考，不得改变角色身份。"
            if candidate.variant == "OUTFIT_SHEET"
            else {
                "CHARACTER": (
                    "按 variant 生成同一角色的单一标准视图；"
                    "必须保持脸、发型、体型和标志特征一致。"
                ),
                "OUTFIT": (
                    "生成该角色穿着指定服装的完整全身造型图；同时服从人物参考和服装参考，"
                    "准确还原服装剪裁、层次、配饰与状态，不改变角色身份。"
                ),
                "STYLE": (
            "生成不含现有作品角色的原创风格测试页，用简单人物与背景验证线稿、"
            + (
                "网点、黑白对比和分格语言，"
                if asset_color_mode == "monochrome"
                else "色板、上色方式、光影层次和分格语言，"
            )
            + "不复制参考漫画的文字与剧情。"
                ),
            }[batch.target_type]
        )
    )
    base_instruction = (
        "生成黑白日式漫画规范资产图。"
        if batch.target_type == "STYLE" and asset_color_mode == "monochrome"
        else "生成彩色日式漫画规范资产图。"
        if batch.target_type == "STYLE"
        else "生成漫画制作规范资产图，色彩与明暗服从参考素材。"
    )
    prompt = (
        base_instruction
        + task_instruction
        + "不要加入文字水印；背景使用便于比对的简洁浅色。输入："
        + json.dumps(prompt_payload, ensure_ascii=False)
    )
    checksum = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    candidate.prompt_snapshot = {
        "template": "asset-v1.1.0",
        "checksum": checksum,
        "input": prompt_payload,
        "prompt_preview": prompt,
    }
    candidate.status = "GENERATING"
    job.status = JobStatus.GENERATING
    job.progress = 45
    db.commit()
    reference_ids = [asset.id for asset in references]
    _lease_reference_assets(db, job, reference_ids)
    for asset in references:
        if not _asset_path(asset).is_file():
            raise RuntimeError(f"参考图文件不存在：{asset.original_name}")
    reference_bytes = [_asset_path(asset).read_bytes() for asset in references]
    reference_types = [asset.mime_type for asset in references]
    binding = _binding(
        db,
        operation="image_edit" if reference_bytes else "image_generate",
        project_id=batch.project_id,
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
    _validate_reference_capacity(binding, len(reference_bytes))
    db.commit()
    response = _invoke_provider(
        db,
        binding,
        lambda adapter: adapter.generate_asset(
            ImageRequest(
                prompt=prompt,
                resolution=candidate.resolution.value,
                aspect_ratio=(
                    "4:3"
                    if candidate.variant in {"SHEET", "OUTFIT_SHEET"}
                    else "3:4"
                ),
                reference_images=tuple(reference_bytes),
                reference_mime_types=tuple(reference_types),
            )
        )
    )
    _ensure_job_not_cancelled(db, job)
    asset = _save_asset_candidate(db, candidate, batch.project_id, response.images[0])
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
            "variant": candidate.variant,
            "protocol": binding.resolved.connection.protocol,
            "route_reason": binding.resolved.route_reason,
            "route_score": binding.resolved.route_score,
        },
        prompt_template="asset-v1.1.0",
        prompt_version="asset-v1.1.0",
        prompt_checksum=checksum,
        input_versions={"batch": batch.version},
        reference_asset_ids=[asset.id for asset in references],
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
    if batch.target_type == "STYLE" and candidate.variant == "STYLE_TEST":
        style = db.get(StyleProfile, batch.target_id)
        if style:
            style.status = "TEST_GENERATED"
            style.version += 1


def _build_style_prompt_summary(analyzed: dict, color_mode: str) -> str:
    """Compile visual language without leaking subjects from the reference page."""

    prefix = "彩色日式漫画" if color_mode == "color" else "黑白日式漫画"
    visual_parts = [
        analyzed.get("line_art", ""),
        analyzed.get("screentone", ""),
        analyzed.get("contrast", ""),
        analyzed.get("panel_language", ""),
        analyzed.get("lighting", ""),
    ]
    return "；".join([prefix, *(part for part in visual_parts if part)])


def _build_color_palette(analyzed: dict) -> dict[str, str]:
    """Recover an editable palette when the model omits the optional palette object."""

    palette = analyzed.get("palette")
    if isinstance(palette, dict) and palette:
        return {str(key): str(value) for key, value in palette.items() if str(value).strip()}

    color_rules = [str(rule) for rule in analyzed.get("color_rules", []) if str(rule).strip()]
    return {
        "主色": color_rules[0] if color_rules else "低饱和冷灰蓝，保持克制与潮湿感",
        "辅助色": "低明度卡其灰与雾紫，只用于小面积识别和层次",
        "肤色": "偏冷的自然肤色，保留血色但避免过度红润",
        "发色": "深黑与低明度识别色，保留发丝层次和角色辨识度",
        "环境色": "潮湿京都的蓝灰、纸门米灰与深木色",
        "光影色": analyzed.get("lighting") or "柔和冷色散射光，阴影不使用纯黑硬切",
    }


def _run_style_analyze(db, job: GenerationJob) -> None:
    style = db.get(StyleProfile, job.target_id)
    if not style:
        raise RuntimeError("风格档案不存在")
    reference_ids = style.profile.get("reference_asset_ids", [])
    references = list(
        db.scalars(
            select(Asset).where(
                Asset.id.in_(reference_ids),
                Asset.deleted_at.is_(None),
                Asset.kind == "STYLE_REFERENCE",
            )
        )
    )
    if not references:
        raise RuntimeError("风格档案没有可用漫画参考图")
    job.status = JobStatus.GENERATING
    job.progress = 35
    db.commit()
    visual_dimensions = (
        "线稿、网点、黑白对比、留白、人物画法、背景画法、光影"
        if style.color_mode == "monochrome"
        else "线稿、主辅色板、肤色与发色、上色方式、色彩光影、人物画法、背景画法"
    )
    atmosphere = job.request_parameters.get("palette_atmosphere", "")
    prompt = f"""分析这些漫画参考页的视觉风格，只总结可复用的画面语言，不识别作者姓名或作品名。
目标输出类型是{'黑白漫画' if style.color_mode == 'monochrome' else '彩色漫画'}。
输出{visual_dimensions}、日式分格语言、构图规则、禁止项，
以及一段可直接用于生图的中文 prompt_summary。彩色模式必须额外输出 palette，包含
主色、辅助色、肤色、发色、环境色和光影色，并输出 color_rules。
章节氛围补充：{atmosphere or '葬礼后的克制、潮湿京都与低饱和情绪'}。
不要复制参考页中的文字或剧情。"""
    _lease_reference_assets(db, job, [asset.id for asset in references[:8]])
    project = db.get(Project, style.project_id)
    binding = _binding(
        db,
        operation="multimodal_analysis",
        project_id=style.project_id,
        explicit_reference=_text_model_reference(job, project),
        task_kind=job.job_type,
    )
    job.catalog_model_id = binding.resolved.model.id
    output = _invoke_provider(
        db,
        binding,
        lambda adapter: adapter.analyze_multimodal(
            MultimodalRequest(
                prompt=prompt,
                images=tuple(_asset_path(asset).read_bytes() for asset in references[:8]),
                mime_types=tuple(asset.mime_type for asset in references[:8]),
            ),
            StyleAnalysisOutput,
        ),
    )
    _ensure_job_not_cancelled(db, job)
    analyzed = output.model_dump()
    analyzed["prompt_summary"] = _build_style_prompt_summary(analyzed, style.color_mode)
    analyzed["reference_asset_ids"] = reference_ids
    analyzed["palette_draft"] = (
        _build_color_palette(analyzed) if style.color_mode == "color" else {}
    )
    analyzed.pop("palette", None)
    analyzed["palette_confirmed"] = False
    analyzed["test_image_approved"] = False
    style.profile = analyzed
    if style.color_mode == "color":
        style.locked_fields = [
            "细腻线稿" if field == "黑白墨线" else field
            for field in style.locked_fields
            if field != "禁止彩色"
        ]
        if "低饱和色板" not in style.locked_fields:
            style.locked_fields = [*style.locked_fields, "低饱和色板"]
    style.status = "DRAFT"
    style.version += 1
    job.progress = 90


def _run_inspection(db, job: GenerationJob) -> None:
    candidate = db.get(PageCandidate, job.target_id)
    if not candidate or not candidate.asset_id:
        raise RuntimeError("候选图片尚未生成")
    page = db.get(MangaPage, candidate.page_id)
    asset = db.get(Asset, candidate.asset_id)
    project = db.get(Project, db.get(Chapter, page.chapter_id).project_id)
    _, snapshot = compile_page_prompt(db, page, project)
    categories = job.request_parameters.get(
        "categories",
        ["SPEAKER", "CHARACTER", "OUTFIT", "PROP", "CONTINUITY"],
    )
    prompt = f"""你是漫画成片质检员。对照结构化目标检查这张生成漫画页。
只检查这些类别：{json.dumps(categories, ensure_ascii=False)}。
目标剧本、格位、说话人、角色、服装与风格上下文：
{json.dumps(snapshot["input"], ensure_ascii=False, separators=(",", ":"))}
SPEAKER 检查气泡归属；
CHARACTER 检查脸、发型、体型和标志特征；OUTFIT 检查场景指定服装；
PROP 检查关键道具；CONTINUITY 检查与页面结构、场景状态和前后逻辑的一致性。
每个请求类别至少输出一项，字段为 category、outcome、score、severity、details、regions；
outcome 只能用 PASS、ACCEPTABLE、MISMATCH、MISSING、EXTRA；
details 必须写清 expected、observed 和 differences。
regions 使用 0 到 1 的归一化 x/y/width/height。"""
    job.status = JobStatus.CONSISTENCY_CHECKING
    job.progress = 45
    db.commit()
    _lease_reference_assets(db, job, [asset.id])
    binding = _binding(
        db,
        operation="multimodal_analysis",
        project_id=project.id,
        explicit_reference=_text_model_reference(job, project),
        task_kind=job.job_type,
    )
    job.catalog_model_id = binding.resolved.model.id
    output = _invoke_provider(
        db,
        binding,
        lambda adapter: adapter.analyze_multimodal(
            MultimodalRequest(
                prompt=prompt,
                images=(_asset_path(asset).read_bytes(),),
                mime_types=(asset.mime_type,),
            ),
            PageInspectionOutput,
        ),
    )
    _ensure_job_not_cancelled(db, job)
    needs_review = False
    for item in output.items:
        if item.outcome not in {"MATCH", "PASS", "ACCEPTABLE"}:
            needs_review = True
        db.add(
            InspectionResult(
                generation_record_id=candidate.generation_record_id,
                candidate_id=candidate.id,
                category=item.category,
                outcome=item.outcome,
                score=item.score,
                details=item.details.model_dump(),
                regions=item.regions,
                severity=item.severity,
            )
        )
    candidate.status = "NEEDS_REVIEW" if needs_review else "INSPECTED"
    page.continuity_status = "NEEDS_REVIEW" if needs_review else "PASSED"
    job.status = JobStatus.CONSISTENCY_CHECKING
    job.progress = 85


def execute_job(job_id: str) -> None:
    db = SessionLocal()
    job = db.get(GenerationJob, job_id)
    if not job or job.status == JobStatus.CANCELLED:
        db.close()
        return
    try:
        project = db.get(Project, job.project_id)
        if not project or project.deleted_at is not None:
            from app.services.job_service import mark_job_cancelled

            mark_job_cancelled(db, job)
            db.commit()
            return
        with EXECUTION_RESERVATION_LOCK:
            active = (
                db.scalar(
                    select(func.count(GenerationJob.id)).where(
                        GenerationJob.project_id == job.project_id,
                        GenerationJob.status.in_(ACTIVE_STATUSES),
                        GenerationJob.id != job.id,
                    )
                )
                or 0
            )
            if project and active >= project.default_concurrency:
                job.status = JobStatus.WAITING
                job.error_code = "CONCURRENCY_LIMIT"
                job.error_message = "等待项目并发名额"
                db.commit()
                return
            job.status = JobStatus.PREPARING
            job.progress = 5
            job.attempt_count += 1
            job.started_at = job.started_at or utcnow()
            db.commit()
        if job.job_type in {"PAGE_GENERATE", "PAGE_REPAIR", "PAGE_UPSCALE"}:
            _run_page_generate(db, job)
        elif job.job_type == "ASSET_GENERATE":
            _run_asset_generate(db, job)
        elif job.job_type == "SOURCE_PARSE":
            _run_story_parse(db, job)
        elif job.job_type == "STYLE_ANALYZE":
            _run_style_analyze(db, job)
        elif job.job_type == "PAGE_INSPECT":
            _run_inspection(db, job)
        elif job.job_type == "WORKFLOW_NODE":
            from app.services.workflow_engine import execute_workflow_node

            execute_workflow_node(db, job)
        else:
            raise RuntimeError(f"未知任务类型：{job.job_type}")
        _ensure_job_not_cancelled(db, job)
        workflow_run_id = job.request_parameters.get("workflow_run_id")
        db.expire(
            job,
            attribute_names=[
                "status",
                "progress",
                "finished_at",
                "error_code",
                "error_message",
            ],
        )
        with db.no_autoflush:
            completed = db.execute(
                update(GenerationJob)
                .where(
                    GenerationJob.id == job.id,
                    GenerationJob.status != JobStatus.CANCELLED,
                )
                .values(
                    status=JobStatus.COMPLETED,
                    progress=100,
                    finished_at=utcnow(),
                    error_code=None,
                    error_message=None,
                )
                .execution_options(synchronize_session=False)
            )
        if completed.rowcount != 1:
            raise JobCancelledError("任务已取消，完成状态不再写入")
        db.commit()
        if workflow_run_id:
            from app.services.workflow_engine import reconcile_run

            reconcile_run(db, workflow_run_id)
    except JobCancelledError:
        db.rollback()
        db.expire_all()
        job = db.get(GenerationJob, job_id)
        if job and job.status != JobStatus.COMPLETED:
            from app.services.job_service import mark_job_cancelled

            mark_job_cancelled(db, job)
            db.commit()
        return
    except StaleStoryboardVersionError as error:
        db.rollback()
        job = db.get(GenerationJob, job_id)
        job.status = JobStatus.FAILED
        job.error_code = "STALE_STORYBOARD_VERSION"
        job.error_message = str(error)
        job.finished_at = utcnow()
        candidate = db.get(PageCandidate, job.target_id)
        if candidate:
            candidate.status = "STALE"
        db.commit()
        if job.request_parameters.get("workflow_run_id"):
            from app.services.workflow_engine import reconcile_run

            reconcile_run(db, job.request_parameters["workflow_run_id"])
        raise
    except ProviderAdapterError as error:
        db.rollback()
        job = db.get(GenerationJob, job_id)
        job.status = JobStatus.FAILED
        job.error_code = error.code
        job.error_message = error.user_message
        job.finished_at = utcnow()
        candidate = db.get(PageCandidate, job.target_id)
        if candidate:
            candidate.status = "FAILED"
        asset_candidate = db.get(AssetCandidate, job.target_id)
        if asset_candidate:
            asset_candidate.status = "FAILED"
        style = db.get(StyleProfile, job.target_id) if job.target_type == "STYLE" else None
        if style:
            style.status = "DRAFT"
        db.commit()
        if job.request_parameters.get("workflow_run_id"):
            from app.services.workflow_engine import reconcile_run

            reconcile_run(db, job.request_parameters["workflow_run_id"])
        raise
    except Exception as error:
        db.rollback()
        job = db.get(GenerationJob, job_id)
        job.status = JobStatus.FAILED
        job.error_code = "WORKER_ERROR"
        job.error_message = str(error)[:500]
        job.finished_at = utcnow()
        candidate = db.get(PageCandidate, job.target_id)
        if candidate:
            candidate.status = "FAILED"
        asset_candidate = db.get(AssetCandidate, job.target_id)
        if asset_candidate:
            asset_candidate.status = "FAILED"
        style = db.get(StyleProfile, job.target_id) if job.target_type == "STYLE" else None
        if style:
            style.status = "DRAFT"
        db.commit()
        if job.request_parameters.get("workflow_run_id"):
            from app.services.workflow_engine import reconcile_run

            reconcile_run(db, job.request_parameters["workflow_run_id"])
        raise
    finally:
        db.close()
