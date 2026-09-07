"""Regression: content-addressed asset dedupe must not adopt tombstones.

``assets`` carries a hard ``(project_id, sha256)`` unique constraint, and the
worker save paths used to look up duplicates without filtering
``deleted_at`` — so a byte-identical regeneration after the user deleted the
asset bound the paid output to a soft-deleted row whose content URL 404s.
The dedupe helpers now only reuse live rows and, when the insert collides
with a tombstone, revive it instead of failing forever.
"""

from datetime import UTC, datetime

from app.models import (
    Asset,
    Chapter,
    GenerationBatch,
    MangaPage,
    PageCandidate,
    Project,
)
from app.services.asset_dedupe import adopt_deleted_duplicate, live_duplicate
from app.services.candidate_lineage import store_region_mask_asset
from app.services.worker_handlers.asset_generate import _save_asset_candidate
from app.services.worker_handlers.page_generate import _save_generated_asset

import hashlib
import struct
import zlib


def _png_bytes(color: bytes) -> bytes:
    """Build a minimal valid one-pixel PNG with the given RGB color."""

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00" + color)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def _page_candidate_chain(db) -> tuple[Project, PageCandidate]:
    project = Project(name="dedupe-page")
    db.add(project)
    db.flush()
    chapter = Chapter(project_id=project.id, title="c1", ordinal=1)
    db.add(chapter)
    db.flush()
    page = MangaPage(chapter_id=chapter.id, page_number=1)
    batch = GenerationBatch(
        project_id=project.id,
        chapter_id=chapter.id,
        page_id=page.id,
        ordinal=1,
    )
    db.add_all([page, batch])
    db.flush()
    candidate = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=1,
        model_alias="image.test",
        resolution="DRAFT_1K",
        status="GENERATING",
    )
    db.add(candidate)
    db.commit()
    return project, candidate


def _asset_candidate_chain(db) -> tuple[Project, object]:
    project = Project(name="dedupe-asset")
    db.add(project)
    db.flush()
    batch = GenerationBatch(
        project_id=project.id,
        target_type="STYLE",
        ordinal=2,
        generation_kind="STYLE_TEST",
    )
    db.add(batch)
    db.flush()
    from app.models import AssetCandidate

    candidate = AssetCandidate(
        batch_id=batch.id,
        ordinal=1,
        model_alias="image.test",
        resolution="DRAFT_1K",
        variant="STYLE_TEST",
        status="GENERATING",
    )
    db.add(candidate)
    db.commit()
    return project, candidate


def _soft_delete(asset: Asset) -> None:
    asset.deleted_at = datetime.now(UTC)
    asset.version += 1


def test_page_save_ignores_deleted_duplicate_and_revives_on_collision(
    db_session, monkeypatch, tmp_path
):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "storage_root", tmp_path / "storage")
    project, candidate = _page_candidate_chain(db_session)
    payload = _png_bytes(b"\x10\x20\x30")

    first = _save_generated_asset(db_session, candidate, payload)
    db_session.commit()
    digest = hashlib.sha256(payload).hexdigest()

    _soft_delete(first)
    db_session.commit()

    assert live_duplicate(
        db_session, project_id=project.id, sha256=digest
    ) is None, "a tombstoned asset must not satisfy the live duplicate lookup"

    second = _save_generated_asset(db_session, candidate, payload)
    db_session.commit()
    assert second.id == first.id
    assert second.deleted_at is None

    rows = (
        db_session.query(Asset)
        .filter(Asset.project_id == project.id, Asset.sha256 == digest)
        .all()
    )
    assert len(rows) == 1 and rows[0].deleted_at is None


def test_page_save_creates_new_row_for_different_bytes(db_session, monkeypatch, tmp_path):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "storage_root", tmp_path / "storage")
    _, candidate = _page_candidate_chain(db_session)

    first = _save_generated_asset(db_session, candidate, _png_bytes(b"\x01\x02\x03"))
    second = _save_generated_asset(db_session, candidate, _png_bytes(b"\x04\x05\x06"))
    db_session.commit()
    assert first.id != second.id


def test_asset_save_ignores_deleted_duplicate(db_session, monkeypatch, tmp_path):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "storage_root", tmp_path / "storage")
    project, candidate = _asset_candidate_chain(db_session)
    payload = _png_bytes(b"\xa0\xb0\xc0")

    first = _save_asset_candidate(db_session, candidate, project.id, payload)
    db_session.commit()
    _soft_delete(first)
    db_session.commit()

    second = _save_asset_candidate(db_session, candidate, project.id, payload)
    db_session.commit()
    assert second.id == first.id
    assert second.deleted_at is None


def test_region_mask_adopt_survives_deleted_mask(db_session, monkeypatch, tmp_path):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "storage_root", tmp_path / "storage")
    project = Project(name="dedupe-mask")
    db_session.add(project)
    db_session.commit()
    regions = [{"points": [[1, 1], [2, 2], [3, 3]]}]

    first = store_region_mask_asset(
        db_session, project_id=project.id, regions=regions, source_command_id="cmd-1"
    )
    db_session.commit()
    _soft_delete(first)
    db_session.commit()

    second = store_region_mask_asset(
        db_session, project_id=project.id, regions=regions, source_command_id="cmd-1"
    )
    db_session.commit()
    assert second.id == first.id
    assert second.deleted_at is None

    other = store_region_mask_asset(
        db_session, project_id=project.id, regions=regions, source_command_id="cmd-2"
    )
    db_session.commit()
    assert other.id != first.id


def test_adopt_deleted_duplicate_returns_none_without_tombstone(db_session):
    project = Project(name="dedupe-none")
    db_session.add(project)
    db_session.commit()
    assert (
        adopt_deleted_duplicate(
            db_session, project_id=project.id, sha256="e" * 64
        )
        is None
    )
