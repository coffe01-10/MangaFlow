import hashlib
from collections.abc import AsyncIterator
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.helpers import asset_read
from app.config import get_settings
from app.database import get_db
from app.models import (
    Asset,
    AssetCandidate,
    AssetStatus,
    CharacterReference,
    GenerationJob,
    JobAssetReference,
    MangaPage,
    Outfit,
    PageCandidate,
    Project,
    SceneAsset,
    SceneAssetReference,
    SceneAssetVariant,
    SceneAssetVariantReference,
    StyleProfile,
    StyleStatus,
)
from app.request_limits import ASSET_UPLOAD_OPENAPI, ParsedUpload, parse_single_file_form
from app.schemas import AssetRead, AssetUpdate
from app.services.character_packages import detach_draft_package_references_for_asset
from app.services.media import (
    create_thumbnails,
    inspect_upload_image,
    remove_thumbnails,
    sanitize_stored_filename,
)
from app.services.ordinal_allocator import lock_entity

router = APIRouter()
CHUNK_SIZE = 1024 * 1024
REFERENCE_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp"}
ASSET_KINDS = {
    "character": "CHARACTER_REFERENCE",
    "outfit": "OUTFIT_REFERENCE",
    "style": "STYLE_REFERENCE",
    "scene": "SCENE_REFERENCE",
    "CHARACTER_REFERENCE": "CHARACTER_REFERENCE",
    "OUTFIT_REFERENCE": "OUTFIT_REFERENCE",
    "STYLE_REFERENCE": "STYLE_REFERENCE",
    "SCENE_REFERENCE": "SCENE_REFERENCE",
}
ACTIVE_REFERENCE_STATUSES = {
    "WAITING",
    "QUEUED",
    "PREPARING",
    "UPLOADING_REFERENCES",
    "GENERATING",
    "OCR_CHECKING",
    "CONSISTENCY_CHECKING",
    "REPAIRING",
}


def _ensure_asset_not_in_active_job(db: Session, asset: Asset) -> None:
    active_job_id = db.scalar(
        select(GenerationJob.id)
        .join(JobAssetReference, JobAssetReference.job_id == GenerationJob.id)
        .where(
            JobAssetReference.asset_id == asset.id,
            GenerationJob.status.in_(ACTIVE_REFERENCE_STATUSES),
        )
        .limit(1)
    )
    if active_job_id:
        raise HTTPException(
            status_code=409,
            detail="素材正被排队或执行中的生成任务使用，请先取消任务后再修改",
        )


def _detach_reference_asset(db: Session, asset: Asset) -> None:
    """Remove a reference asset from every structured binding in its project."""

    # Contract §10.3: DRAFT package relation rows are physically cleared with the
    # asset; READY+ rows keep the frozen fact and consumers filter by deleted_at.
    detach_draft_package_references_for_asset(db, asset.id)
    db.execute(delete(CharacterReference).where(CharacterReference.asset_id == asset.id))
    for outfit in db.scalars(select(Outfit).where(Outfit.project_id == asset.project_id)):
        if asset.id not in outfit.reference_asset_ids:
            continue
        outfit.reference_asset_ids = [
            asset_id for asset_id in outfit.reference_asset_ids if asset_id != asset.id
        ]
        outfit.status = AssetStatus.NEEDS_CONFIRMATION
        outfit.version += 1
    styles = db.scalars(
        select(StyleProfile).where(StyleProfile.project_id == asset.project_id)
    )
    for style in styles:
        profile = dict(style.profile)
        reference_ids = list(profile.get("reference_asset_ids", []))
        if asset.id not in reference_ids:
            continue
        profile["reference_asset_ids"] = [
            asset_id for asset_id in reference_ids if asset_id != asset.id
        ]
        profile["palette_confirmed"] = False
        profile["test_image_approved"] = False
        profile.pop("test_candidate_id", None)
        style.profile = profile
        style.status = StyleStatus.DRAFT
        style.version += 1
    affected_scene_asset_ids = set(
        db.scalars(
            select(SceneAssetReference.scene_asset_id).where(
                SceneAssetReference.asset_id == asset.id
            )
        )
    )
    affected_scene_asset_ids |= set(
        db.scalars(
            select(SceneAssetVariant.scene_asset_id)
            .join(
                SceneAssetVariantReference,
                SceneAssetVariantReference.variant_id == SceneAssetVariant.id,
            )
            .where(SceneAssetVariantReference.asset_id == asset.id)
        )
    )
    db.execute(
        delete(SceneAssetReference).where(SceneAssetReference.asset_id == asset.id)
    )
    db.execute(
        delete(SceneAssetVariantReference).where(
            SceneAssetVariantReference.asset_id == asset.id
        )
    )
    for scene_asset_id in affected_scene_asset_ids:
        scene_asset = db.get(SceneAsset, scene_asset_id)
        if not scene_asset:
            continue
        scene_asset.status = AssetStatus.NEEDS_CONFIRMATION
        scene_asset.version += 1


@router.get("", response_model=list[AssetRead])
def list_assets(project_id: str, db: Session = Depends(get_db)) -> list[AssetRead]:
    project = db.get(Project, project_id)
    if not project or project.deleted_at is not None:
        raise HTTPException(status_code=404, detail="项目不存在")
    assets = list(
        db.scalars(
            select(Asset)
            .where(Asset.project_id == project_id, Asset.deleted_at.is_(None))
            .order_by(Asset.created_at.desc())
        )
    )
    return [asset_read(asset) for asset in assets]


async def _parse_asset_upload(request: Request) -> AsyncIterator[ParsedUpload]:
    parsed = await parse_single_file_form(request, required_fields=("project_id", "kind"))
    try:
        yield parsed
    finally:
        await parsed.file.close()


@router.post(
    "/upload",
    response_model=AssetRead,
    status_code=status.HTTP_201_CREATED,
    openapi_extra=ASSET_UPLOAD_OPENAPI,
)
def upload_asset(
    parsed: ParsedUpload = Depends(_parse_asset_upload),
    db: Session = Depends(get_db),
) -> AssetRead:
    settings = get_settings()
    project_id = parsed.texts["project_id"]
    kind = parsed.texts["kind"]
    file = parsed.file
    normalized_kind = ASSET_KINDS.get(kind)
    if not normalized_kind:
        raise HTTPException(status_code=422, detail="请选择人物、服装、漫画风格或场景参考用途")
    project = db.get(Project, project_id)
    if not project or project.deleted_at is not None:
        raise HTTPException(status_code=404, detail="项目不存在")
    if (
        file.content_type not in settings.allowed_upload_types
        or file.content_type not in REFERENCE_IMAGE_TYPES
    ):
        raise HTTPException(status_code=415, detail="不支持的文件类型")

    safe_name = sanitize_stored_filename(file.filename or "upload")
    suffix = Path(safe_name).suffix.lower()
    asset_id = str(uuid4())
    project_dir = settings.upload_root / project_id
    project_dir.mkdir(parents=True, exist_ok=True)
    destination = project_dir / f"{asset_id}{suffix}"
    thumbnail_asset_id = asset_id

    digest = hashlib.sha256()
    byte_size = 0
    try:
        with destination.open("wb") as output:
            while chunk := file.file.read(CHUNK_SIZE):
                byte_size += len(chunk)
                if byte_size > settings.max_upload_bytes:
                    raise HTTPException(status_code=413, detail="文件超过上传上限")
                digest.update(chunk)
                output.write(chunk)

        try:
            width, height, mime_type, detected_suffix = inspect_upload_image(
                destination,
                max_pixels=settings.max_image_pixels,
                max_side=settings.max_image_side,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        if detected_suffix != suffix:
            renamed = destination.with_suffix(detected_suffix)
            destination.rename(renamed)
            destination = renamed
            suffix = detected_suffix

        existing = db.scalar(
            select(Asset).where(
                Asset.project_id == project_id,
                Asset.sha256 == digest.hexdigest(),
            )
        )
        if existing:
            old_path = (settings.upload_root / existing.storage_key).resolve()
            safe_old_path = old_path.is_relative_to(settings.upload_root.resolve())
            if existing.deleted_at is None and safe_old_path and old_path.is_file():
                destination.unlink(missing_ok=True)
                return asset_read(existing)
            if existing.source != "USER_UPLOAD":
                raise HTTPException(status_code=409, detail="同内容的生成素材已存在")

            remove_thumbnails(settings.upload_root, existing.id)
            thumbnail_asset_id = existing.id
            thumbnails = create_thumbnails(destination, settings.upload_root, existing.id)
            existing.kind = normalized_kind
            existing.original_name = safe_name
            existing.storage_key = destination.relative_to(settings.upload_root).as_posix()
            existing.thumbnail_320_key = thumbnails[320]
            existing.thumbnail_640_key = thumbnails[640]
            existing.mime_type = mime_type
            existing.byte_size = byte_size
            existing.width = width
            existing.height = height
            existing.status = AssetStatus.UPLOADED
            existing.deleted_at = None
            existing.version += 1
            db.commit()
            db.refresh(existing)
            if safe_old_path and old_path != destination.resolve():
                # The database already points at the replacement. A locked stale
                # file must not make the restored asset unusable.
                with suppress(OSError):
                    old_path.unlink(missing_ok=True)
            return asset_read(existing)

        thumbnails = create_thumbnails(destination, settings.upload_root, asset_id)
        asset = Asset(
            id=asset_id,
            project_id=project_id,
            kind=normalized_kind,
            original_name=safe_name,
            storage_key=destination.relative_to(settings.upload_root).as_posix(),
            thumbnail_320_key=thumbnails[320],
            thumbnail_640_key=thumbnails[640],
            mime_type=mime_type,
            byte_size=byte_size,
            sha256=digest.hexdigest(),
            width=width,
            height=height,
        )
        db.add(asset)
        try:
            db.commit()
        except IntegrityError as error:
            # A concurrent upload of the same bytes won the
            # (project_id, sha256) unique slot; a routine duplicate must not
            # surface as 文件保存失败. Only that constraint is deduped — any
            # other integrity failure keeps its real cause.
            if "unique" not in str(error.orig).lower() or "sha256" not in str(
                error.orig
            ).lower():
                raise
            db.rollback()
            winner = db.scalar(
                select(Asset).where(
                    Asset.project_id == project_id,
                    Asset.sha256 == digest.hexdigest(),
                    Asset.deleted_at.is_(None),
                )
            )
            destination.unlink(missing_ok=True)
            remove_thumbnails(settings.upload_root, asset_id)
            if winner is not None:
                return asset_read(winner)
            raise HTTPException(status_code=409, detail="同内容素材已存在") from None
        db.refresh(asset)
        return asset_read(asset)
    except HTTPException:
        destination.unlink(missing_ok=True)
        remove_thumbnails(settings.upload_root, thumbnail_asset_id)
        raise
    except (OSError, SQLAlchemyError) as error:
        db.rollback()
        destination.unlink(missing_ok=True)
        remove_thumbnails(settings.upload_root, thumbnail_asset_id)
        raise HTTPException(status_code=500, detail="文件保存失败") from error
    finally:
        file.file.close()


@router.patch("/{asset_id}", response_model=AssetRead)
def update_asset(asset_id: str, payload: AssetUpdate, db: Session = Depends(get_db)) -> AssetRead:
    asset = db.get(Asset, asset_id)
    if not asset or asset.deleted_at is not None:
        raise HTTPException(status_code=404, detail="素材不存在")
    if payload.kind is not None:
        if asset.source != "USER_UPLOAD":
            raise HTTPException(status_code=409, detail="生成结果不能改成参考图")
        if payload.kind != asset.kind:
            _ensure_asset_not_in_active_job(db, asset)
            _detach_reference_asset(db, asset)
            asset.kind = payload.kind
    if "display_name" in payload.model_fields_set:
        asset.display_name = payload.display_name
    asset.version += 1
    db.commit()
    db.refresh(asset)
    return asset_read(asset)


@router.post("/{asset_id}/adopt-reference", response_model=AssetRead)
def adopt_generated_asset_as_reference(asset_id: str, db: Session = Depends(get_db)) -> AssetRead:
    """Make an AI-generated asset available for structured reference bindings."""

    asset = db.get(Asset, asset_id)
    if not asset or asset.deleted_at is not None:
        raise HTTPException(status_code=404, detail="素材不存在")
    if asset.source not in {"AI_GENERATED", "VERTEX_GENERATED"}:
        raise HTTPException(status_code=409, detail="只有生成素材可以导入为参考图")
    # Importing creates a new structured binding; it must not rewrite the source
    # asset's role because page candidates and generation history still own it.
    return asset_read(asset)


@router.delete("/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_asset(asset_id: str, db: Session = Depends(get_db)) -> None:
    asset = db.get(Asset, asset_id)
    if not asset or asset.deleted_at is not None:
        raise HTTPException(status_code=404, detail="素材不存在")
    _ensure_asset_not_in_active_job(db, asset)
    page_candidates = list(
        db.scalars(
            select(PageCandidate).where(
                PageCandidate.asset_id == asset.id,
                PageCandidate.deleted_at.is_(None),
            )
        )
    )
    # Agreed convention with select_candidate: both sides hold the page lock
    # before their selected-guard reads, so a concurrent adopt cannot slip
    # between this read and the soft delete below. The guard below runs on a
    # post-lock re-read (populate_existing refreshes stale identity-map rows).
    for page_id in sorted({candidate.page_id for candidate in page_candidates}):
        lock_entity(db, MangaPage, page_id)
    page_candidates = list(
        db.scalars(
            select(PageCandidate)
            .where(
                PageCandidate.asset_id == asset.id,
                PageCandidate.deleted_at.is_(None),
            )
            .execution_options(populate_existing=True)
        )
    )
    if any(candidate.is_selected for candidate in page_candidates):
        raise HTTPException(status_code=409, detail="当前采用的分页成图不能删除，请先改用其他候选")
    _detach_reference_asset(db, asset)
    deleted_at = datetime.now(UTC)
    for candidate in page_candidates:
        candidate.deleted_at = deleted_at
        candidate.version += 1
    for candidate in db.scalars(
        select(AssetCandidate).where(
            AssetCandidate.asset_id == asset.id,
            AssetCandidate.deleted_at.is_(None),
        )
    ):
        candidate.deleted_at = deleted_at
        candidate.version += 1
    asset.deleted_at = deleted_at
    db.commit()


@router.get("/{asset_id}/content")
def asset_content(asset_id: str, db: Session = Depends(get_db)) -> FileResponse:
    settings = get_settings()
    asset = db.get(Asset, asset_id)
    if not asset or asset.deleted_at is not None:
        raise HTTPException(status_code=404, detail="素材不存在")
    root = settings.upload_root if asset.source == "USER_UPLOAD" else settings.storage_root
    path = (root / asset.storage_key).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise HTTPException(status_code=404, detail="素材文件不存在")
    return FileResponse(path, media_type=asset.mime_type, filename=asset.original_name)


@router.get("/{asset_id}/thumbnail/{size}")
def asset_thumbnail(asset_id: str, size: int, db: Session = Depends(get_db)) -> FileResponse:
    if size not in {320, 640}:
        raise HTTPException(status_code=422, detail="缩略图尺寸只支持 320 或 640")
    settings = get_settings()
    asset = db.get(Asset, asset_id)
    if not asset or asset.deleted_at is not None:
        raise HTTPException(status_code=404, detail="素材不存在")
    root = settings.upload_root if asset.source == "USER_UPLOAD" else settings.storage_root
    key = asset.thumbnail_320_key if size == 320 else asset.thumbnail_640_key
    path = (root / key).resolve() if key else None
    if not path or not path.is_relative_to(root.resolve()) or not path.is_file():
        source = (root / asset.storage_key).resolve()
        if not source.is_relative_to(root.resolve()) or not source.is_file():
            raise HTTPException(status_code=404, detail="素材文件不存在")
        try:
            thumbnails = create_thumbnails(source, root, asset.id)
        except OSError as error:
            raise HTTPException(status_code=422, detail="无法生成素材缩略图") from error
        asset.thumbnail_320_key = thumbnails[320]
        asset.thumbnail_640_key = thumbnails[640]
        db.commit()
        path = (root / thumbnails[size]).resolve()
    return FileResponse(path, media_type="image/webp")
