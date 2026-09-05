"""Create an isolated backup-restore fixture. Never points at real app data."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sys
import zipfile
from io import BytesIO
from pathlib import Path

from PIL import Image

from backup_restore import (
    FIXTURE_KIND,
    FIXTURE_MARKER_NAME,
    BackupRestoreError,
    _write_json,
    create_new_directory,
    read_schema_revision,
    run_alembic_upgrade,
)

QUALITY_CATEGORIES = ("SPEAKER", "CHARACTER", "OUTFIT", "PROP", "CONTINUITY")


def _png(color: tuple[int, int, int, int]) -> bytes:
    buffer = BytesIO()
    Image.new("RGBA", (1, 1), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _webp(color: tuple[int, int, int, int], side: int) -> bytes:
    buffer = BytesIO()
    Image.new("RGBA", (side, side), color).save(buffer, format="WEBP", quality=82)
    return buffer.getvalue()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _complete_fixture_marker(marker_path: Path, marker: dict[str, object]) -> None:
    pending = marker_path.with_name(marker_path.name + ".pending")
    completed = {**marker, "status": "complete"}
    try:
        _write_json(pending, completed)
        os.replace(pending, marker_path)
        confirmed = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BackupRestoreError("OWNER_MARKER_UPDATE_FAILED", str(exc)) from exc
    if (
        confirmed.get("kind") != FIXTURE_KIND
        or confirmed.get("run_id") != marker["run_id"]
        or confirmed.get("status") != "complete"
    ):
        raise BackupRestoreError(
            "OWNER_MARKER_UPDATE_FAILED",
            "fixture marker update was not durable",
        )


def create_isolated_fixture(destination: Path, *, repo_root: Path) -> dict[str, str]:
    dest = create_new_directory(destination)
    marker_path = dest / FIXTURE_MARKER_NAME
    marker: dict[str, object] = {
        "version": 1,
        "kind": FIXTURE_KIND,
        "run_id": secrets.token_hex(32),
        "status": "in_progress",
    }
    _write_json(marker_path, marker)
    (dest / "storage" / "generated").mkdir(parents=True)
    (dest / "storage" / "thumbnails").mkdir(parents=True)
    (dest / "storage" / "exports").mkdir(parents=True)
    (dest / "uploads").mkdir(parents=True)
    run_alembic_upgrade(dest, repo_root)

    api_root = repo_root / "apps" / "api"
    if str(api_root) not in sys.path:
        sys.path.insert(0, str(api_root))
    if "app.models" not in sys.modules:
        os.environ.setdefault("MANGAFLOW_DISABLE_DOTENV", "1")
        os.environ.setdefault(
            "DATABASE_URL",
            "sqlite:///" + (dest / "storage" / "mangaflow.db").resolve().as_posix(),
        )
        os.environ.setdefault("STORAGE_ROOT", str(dest / "storage"))
        os.environ.setdefault("UPLOAD_ROOT", str(dest / "uploads"))

    from app.domain.states import Resolution
    from app.models import (
        Asset,
        Chapter,
        ExportBundle,
        GenerationBatch,
        InspectionResult,
        MangaPage,
        PageCandidate,
        Panel,
        Project,
    )
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    database = dest / "storage" / "mangaflow.db"
    engine = create_engine("sqlite:///" + database.as_posix())
    generated_png = _png((0, 0, 0, 255))
    upload_png = _png((255, 0, 0, 255))
    generated_digest = _sha256(generated_png)
    upload_digest = _sha256(upload_png)

    with Session(engine) as session:
        project = Project(name="backup-restore-fixture")
        session.add(project)
        session.flush()
        chapter = Chapter(project_id=project.id, title="演练章", ordinal=1)
        session.add(chapter)
        session.flush()
        page = MangaPage(
            chapter_id=chapter.id,
            page_number=1,
            storyboard_version=1,
            selected_candidate_ack_version=1,
            continuity_status="PASSED",
            source_coverage={"complete": True},
        )
        session.add(page)
        session.flush()
        session.add(Panel(page_id=page.id, reading_order=1, background="隔离占位页"))
        batch = GenerationBatch(
            project_id=project.id,
            chapter_id=chapter.id,
            page_id=page.id,
            ordinal=1,
            generation_kind="PAGE",
            status="OPEN",
        )
        session.add(batch)
        session.flush()

        generated_key = f"generated/{project.id}/{batch.id}/page.png"
        upload_key = f"{project.id}/reference.png"
        _write_bytes(dest / "storage" / generated_key, generated_png)
        _write_bytes(dest / "uploads" / upload_key, upload_png)

        generated_asset = Asset(
            project_id=project.id,
            kind="PAGE_CANDIDATE",
            original_name="page.png",
            storage_key=generated_key,
            mime_type="image/png",
            byte_size=len(generated_png),
            sha256=generated_digest,
            width=1,
            height=1,
            source="GENERATED",
            status="GENERATED",
        )
        upload_asset = Asset(
            project_id=project.id,
            kind="STYLE_REFERENCE",
            original_name="reference.png",
            storage_key=upload_key,
            mime_type="image/png",
            byte_size=len(upload_png),
            sha256=upload_digest,
            width=1,
            height=1,
            source="USER_UPLOAD",
            status="UPLOADED",
        )
        session.add_all([generated_asset, upload_asset])
        session.flush()

        # Real DB-referenced blobs outside the historical backup scope: webp
        # thumbnails keyed by asset id (services/media.py create_thumbnails
        # layout) and a committed ExportBundle artifact (api/routes/exports.py
        # naming), so drills must cover them end to end.
        thumbnail_320_key = f"thumbnails/{generated_asset.id}/320.webp"
        thumbnail_640_key = f"thumbnails/{generated_asset.id}/640.webp"
        _write_bytes(dest / "storage" / thumbnail_320_key, _webp((32, 32, 32, 255), 320))
        _write_bytes(dest / "storage" / thumbnail_640_key, _webp((64, 64, 64, 255), 640))
        generated_asset.thumbnail_320_key = thumbnail_320_key
        generated_asset.thumbnail_640_key = thumbnail_640_key

        candidate = PageCandidate(
            batch_id=batch.id,
            page_id=page.id,
            ordinal=1,
            model_alias="image.nano_banana_2",
            resolution=Resolution.DRAFT_1K,
            status="INSPECTED",
            asset_id=generated_asset.id,
            based_on_storyboard_version=1,
            is_selected=True,
        )
        session.add(candidate)
        session.flush()
        page.selected_candidate_id = candidate.id

        export_token = hashlib.sha256(candidate.id.encode("utf-8")).hexdigest()[:12]
        export_key = (
            f"exports/{project.id}/{chapter.id}/"
            f"{export_token}-20260905000000-pages.zip"
        )
        bundle_buffer = BytesIO()
        with zipfile.ZipFile(bundle_buffer, "w", zipfile.ZIP_DEFLATED) as bundle_zip:
            bundle_zip.writestr("0001-page.png", generated_png)
        bundle_bytes = bundle_buffer.getvalue()
        _write_bytes(dest / "storage" / export_key, bundle_bytes)
        session.add(
            ExportBundle(
                project_id=project.id,
                chapter_id=chapter.id,
                export_type="PNG",
                storage_key=export_key,
                byte_size=len(bundle_bytes),
                sha256=_sha256(bundle_bytes),
                page_count=1,
            )
        )

        for category in QUALITY_CATEGORIES:
            session.add(
                InspectionResult(
                    candidate_id=candidate.id,
                    storyboard_version=1,
                    category=category,
                    outcome="PASS",
                    score=1.0,
                )
            )
        session.commit()
        ids = {
            "root": str(dest),
            "project_id": project.id,
            "chapter_id": chapter.id,
            "page_id": page.id,
            "generated_key": generated_key,
            "upload_key": upload_key,
            "generated_asset_id": generated_asset.id,
            "thumbnail_320_key": thumbnail_320_key,
            "thumbnail_640_key": thumbnail_640_key,
            "export_storage_key": export_key,
        }

    engine.dispose()
    (dest / ".env").write_text(
        "GOOGLE_CLOUD_PROJECT=must-not-be-copied\n", encoding="utf-8"
    )
    (dest / "storage" / ".provider-credential-master-key").write_text(
        "must-not-be-copied\n", encoding="utf-8"
    )
    (dest / "uploads" / ".env.local").write_text(
        "SECRET=must-not-be-copied\n", encoding="utf-8"
    )
    (dest / "uploads" / "credentials.json").write_text("{}\n", encoding="utf-8")
    (dest / "storage" / "generated" / ".provider-credential-master-key").write_text(
        "must-not-be-copied\n", encoding="utf-8"
    )

    marker.update(
        page_id=ids["page_id"],
        project_id=ids["project_id"],
        chapter_id=ids["chapter_id"],
    )
    _complete_fixture_marker(marker_path, marker)
    ids["schema_revision"] = read_schema_revision(database)
    return ids
