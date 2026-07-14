import hashlib
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from PIL import Image, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.helpers import asset_read
from app.config import get_settings
from app.database import get_db
from app.models import Asset, Project
from app.schemas import AssetRead

router = APIRouter()
CHUNK_SIZE = 1024 * 1024


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

        asset = Asset(
            id=asset_id,
            project_id=project_id,
            kind=kind,
            original_name=safe_name,
            storage_key=destination.relative_to(settings.upload_root).as_posix(),
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
        raise
    except (OSError, SQLAlchemyError) as error:
        db.rollback()
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="文件保存失败") from error
    finally:
        file.file.close()


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
