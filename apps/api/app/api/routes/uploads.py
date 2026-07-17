import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from PIL import Image, UnidentifiedImageError
from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.helpers import asset_read
from app.config import get_settings
from app.database import get_db
from app.models import Asset, CharacterReference, Project
from app.schemas import AssetRead, AssetUpdate
from app.services.media import create_thumbnails, remove_thumbnails

router = APIRouter()
CHUNK_SIZE = 1024 * 1024
ASSET_KINDS = {
    "character": "CHARACTER_REFERENCE",
    "outfit": "OUTFIT_REFERENCE",
    "style": "STYLE_REFERENCE",
    "CHARACTER_REFERENCE": "CHARACTER_REFERENCE",
    "OUTFIT_REFERENCE": "OUTFIT_REFERENCE",
    "STYLE_REFERENCE": "STYLE_REFERENCE",
}


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


@router.post("/upload", response_model=AssetRead, status_code=status.HTTP_201_CREATED)
def upload_asset(
    project_id: str = Form(),
    kind: str = Form(),
    file: UploadFile = File(),
    db: Session = Depends(get_db),
) -> AssetRead:
    settings = get_settings()
    normalized_kind = ASSET_KINDS.get(kind)
    if not normalized_kind:
        raise HTTPException(status_code=422, detail="请选择人物、服装或漫画风格参考用途")
    project = db.get(Project, project_id)
    if not project or project.deleted_at is not None:
        raise HTTPException(status_code=404, detail="项目不存在")
    if file.content_type not in settings.allowed_upload_types:
        raise HTTPException(status_code=415, detail="不支持的文件类型")

    safe_name = Path(file.filename or "upload").name
    suffix = Path(safe_name).suffix.lower()
    asset_id = str(uuid4())
    project_dir = settings.upload_root / project_id
    project_dir.mkdir(parents=True, exist_ok=True)
    destination = project_dir / f"{asset_id}{suffix}"

    digest = hashlib.sha256()
    byte_size = 0
    try:
        with destination.open("wb") as output:
            while chunk := file.file.read(CHUNK_SIZE):
                byte_size += len(chunk)
                if byte_size > settings.max_upload_bytes:
                    raise HTTPException(status_code=413, detail="文件超过 20 MB 上限")
                digest.update(chunk)
                output.write(chunk)

        existing = db.scalar(
            select(Asset).where(
                Asset.project_id == project_id,
                Asset.sha256 == digest.hexdigest(),
            )
        )
        if existing:
            destination.unlink(missing_ok=True)
            return asset_read(existing)

        width = height = None
        if file.content_type.startswith("image/"):
            try:
                with Image.open(destination) as image:
                    image.verify()
                with Image.open(destination) as image:
                    width, height = image.size
            except (UnidentifiedImageError, OSError) as error:
                raise HTTPException(status_code=422, detail="图片文件损坏或格式不符") from error

        thumbnails = create_thumbnails(destination, settings.upload_root, asset_id)
        asset = Asset(
            id=asset_id,
            project_id=project_id,
            kind=normalized_kind,
            original_name=safe_name,
            storage_key=destination.relative_to(settings.upload_root).as_posix(),
            thumbnail_320_key=thumbnails[320],
            thumbnail_640_key=thumbnails[640],
            mime_type=file.content_type,
            byte_size=byte_size,
            sha256=digest.hexdigest(),
            width=width,
            height=height,
        )
        db.add(asset)
        db.commit()
        db.refresh(asset)
        return asset_read(asset)
    except HTTPException:
        destination.unlink(missing_ok=True)
        remove_thumbnails(settings.upload_root, asset_id)
        raise
    except (OSError, SQLAlchemyError) as error:
        db.rollback()
        destination.unlink(missing_ok=True)
        remove_thumbnails(settings.upload_root, asset_id)
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
        asset.kind = payload.kind
        if payload.kind != "CHARACTER_REFERENCE":
            db.execute(delete(CharacterReference).where(CharacterReference.asset_id == asset.id))
    if "display_name" in payload.model_fields_set:
        asset.display_name = payload.display_name
    asset.version += 1
    db.commit()
    db.refresh(asset)
    return asset_read(asset)


@router.delete("/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_asset(asset_id: str, db: Session = Depends(get_db)) -> None:
    asset = db.get(Asset, asset_id)
    if not asset or asset.deleted_at is not None:
        raise HTTPException(status_code=404, detail="素材不存在")
    if asset.source != "USER_UPLOAD":
        raise HTTPException(status_code=409, detail="生成结果请在对应批次中删除")
    db.execute(delete(CharacterReference).where(CharacterReference.asset_id == asset.id))
    asset.deleted_at = datetime.now(UTC)
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
