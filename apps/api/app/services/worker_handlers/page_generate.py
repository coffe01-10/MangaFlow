"""PAGE_GENERATE / PAGE_REPAIR / PAGE_UPSCALE / PAGE_REGION_REGENERATE handler.

Owns storyboard guard, reference loading, prompt snapshot, the paid image
call and candidate/asset persistence for page-level jobs.  Cancellation and
lease checks stay owned by the execution shell via ``execution`` helpers.
"""

import copy
import hashlib
import json
import logging

from PIL import Image
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.domain.states import JobStatus, PageStatus
from app.model_adapters.base import ImageRequest, ProviderAdapterError
from app.models import (
    Asset,
    Chapter,
    Character,
    CharacterModelPackageVersion,
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
from app.services.asset_dedupe import adopt_deleted_duplicate, live_duplicate
from app.services.media import create_thumbnails, remove_thumbnails
from app.services.model_capabilities import (
    REGION_EDIT_SURFACE_LABELS,
    model_region_edit_surface,
    model_supports_explicit_mask,
)
from app.services.model_router import model_supports_resolution
from app.services.ordinal_allocator import lock_entity
from app.services.prompt_compiler import (
    PAGE_TEMPLATE_VERSION,
    STRUCTURED_BLOCK_MAX_CHARS,
    _bound_structured_block,
    compile_page_prompt,
)
from app.services.worker_handlers import execution, provider
from app.services.worker_handlers.execution import (
    JobCancelledError,
    StaleStoryboardVersionError,
)

LOGGER = logging.getLogger("mangaflow.worker.page_generate")


def _asset_blob_bytes(asset: Asset) -> bytes:
    """Read an asset blob with a missing-file preflight (#210-5).

    Terminal INVALID_INPUT instead of a raw FileNotFoundError (which the
    worker classifies as retryable WORKER_ERROR and re-pays). The ``is_file``
    probe tolerates the legacy ``_asset_path`` test seam (bare namespaces
    exposing only ``read_bytes``).
    """

    path = provider._asset_path(asset)
    is_file = getattr(path, "is_file", None)
    if callable(is_file) and not is_file():
        raise ProviderAdapterError(
            "INVALID_INPUT",
            f"素材文件缺失，已终止任务：{asset.original_name}",
            retryable=False,
        )
    return path.read_bytes()


def _load_reference_assets(
    db,
    page: MangaPage,
    project: Project,
    reference_selections: dict[str, dict[str, str | None]] | None = None,
    scene_reference_ids: list[str] | None = None,
    queued_character_packages: dict[str, dict] | None = None,
) -> list[Asset]:
    page_character_ids = {
        character_id
        for panel in db.scalars(select(Panel).where(Panel.page_id == page.id))
        for character_id in panel.characters
    }
    package_facts = queued_character_packages or {}
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
        # #642: the prompt's binding declaration iterates
        # reference_selections (character reference first, outfit second per
        # character), and ImageRequest only ships image bytes — position is
        # the model's only alignment channel for the "逐项对应" mapping. The
        # id-set query above returns rows in database order, so reorder the
        # loaded references to the declaration order; a stable sort keeps any
        # id outside the selection list (none here — appended scene/style/
        # continuity references below) behind the selected ones.
        declared_positions = {
            asset_id: index
            for index, asset_id in enumerate(
                selected_id
                for character_id, selection in reference_selections.items()
                for selected_id in (
                    selection.get("character_asset_id"),
                    selection.get("outfit_asset_id"),
                )
                if selected_id
            )
        }
        references.sort(
            key=lambda asset: declared_positions.get(asset.id, len(declared_positions))
        )
        for character_id in page_character_ids:
            if character_id in package_facts:
                # Contract §8.5: package candidates consume the queue-time
                # snapshot; version bindings are never re-validated here.
                continue
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
    if scene_reference_ids:
        scene_references = list(
            db.scalars(
                select(Asset).where(
                    Asset.id.in_(scene_reference_ids),
                    Asset.project_id == project.id,
                    Asset.deleted_at.is_(None),
                )
            )
        )
        loaded_scene_ids = {item.id for item in scene_references}
        if loaded_scene_ids != set(scene_reference_ids):
            missing = sorted(set(scene_reference_ids) - loaded_scene_ids)
            raise RuntimeError(
                "场景参考图已删除或失效，已在调用模型前停止任务：" + "、".join(missing)
            )
        references.extend(scene_references)
    style = (
        db.get(StyleProfile, page.style_id or project.default_style_id)
        if page.style_id or project.default_style_id
        else None
    )
    if style:
        style_reference_ids = style.profile.get("reference_asset_ids", [])
        if style_reference_ids:
            style_references = list(
                db.scalars(
                    select(Asset).where(
                        Asset.id.in_(style_reference_ids), Asset.deleted_at.is_(None)
                    )
                )
            )
            loaded_style_ids = {item.id for item in style_references}
            if loaded_style_ids != set(style_reference_ids):
                # #236-2 fence: the deleted_at filter used to silently shrink
                # the reference list, so a paid call could lose the style
                # image with no signal — the generated page drifted from the
                # style the user selected. Same pre-call contract as the
                # scene/selection reference loads above.
                missing_style = sorted(set(style_reference_ids) - loaded_style_ids)
                raise RuntimeError(
                    "风格参考图已删除或失效，已在调用模型前停止任务："
                    + "、".join(missing_style)
                )
            references.extend(style_references)
    previous = db.scalar(
        select(MangaPage).where(
            MangaPage.chapter_id == page.chapter_id,
            MangaPage.page_number == page.page_number - 1,
        )
    )
    if previous and previous.selected_candidate_id:
        # Mirror the deleted_at filters every other reference load in this
        # function applies (:176 outfits, :186 scene, :208 style): a page can
        # legally hold a selected candidate that was soft-deleted afterwards
        # (#137). A deleted candidate/asset must never feed the paid prompt
        # as a continuity reference — skip it instead.
        previous_candidate = db.scalar(
            select(PageCandidate).where(
                PageCandidate.id == previous.selected_candidate_id,
                PageCandidate.deleted_at.is_(None),
            )
        )
        if previous_candidate is None:
            LOGGER.info(
                "上一页（%s）已采用候选 %s 已删除或不存在，跳过该连续性参考",
                previous.id,
                previous.selected_candidate_id,
            )
        elif previous_candidate.asset_id:
            previous_asset = db.scalar(
                select(Asset).where(
                    Asset.id == previous_candidate.asset_id,
                    Asset.deleted_at.is_(None),
                )
            )
            if previous_asset is None:
                LOGGER.info(
                    "上一页（%s）候选 %s 的图片素材已删除，跳过该连续性参考",
                    previous.id,
                    previous_candidate.id,
                )
            else:
                references.append(previous_asset)
    return list({asset.id: asset for asset in references}.values())


def _save_generated_asset(db, candidate: PageCandidate, data: bytes) -> Asset:
    settings = get_settings()
    page = db.get(MangaPage, candidate.page_id)
    chapter = db.get(Chapter, page.chapter_id)
    digest = hashlib.sha256(data).hexdigest()
    # Dedupe must only consider live AI-generated page candidates. Asset holds
    # a hard UNIQUE(project_id, sha256), so an unfiltered match can hand a new
    # paid candidate a soft-deleted asset (content 404s) or a byte-identical
    # user upload / another generation kind's row.
    existing = live_duplicate(
        db,
        project_id=chapter.project_id,
        sha256=digest,
        source="AI_GENERATED",
        kind="page_candidate",
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
            thumbnails = create_thumbnails(
                destination,
                settings.storage_root,
                asset.id,
                max_pixels=settings.max_image_pixels,
                max_side=settings.max_image_side,
            )
            asset.thumbnail_320_key = thumbnails[320]
            asset.thumbnail_640_key = thumbnails[640]
        return asset
    except IntegrityError:
        # The insert can only collide on UNIQUE(project_id, sha256): a live
        # matching row appeared concurrently, or a soft-deleted generated page
        # candidate holds the digest (asset deletes unlink no files). Flush so
        # the re-queries below observe every row this transaction can see.
        db.flush()
        existing = live_duplicate(
            db,
            project_id=chapter.project_id,
            sha256=digest,
            source="AI_GENERATED",
            kind="page_candidate",
        )
        if existing:
            destination.unlink(missing_ok=True)
            # The thumbnails were written for the never-committed asset row
            # inside the rolled-back savepoint; the boot-time orphan sweep
            # would get them in a week, but this branch knows the id — clean
            # up now like the generic failure path below does.
            if "asset" in locals() and asset.id:
                remove_thumbnails(settings.storage_root, asset.id)
            return existing
        deleted = adopt_deleted_duplicate(
            db,
            project_id=chapter.project_id,
            sha256=digest,
            source="AI_GENERATED",
            kind="page_candidate",
        )
        if deleted:
            # Revive in place — mirroring upload_asset — because the hard
            # UNIQUE(project_id, sha256) makes a fresh row impossible. Keep the
            # NEW file and repoint the row at it, regenerating thumbnails; the
            # previous file stays on disk so a later rollback cannot orphan the
            # row (and delete already left every byte in place).
            remove_thumbnails(settings.storage_root, deleted.id)
            thumbnails = create_thumbnails(
                destination,
                settings.storage_root,
                deleted.id,
                max_pixels=settings.max_image_pixels,
                max_side=settings.max_image_side,
            )
            deleted.original_name = (
                f"page-{page.page_number}-candidate-{candidate.ordinal}.png"
            )
            deleted.storage_key = destination.relative_to(
                settings.storage_root
            ).as_posix()
            deleted.thumbnail_320_key = thumbnails[320]
            deleted.thumbnail_640_key = thumbnails[640]
            deleted.mime_type = mime_type
            deleted.byte_size = len(data)
            deleted.width = width
            deleted.height = height
            deleted.status = "GENERATED"
            deleted.deleted_at = None
            deleted.version += 1
            return deleted
        # A non-generated or wrong-kind row owns the digest and must never be
        # attached to a page candidate; the constraint blocks a fresh row.
        destination.unlink(missing_ok=True)
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
    if candidate.deleted_at is not None:
        # A soft-deleted candidate must never take a paid call, no matter
        # which delete path landed after enqueueing. Raise the shell's
        # cancellation error so execute_job rolls back and stamps the job
        # CANCELLED; the deleted row is left untouched.
        raise JobCancelledError("候选已删除，任务取消，不再调用模型")
    page = db.get(MangaPage, candidate.page_id)
    if candidate.based_on_storyboard_version != page.storyboard_version:
        raise StaleStoryboardVersionError(
            "分镜版本已变化，已在调用模型前取消本次生成；请按当前分镜重新生成"
        )
    chapter = db.get(Chapter, page.chapter_id)
    if chapter is not None and chapter.deleted_at is not None:
        # delete_chapter is a soft delete with no active-job 409 and cancels
        # nothing, and it bumps only chapter.version — invisible to the
        # candidate and storyboard fences above. A deleted chapter must never
        # take a paid call; mirror the deleted-candidate guard.
        raise JobCancelledError("章节已删除，任务取消，不再调用模型")
    project = db.get(Project, chapter.project_id)
    if not page.scene_ids or not page.beat_ids:
        raise RuntimeError("页面缺少剧本与分镜来源，禁止生成")
    if not page.source_coverage.get("complete"):
        raise RuntimeError("页面原文覆盖不完整，禁止生成")

    reference_selections = candidate.prompt_snapshot.get("reference_selections", {})
    # The queue-time snapshot is the immutable input contract of this
    # candidate; later asset or variant edits must not rewrite it.
    queued_scene_snapshot = (candidate.prompt_snapshot or {}).get("scene_asset") or {}
    queued_scene_background = (
        queued_scene_snapshot.get("compiled_background")
        if queued_scene_snapshot.get("scene_asset_id") is not None
        else None
    )
    # Contract §8.3: the compiled snapshot below replaces the queue-time one, so
    # the frozen character package facts must be captured before that replace.
    queued_character_packages = dict(
        (candidate.prompt_snapshot or {}).get("character_packages") or {}
    )
    queued_lineage = copy.deepcopy((candidate.prompt_snapshot or {}).get("lineage"))
    for package_fact in queued_character_packages.values():
        # Contract §8.5-b: referenced version rows cannot be physically deleted
        # (server invariant), so any absence is corruption, not a recoverable state.
        if (
            package_fact.get("package_version_id")
            and db.get(CharacterModelPackageVersion, package_fact["package_version_id"]) is None
        ):
            raise RuntimeError("角色模型包版本已不存在，已在调用模型前停止任务")
    prompt, snapshot = compile_page_prompt(
        db,
        page,
        project,
        scene_background=queued_scene_background,
        character_package_facts=queued_character_packages or None,
    )
    reference_bindings: list[dict[str, str | None]] = []
    for character_id, selection in reference_selections.items():
        character = db.get(Character, character_id)
        package_fact = queued_character_packages.get(character_id) or {}
        outfit = db.get(Outfit, selection.get("outfit_id")) if selection.get("outfit_id") else None
        character_asset = db.get(Asset, selection.get("character_asset_id"))
        outfit_asset = (
            db.get(Asset, selection.get("outfit_asset_id"))
            if selection.get("outfit_asset_id")
            else None
        )
        reference_bindings.append(
            {
                "character": (
                    package_fact.get("primary_name")
                    or (character.primary_name if character else character_id)
                ),
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
    derived_page_job = job.job_type in {
        "PAGE_REPAIR",
        "PAGE_UPSCALE",
        "PAGE_REGION_REGENERATE",
    }
    # Derived jobs must not yank an adopted/final page back to draft. Fresh
    # PAGE_GENERATE only occupies DRAFT_GENERATING when the page is still in
    # a draft-like state; FINAL_* / selected pages stay put.
    if not derived_page_job:
        db.execute(
            update(MangaPage)
            .where(
                MangaPage.id == page.id,
                MangaPage.status.in_(
                    {
                        PageStatus.PLANNED,
                        PageStatus.STORYBOARDED,
                        PageStatus.DRAFT_READY,
                        PageStatus.DRAFT_GENERATING,
                    }
                ),
            )
            .values(status=PageStatus.DRAFT_GENERATING)
            .execution_options(synchronize_session=False)
        )
        db.refresh(page, attribute_names=["status", "selected_candidate_id", "version"])
    execution._commit_owned_progress(
        db, job, status=JobStatus.UPLOADING_REFERENCES, progress=20
    )

    reference_assets = _load_reference_assets(
        db,
        page,
        project,
        reference_selections,
        queued_scene_snapshot.get("reference_asset_ids") or [],
        queued_character_packages=queued_character_packages or None,
    )
    reference_bytes: list[bytes] = []
    reference_types: list[str] = []
    for asset in reference_assets:
        # #210-5: a vanished blob must terminate here (non-retryable) instead
        # of failing the paid dispatch as retryable WORKER_ERROR.
        reference_bytes.append(_asset_blob_bytes(asset))
        reference_types.append(asset.mime_type)

    reference_asset_ids = [asset.id for asset in reference_assets]
    if job.job_type in {"PAGE_REPAIR", "PAGE_UPSCALE", "PAGE_REGION_REGENERATE"}:
        original = db.get(PageCandidate, job.request_parameters.get("original_candidate_id"))
        if not original or not original.asset_id:
            raise RuntimeError("修复或升清任务缺少原始候选图")
        if original.deleted_at is not None:
            raise JobCancelledError("原始候选已被删除，模型返回结果不再写入")
        original_asset = db.get(Asset, original.asset_id)
        if original_asset is None or original_asset.deleted_at is not None:
            raise JobCancelledError("原始候选素材已被删除，模型返回结果不再写入")
        reference_bytes.insert(0, _asset_blob_bytes(original_asset))
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
            # #244-1: the raw json.dumps used to append inspection.details and
            # repair.target_regions UNBOUNDED after the compiled prompt had
            # already been squeezed into PROMPT_CHAR_BUDGET — a hostile or
            # degenerate verdict blob reopened the budget through this side
            # door. Bound each context block with the compiler's own
            # per-block budget (deterministic hard cuts, #160 style).
            bounded_repair_context = _bound_structured_block(
                repair_context, STRUCTURED_BLOCK_MAX_CHARS
            )
            prompt += (
                "\n这是局部修复任务。严格根据以下检查结果修复指定范围："
                f"{json.dumps(bounded_repair_context, ensure_ascii=False, separators=(',', ':'))}。"
                "不得改动范围外的人物身份、服装、背景、格线、文字、镜头与构图；"
                "修复后仍输出完整页面。"
            )
        elif job.job_type == "PAGE_REGION_REGENERATE":
            # V02-42B red line: a region job without its server-side mask
            # asset fails before the paid call instead of degrading to a
            # whole-page image-to-image edit.
            mask_asset_id = job.request_parameters.get("mask_asset_id")
            target_regions = job.request_parameters.get("target_regions") or []
            mask_asset = db.get(Asset, mask_asset_id) if mask_asset_id else None
            if mask_asset is None or mask_asset.deleted_at is not None or not target_regions:
                raise RuntimeError("局部重抽卡任务缺少 mask 资产，已在调用模型前停止任务")
            region_context = {
                "instruction": job.request_parameters.get("instruction") or "",
                "mask_asset_id": mask_asset.id,
                "target_regions": target_regions,
            }
            # #244-1: same budget bound as the repair context — the caller's
            # target_regions are request parameters, not compiler output, and
            # must not append unbounded either.
            bounded_region_context = _bound_structured_block(
                region_context, STRUCTURED_BLOCK_MAX_CHARS
            )
            prompt += (
                "\n这是局部重抽卡任务。原始页是第一张参考图；只允许重绘以下 mask "
                "区域内的内容："
                f"{json.dumps(bounded_region_context, ensure_ascii=False, separators=(',', ':'))}。"
                "mask 区域外的人物身份、服装、背景、格线、文字、镜头与构图必须与原图"
                "保持一致；仍输出完整页面。"
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
    snapshot["scene_asset"] = queued_scene_snapshot
    if queued_character_packages:
        # Merged from the preserved queue-time capture: the frozen facts stay on
        # the candidate even after the compiled snapshot replaces the input.
        snapshot["character_packages"] = queued_character_packages
    if queued_lineage:
        # UI and accept-idempotency hang derived candidates off
        # prompt_snapshot.lineage.source_command_id; dropping it on compile
        # makes the local-edit workspace lose the in-flight child.
        snapshot["lineage"] = queued_lineage
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
    if job.job_type == "PAGE_REGION_REGENERATE" and not model_supports_explicit_mask(
        binding.resolved.model
    ):
        # V02-44B defense in depth: a region job whose model lost (or never
        # had) the explicit-mask capability fails closed before the paid call
        # — no attempt, no artifact, never a silent whole-page degrade and
        # never a fallback onto another model/provider.
        raise ProviderAdapterError(
            "UNSUPPORTED_CAPABILITY",
            "所选模型不具备显式 mask 局部编辑能力"
            f"（当前目录声明：{REGION_EDIT_SURFACE_LABELS[model_region_edit_surface(binding.resolved.model)]}），"
            "已在调用模型前停止局部重抽卡",
        )
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
    execution._ensure_candidate_live(db, candidate)
    # An edit or select-candidate may land while the paid request is in flight.
    # Refresh status/selection before any page-row write so we cannot clobber
    # FINAL_* with a stale DRAFT_GENERATING identity map.
    db.refresh(
        page,
        attribute_names=["storyboard_version", "status", "selected_candidate_id", "version"],
    )
    if len(response.images) > 1:
        LOGGER.warning(
            "模型返回 %d 张图片，仅持久化第 1 张（其余图片不落盘，用量按供应商返回如实记录）",
            len(response.images),
        )
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
            "scene_asset": snapshot.get("scene_asset") or {},
            **(
                {
                    "character_packages": {
                        character_id: {
                            "package_id": fact.get("package_id"),
                            "package_version_id": fact.get("package_version_id"),
                            "version_number": fact.get("version_number"),
                            "spec_fingerprint": fact.get("spec_fingerprint"),
                        }
                        for character_id, fact in queued_character_packages.items()
                    }
                }
                if queued_character_packages
                else {}
            ),
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
    # Re-acquire the JOB row lock before the page fence: the mid-handler
    # progress commits (_commit_owned_progress / _lease_reference_assets)
    # released the FOR UPDATE taken in execute_job, and this final unit would
    # otherwise run PAGE→JOB — opposite to every cancel/failure/restore path
    # (job claim, then page restore lock), an AB-BA deadlock on PostgreSQL.
    lock_entity(db, GenerationJob, job.id)
    # Page row lock for the DRAFT_READY fence bump: serializes against
    # route-side storyboard edits so the version increment cannot be lost
    # to a concurrent read-modify-write on the same row.
    page = lock_entity(db, MangaPage, page.id)
    if page.status == PageStatus.DRAFT_GENERATING and not page.selected_candidate_id:
        page.status = PageStatus.DRAFT_READY
        page.version += 1
    provider.stage_attempt_output(
        db,
        asset,
        quality=candidate.resolution.value,
    )
