import hashlib
import json
import os
import zipfile
from pathlib import Path
from uuid import uuid4

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
    GenerationRecord,
    MangaPage,
    PageCandidate,
    Project,
)
from app.schemas import ExportRead, ExportRequest
from app.services.page_completion import (
    build_page_production_readiness,
    production_error_detail,
)

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
        production = build_page_production_readiness(db, page)
        if not production.ready:
            detail = production_error_detail(production)
            detail["message"] = f"第 {page.page_number} 页尚未达到生产通过状态"
            detail["page_number"] = page.page_number
            raise HTTPException(status_code=409, detail=detail)
        candidate = db.get(PageCandidate, page.selected_candidate_id)
        asset = db.get(Asset, candidate.asset_id) if candidate and candidate.asset_id else None
        if not asset:
            raise HTTPException(status_code=409, detail=f"第 {page.page_number} 页采用素材不存在")
        result.append((page, candidate, asset))
    return result


@router.get("/pages/{page_id}/export.png")
def download_selected_page(page_id: str, db: Session = Depends(get_db)) -> FileResponse:
    page = db.get(MangaPage, page_id)
    if not page:
        raise HTTPException(status_code=404, detail="页面不存在")
    production = build_page_production_readiness(db, page)
    if not production.ready:
        raise HTTPException(status_code=409, detail=production_error_detail(production))
    candidate = db.get(PageCandidate, page.selected_candidate_id)
    asset = db.get(Asset, candidate.asset_id) if candidate and candidate.asset_id else None
    if not asset:
        raise HTTPException(status_code=409, detail="页面采用素材不存在")
    path = _asset_path(asset)
    return FileResponse(
        path,
        media_type=asset.mime_type or "image/png",
        filename=f"page-{page.page_number:04d}.png",
    )


def _write_export_atomically(destination: Path, write):
    """Write through a unique temp file and rename into place.

    The destination name is deterministic (candidate-set hash), so two
    concurrent exports of the same chapter would otherwise interleave
    truncate-mode writes and leave a permanently corrupt artifact whose
    recorded sha256 never matches any complete output.
    """

    temp = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        write(temp)
        os.replace(temp, destination)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def _hash_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


@router.post(
    "/chapters/{chapter_id}/exports",
    response_model=ExportRead,
    status_code=status.HTTP_201_CREATED,
)
def create_export(
    chapter_id: str,
    payload: ExportRequest,
    db: Session = Depends(get_db),
    reuse_existing: bool = False,
) -> ExportBundle:
    """Export a chapter's selected pages into a bundle artifact.

    ``reuse_existing`` is a worker-side idempotency switch (default off, so
    direct HTTP callers keep the historical one-row-per-POST behavior). The
    artifact path is deterministic given the selected candidate set, so an
    existing row with the same (chapter, export_type, storage_key) proves this
    exact artifact was already committed; it is returned as-is without
    rewriting the file or inserting a duplicate row. The workflow export node
    passes True because lease-expiry reclaim and RQ redelivery re-execute the
    node after a previous attempt already committed the bundle row but before
    the job completion CAS. The check-then-insert is not atomic: truly
    concurrent double-execution would still need a DB unique constraint on
    (chapter_id, export_type, storage_key), which requires a migration.
    """
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
    # The artifact path is deterministic given the selected candidate set, so
    # it doubles as the idempotency key for worker-side re-execution.
    destination = output_dir / {
        "PNG": f"{token}-pages.zip",
        "PDF": f"{token}-chapter.pdf",
    }.get(payload.export_type, f"{token}-project.json")

    if reuse_existing:
        storage_key = destination.relative_to(settings.storage_root).as_posix()
        existing = db.scalar(
            select(ExportBundle)
            .where(
                ExportBundle.chapter_id == chapter.id,
                ExportBundle.export_type == payload.export_type,
                ExportBundle.storage_key == storage_key,
            )
            .order_by(ExportBundle.created_at.desc(), ExportBundle.id.desc())
            .limit(1)
        )
        if existing is not None:
            # The committed row is the idempotency marker: it is only written
            # after the artifact has been renamed into place, so the file it
            # points at is complete.
            return existing

    if payload.export_type == "PNG":

        def _write_zip(temp: Path) -> None:
            with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as archive:
                for page, _, asset in selected:
                    archive.write(
                        _asset_path(asset),
                        arcname=f"{page.page_number:04d}-{asset.original_name}",
                    )

        _write_export_atomically(destination, _write_zip)
    elif payload.export_type == "PDF":
        # Lazy opens: Pillow encodes frames one at a time, so a 4K long
        # chapter no longer decodes every page's bitmap into memory at once.
        images = [Image.open(_asset_path(asset)) for _, _, asset in selected]
        try:
            first = images[0]
            _write_export_atomically(
                destination,
                # format= is explicit because the temp filename's suffix is
                # ".tmp", from which Pillow cannot infer the encoder.
                lambda temp: first.save(
                    temp, format="PDF", save_all=True, append_images=images[1:]
                ),
            )
        finally:
            for image in images:
                image.close()
    else:
        manifest: dict[str, dict] = {}
        for _, candidate, asset in selected:
            related_ids = [asset.id]
            if candidate.generation_record_id:
                record = db.get(GenerationRecord, candidate.generation_record_id)
                if record:
                    related_ids.extend(record.reference_asset_ids)
            for asset_id in related_ids:
                related = db.get(Asset, asset_id)
                if related:
                    manifest[related.id] = {
                        "id": related.id,
                        "kind": related.kind,
                        "original_name": related.original_name,
                        "mime_type": related.mime_type,
                        "byte_size": related.byte_size,
                        "sha256": related.sha256,
                        "source": related.source,
                    }
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
            "asset_manifest": list(manifest.values()),
        }
        payload_text = json.dumps(document, ensure_ascii=False, indent=2)

        def _write_json(temp: Path) -> None:
            temp.write_text(payload_text, encoding="utf-8")

        _write_export_atomically(destination, _write_json)

    byte_size, digest = _hash_file(destination)
    bundle = ExportBundle(
        project_id=project.id,
        chapter_id=chapter.id,
        export_type=payload.export_type,
        storage_key=destination.relative_to(settings.storage_root).as_posix(),
        byte_size=byte_size,
        sha256=digest,
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
