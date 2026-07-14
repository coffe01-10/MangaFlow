import hashlib
import json
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import (
    Asset,
    Chapter,
    ExportBundle,
    MangaPage,
    PageCandidate,
    Project,
)
from app.schemas import ExportRead, ExportRequest

router = APIRouter()


def _asset_path(asset: Asset) -> Path:
    settings = get_settings()
    root = settings.upload_root if asset.source == "USER_UPLOAD" else settings.storage_root
    path = (root / asset.storage_key).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise HTTPException(status_code=409, detail="采用的页面素材文件不存在")
    return path


def _selected_pages(db: Session, chapter: Chapter):
    pages = list(
        db.scalars(
            select(MangaPage)
            .where(MangaPage.chapter_id == chapter.id)
            .order_by(MangaPage.page_number)
        )
    )
    if not pages:
        raise HTTPException(status_code=409, detail="章节还没有页面规划")
    result = []
    for page in pages:
        if not page.selected_candidate_id:
            raise HTTPException(status_code=409, detail=f"第 {page.page_number} 页尚未采用候选")
        candidate = db.get(PageCandidate, page.selected_candidate_id)
        asset = db.get(Asset, candidate.asset_id) if candidate and candidate.asset_id else None
        if not asset:
            raise HTTPException(status_code=409, detail=f"第 {page.page_number} 页采用素材不存在")
        result.append((page, candidate, asset))
    return result


@router.post(
    "/chapters/{chapter_id}/exports",
    response_model=ExportRead,
    status_code=status.HTTP_201_CREATED,
)
def create_export(
    chapter_id: str,
    payload: ExportRequest,
    db: Session = Depends(get_db),
) -> ExportBundle:
    chapter = db.get(Chapter, chapter_id)
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")
    project = db.get(Project, chapter.project_id)
    selected = _selected_pages(db, chapter)
    settings = get_settings()
    output_dir = settings.storage_root / "exports" / project.id / chapter.id
    output_dir.mkdir(parents=True, exist_ok=True)
    token = hashlib.sha256(
        "|".join(candidate.id for _, candidate, _ in selected).encode("utf-8")
    ).hexdigest()[:12]

    if payload.export_type == "PNG":
        destination = output_dir / f"{token}-pages.zip"
        with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
            for page, _, asset in selected:
                archive.write(
                    _asset_path(asset),
                    arcname=f"{page.page_number:04d}-{asset.original_name}",
                )
    elif payload.export_type == "PDF":
        destination = output_dir / f"{token}-chapter.pdf"
        images = [Image.open(_asset_path(asset)).convert("RGB") for _, _, asset in selected]
        try:
            images[0].save(destination, save_all=True, append_images=images[1:])
        finally:
            for image in images:
                image.close()
    else:
        destination = output_dir / f"{token}-project.json"
        document = {
            "schema_version": "1.0",
            "project": {"id": project.id, "name": project.name},
            "chapter": {"id": chapter.id, "title": chapter.title},
            "pages": [
                {
                    "id": page.id,
                    "page_number": page.page_number,
                    "source_coverage": page.source_coverage,
                    "selected_candidate": {
                        "id": candidate.id,
                        "model_alias": candidate.model_alias,
                        "resolution": candidate.resolution.value,
                        "asset_id": asset.id,
                    },
                }
                for page, candidate, asset in selected
            ],
        }
        destination.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")

    data = destination.read_bytes()
    bundle = ExportBundle(
        project_id=project.id,
        chapter_id=chapter.id,
        export_type=payload.export_type,
        storage_key=destination.relative_to(settings.storage_root).as_posix(),
        byte_size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        page_count=len(selected),
    )
    db.add(bundle)
    db.commit()
    db.refresh(bundle)
    return bundle


@router.get("/projects/{project_id}/exports", response_model=list[ExportRead])
def list_exports(project_id: str, db: Session = Depends(get_db)) -> list[ExportBundle]:
    return list(
        db.scalars(
            select(ExportBundle)
            .where(ExportBundle.project_id == project_id)
            .order_by(ExportBundle.created_at.desc())
        )
    )


@router.get("/exports/{export_id}/download")
def download_export(export_id: str, db: Session = Depends(get_db)) -> FileResponse:
    bundle = db.get(ExportBundle, export_id)
    if not bundle:
        raise HTTPException(status_code=404, detail="导出记录不存在")
    root = get_settings().storage_root.resolve()
    path = (root / bundle.storage_key).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(status_code=404, detail="导出文件不存在")
    media_types = {
        "PNG": "application/zip",
        "PDF": "application/pdf",
        "JSON": "application/json",
    }
    return FileResponse(path, media_type=media_types[bundle.export_type], filename=path.name)
