"""ASSET_GENERATE handler.

Owns character/outfit/style asset generation: reference resolution per target
type, prompt snapshot, the paid image call and candidate/asset persistence.
Cancellation and lease checks stay owned by the execution shell.
"""

import hashlib
import json
import logging

from PIL import Image
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.domain.states import JobStatus
from app.model_adapters.base import ImageRequest, ProviderAdapterError
from app.models import (
    Asset,
    AssetCandidate,
    Character,
    CharacterReference,
    GenerationBatch,
    GenerationJob,
    GenerationRecord,
    Outfit,
    StyleProfile,
    utcnow,
)
from app.services.media import create_thumbnails, remove_thumbnails
from app.services.model_router import model_supports_resolution
from app.services.worker_handlers import execution, provider
from app.services.worker_handlers.execution import JobCancelledError

LOGGER = logging.getLogger("mangaflow.worker.asset_generate")


def _save_asset_candidate(db, candidate: AssetCandidate, project_id: str, data: bytes) -> Asset:
    settings = get_settings()
    batch = db.get(GenerationBatch, candidate.batch_id)
    kind = batch.generation_kind.lower()
    digest = hashlib.sha256(data).hexdigest()
    # Dedupe must only consider live AI-generated rows of this batch's kind.
    # Asset holds a hard UNIQUE(project_id, sha256), so an unfiltered match can
    # hand a new paid candidate a soft-deleted asset (content 404s) or a
    # byte-identical user upload / another generation kind's row.
    live_dedupe_filters = (
        Asset.project_id == project_id,
        Asset.sha256 == digest,
        Asset.deleted_at.is_(None),
        Asset.source == "AI_GENERATED",
        Asset.kind == kind,
    )
    existing = db.scalar(select(Asset).where(*live_dedupe_filters))
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
    try:
        with db.begin_nested():
            asset = Asset(
                project_id=project_id,
                kind=kind,
                original_name=(
                    f"{kind}-{candidate.variant.lower()}-"
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
        # matching row appeared concurrently, or a soft-deleted generated row
        # of this kind holds the digest (asset deletes unlink no files). Flush
        # so the re-queries below observe every row this transaction can see.
        db.flush()
        existing = db.scalar(select(Asset).where(*live_dedupe_filters))
        if existing:
            destination.unlink(missing_ok=True)
            return existing
        deleted = db.scalar(
            select(Asset).where(
                Asset.project_id == project_id,
                Asset.sha256 == digest,
                Asset.source == "AI_GENERATED",
                Asset.kind == kind,
            )
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
                f"{kind}-{candidate.variant.lower()}-{candidate.ordinal}.png"
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
        # attached to this candidate; the constraint blocks a fresh row.
        destination.unlink(missing_ok=True)
        raise
    except Exception:
        destination.unlink(missing_ok=True)
        if "asset" in locals() and asset.id:
            remove_thumbnails(settings.storage_root, asset.id)
        raise


def _run_asset_generate(db, job: GenerationJob) -> None:
    candidate = db.get(AssetCandidate, job.target_id)
    if not candidate:
        raise RuntimeError("资产候选不存在")
    if candidate.deleted_at is not None:
        # A soft-deleted candidate must never take a paid call, no matter
        # which delete path landed after enqueueing. Raise the shell's
        # cancellation error so execute_job rolls back and stamps the job
        # CANCELLED; the deleted row is left untouched.
        raise JobCancelledError("候选已删除，任务取消，不再调用模型")
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
    execution._commit_owned_progress(db, job, status=JobStatus.GENERATING, progress=45)
    reference_ids = [asset.id for asset in references]
    provider._lease_reference_assets(db, job, reference_ids)
    for asset in references:
        if not provider._asset_path(asset).is_file():
            raise RuntimeError(f"参考图文件不存在：{asset.original_name}")
    reference_bytes = [provider._asset_path(asset).read_bytes() for asset in references]
    reference_types = [asset.mime_type for asset in references]
    binding = provider._binding(
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
    provider._validate_reference_capacity(binding, len(reference_bytes))
    db.commit()
    response = provider._invoke_provider(
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
    execution._ensure_job_not_cancelled(db, job)
    # A delete can land while the paid request is in flight. The call is
    # already spent (its ModelCallAttempt/usage rows committed autonomously),
    # but nothing may be attached to the deleted row: abort before any
    # persistence so the worker shell rolls the attach back.
    db.refresh(candidate, attribute_names=["deleted_at"])
    if candidate.deleted_at is not None:
        LOGGER.warning(
            "资产候选 %s 在模型调用完成后被删除，丢弃生成结果并取消任务 %s",
            candidate.id,
            job.id,
        )
        raise JobCancelledError("候选已删除，模型返回结果不再写入")
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
    provider.stage_attempt_output(
        db,
        asset,
        quality=candidate.resolution.value,
    )
    if batch.target_type == "STYLE" and candidate.variant == "STYLE_TEST":
        style = db.get(StyleProfile, batch.target_id)
        if style:
            style.status = "TEST_GENERATED"
            style.version += 1
