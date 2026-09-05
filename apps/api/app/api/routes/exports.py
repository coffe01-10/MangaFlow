import hashlib
import json
import os
import zipfile
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from PIL import Image
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.helpers import ensure_project_scope
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
    utcnow,
)
from app.schemas import ExportRead, ExportRequest
from app.services.media import sanitize_stored_filename
from app.services.page_completion import (
    build_page_production_readiness,
    production_error_detail,
)

router = APIRouter()



def _safe_archive_name(original_name: str) -> str:
    r"""Neutralize a stored asset name before it becomes a zip member.

    Upload-side sanitizing only covers new uploads; legacy rows can still
    carry backslash separators that Windows extractors treat as paths,
    letting members extract outside the target directory.
    """

    return sanitize_stored_filename(original_name, default="page.png")


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
        asset = (
            db.get(Asset, candidate.asset_id)
            if candidate and candidate.asset_id and candidate.deleted_at is None
            else None
        )
        if not asset or asset.deleted_at is not None:
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
    asset = (
        db.get(Asset, candidate.asset_id)
        if candidate and candidate.asset_id and candidate.deleted_at is None
        else None
    )
    if not asset or asset.deleted_at is not None:
        raise HTTPException(status_code=409, detail="页面采用素材不存在")
    path = _asset_path(asset)
    return FileResponse(
        path,
        media_type=asset.mime_type or "image/png",
        filename=f"page-{page.page_number:04d}.png",
    )


def _write_export_atomically(destination: Path, write):
    """Write through a unique temp file and rename into place.

    Concurrent writes to the same destination would otherwise interleave
    truncate-mode writes and leave a permanently corrupt artifact whose
    recorded sha256 never matches any complete output; destinations carry a
    per-export serial so live bundles are never rewritten underneath a
    download.
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
    if not chapter or chapter.deleted_at is not None:
        raise HTTPException(status_code=404, detail="章节不存在")
    project = db.get(Project, chapter.project_id)
    if not project or project.deleted_at is not None:
        raise HTTPException(status_code=404, detail="项目不存在")
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
        # PNG/PDF/JSON artifacts carry a random serial between the token and
        # the extension, so an exact storage_key match never hits across
        # executions. The token prefix is content-addressed (selected
        # candidate set), which is the idempotency identity.
        key_prefix = f"exports/{project.id}/{chapter.id}/{token}-"
        existing = db.scalar(
            select(ExportBundle)
            .where(
                ExportBundle.chapter_id == chapter.id,
                ExportBundle.export_type == payload.export_type,
                ExportBundle.storage_key.like(f"{key_prefix}%"),
            )
            .order_by(ExportBundle.created_at.desc(), ExportBundle.id.desc())
            .limit(1)
        )
        if existing is not None:
            # The committed row is the idempotency marker: it is only written
            # after the artifact has been renamed into place, so the file it
            # points at is complete.
            return existing

    serial = f"{utcnow().strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:8]}"
    if payload.export_type == "PNG":
        destination = output_dir / f"{token}-{serial}-pages.zip"

        def _write_zip(temp: Path) -> None:
            with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as archive:
                for page, _, asset in selected:
                    archive.write(
                        _asset_path(asset),
                        arcname=f"{page.page_number:04d}-{_safe_archive_name(asset.original_name)}",
                    )

        _write_export_atomically(destination, _write_zip)
    elif payload.export_type == "PDF":
        destination = output_dir / f"{token}-{serial}-chapter.pdf"

        def _write_pdf(temp: Path) -> None:
            # Encode page by page in append mode: save_all keeps every
            # decoded bitmap referenced until the final write, so a long
            # chapter's peak memory is one page, not the whole chapter.
            for index, (_, _, asset) in enumerate(selected):
                with Image.open(_asset_path(asset)) as page_image:
                    # format= is explicit because the temp filename's suffix
                    # is ".tmp", from which Pillow cannot infer the encoder.
                    page_image.save(temp, format="PDF", append=index > 0)

        _write_export_atomically(destination, _write_pdf)
    else:
        destination = output_dir / f"{token}-{serial}-project.json"
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
    _prune_superseded_exports(db, settings, chapter.id, payload.export_type, bundle.id)
    return bundle


EXPORT_KEEP_PER_CHAPTER_TYPE = 20


def _prune_superseded_exports(
    db: Session, settings, chapter_id: str, export_type: str, keep_id: str
) -> None:
    """Bound export disk usage: keep the newest bundles per chapter+type.

    Unique destination names mean every export owns its file forever, so a
    repeated export button would otherwise grow storage without bound — no
    other cleanup pass exists for these artifacts. Rows are removed first
    (one commit); files are unlinked after, so a crash leaves at worst a
    harmless orphan file, never a dangling bundle row.
    """

    superseded = list(
        db.scalars(
            select(ExportBundle)
            .where(
                ExportBundle.chapter_id == chapter_id,
                ExportBundle.export_type == export_type,
                ExportBundle.id != keep_id,
            )
            # The just-created bundle occupies one slot of the cap.
            .order_by(ExportBundle.created_at.desc(), ExportBundle.id.desc())
            .offset(EXPORT_KEEP_PER_CHAPTER_TYPE - 1)
        )
    )
    if not superseded:
        return
    exports_root = (settings.storage_root / "exports").resolve()
    removed_keys = [row.storage_key for row in superseded]
    db.execute(
        delete(ExportBundle).where(
            ExportBundle.id.in_([row.id for row in superseded])
        )
    )
    db.commit()
    for key in removed_keys:
        path = (settings.storage_root / key).resolve()
        if path.is_relative_to(exports_root) and path.is_file():
            with suppress(OSError):
                path.unlink()


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
def download_export(
    export_id: str, db: Session = Depends(get_db), project_id: str | None = None
) -> FileResponse:
    bundle = db.get(ExportBundle, export_id)
    if not bundle:
        raise HTTPException(status_code=404, detail="导出记录不存在")
    ensure_project_scope(db, bundle, project_id, label="导出记录")
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
