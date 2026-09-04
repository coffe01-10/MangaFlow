"""Regression tests for generated-asset sha256 dedupe integrity.

Asset carries a hard ``UniqueConstraint("project_id", "sha256")``, so a fresh
row for an already-seen digest is impossible. The worker handlers used to look
up the digest without a ``deleted_at``/``source``/``kind`` filter, which could
attach a soft-deleted asset (content 404s) or a byte-identical user upload to
a new paid candidate. ``upload_asset`` already revives soft-deleted rows for
exactly this reason; these tests pin the same integrity contract for
``_save_generated_asset`` (page flow) and ``_save_asset_candidate`` (asset
flow).
"""

import hashlib
import io

import pytest
from PIL import Image
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.domain.states import Resolution
from app.models import (
    Asset,
    AssetCandidate,
    AssetStatus,
    Chapter,
    GenerationBatch,
    MangaPage,
    PageCandidate,
    Project,
    utcnow,
)
from app.services.worker_handlers.asset_generate import _save_asset_candidate
from app.services.worker_handlers.page_generate import _save_generated_asset


def _png_bytes(color: int = 100) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (color, color, color)).save(buffer, format="PNG")
    return buffer.getvalue()


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def storage_root(tmp_path, monkeypatch):
    root = tmp_path / "storage"
    monkeypatch.setattr(get_settings(), "storage_root", root)
    return root


def _seed_page_flow(db):
    project = Project(name="页面去重项目")
    db.add(project)
    db.flush()
    chapter = Chapter(project_id=project.id, title="第一章", ordinal=1)
    db.add(chapter)
    db.flush()
    page = MangaPage(chapter_id=chapter.id, page_number=1)
    db.add(page)
    db.flush()
    batch = GenerationBatch(
        project_id=project.id,
        chapter_id=chapter.id,
        page_id=page.id,
        ordinal=1,
        generation_kind="PAGE",
    )
    db.add(batch)
    db.flush()
    candidates = [
        PageCandidate(
            batch_id=batch.id,
            page_id=page.id,
            ordinal=ordinal,
            model_alias="fake-model",
            resolution=Resolution.DRAFT_1K,
        )
        for ordinal in (1, 2)
    ]
    db.add_all(candidates)
    db.flush()
    return project, chapter, batch, candidates


def _seed_asset_flow(db):
    project = Project(name="资产去重项目")
    db.add(project)
    db.flush()
    batch = GenerationBatch(
        project_id=project.id,
        ordinal=1,
        target_type="CHARACTER",
        target_id="character-1",
        generation_kind="CHARACTER",
    )
    db.add(batch)
    db.flush()
    candidates = [
        AssetCandidate(
            batch_id=batch.id,
            ordinal=ordinal,
            model_alias="fake-model",
            resolution=Resolution.DRAFT_1K,
            variant="FRONT",
        )
        for ordinal in (1, 2)
    ]
    db.add_all(candidates)
    db.flush()
    return project, batch, candidates


def _upload_asset_row(project_id: str, data: bytes) -> Asset:
    return Asset(
        project_id=project_id,
        kind="CHARACTER_REFERENCE",
        original_name="ref.png",
        storage_key=f"{project_id}/ref.png",
        mime_type="image/png",
        byte_size=len(data),
        sha256=_digest(data),
        width=8,
        height=8,
        source="USER_UPLOAD",
        status=AssetStatus.UPLOADED,
    )


# ---------------------------------------------------------------------------
# Page flow: _save_generated_asset
# ---------------------------------------------------------------------------


def test_page_save_revives_soft_deleted_page_candidate(db_session, storage_root):
    """T1: a soft-deleted page_candidate row must be revived, not returned dead."""
    project, _chapter, batch, candidates = _seed_page_flow(db_session)
    data = _png_bytes()
    first = _save_generated_asset(db_session, candidates[0], data)
    db_session.commit()

    first.deleted_at = utcnow()
    db_session.commit()

    revived = _save_generated_asset(db_session, candidates[1], data)
    db_session.commit()

    # The hard UNIQUE(project_id, sha256) forbids a fresh row: revive in place.
    assert revived.id == first.id
    assert revived.deleted_at is None
    assert revived.source == "AI_GENERATED"
    assert revived.kind == "page_candidate"
    expected_key = (
        storage_root / "generated" / project.id / batch.id / f"{candidates[1].id}.png"
    ).relative_to(storage_root).as_posix()
    assert revived.storage_key == expected_key
    assert (storage_root / revived.storage_key).is_file()
    assert revived.thumbnail_320_key
    assert (storage_root / revived.thumbnail_320_key).is_file()
    assert revived.mime_type == "image/png"
    assert revived.byte_size == len(data)

    rows = list(db_session.scalars(select(Asset).where(Asset.sha256 == _digest(data))))
    assert [row.id for row in rows] == [first.id]
    assert rows[0].deleted_at is None


def test_page_save_never_attaches_user_upload_with_same_digest(db_session, storage_root):
    """T2: a live USER_UPLOAD row with the digest must not be handed to the candidate."""
    project, _chapter, _batch, candidates = _seed_page_flow(db_session)
    data = _png_bytes()
    upload = _upload_asset_row(project.id, data)
    db_session.add(upload)
    db_session.commit()

    # The insert cannot succeed (hard UNIQUE(project_id, sha256) is held by the
    # upload row) and reviving the upload is forbidden: the honest outcome is
    # the same IntegrityError the old fallback re-raised.
    with pytest.raises(IntegrityError):
        _save_generated_asset(db_session, candidates[0], data)
    db_session.rollback()

    rows = list(db_session.scalars(select(Asset).where(Asset.sha256 == _digest(data))))
    assert [row.id for row in rows] == [upload.id]
    assert rows[0].source == "USER_UPLOAD"
    assert not list((storage_root / "generated").rglob("*.png"))


def test_page_save_keeps_live_generated_dedupe(db_session, storage_root):
    """T3: normal live-row dedupe is preserved (same row, no new row)."""
    _project, _chapter, _batch, candidates = _seed_page_flow(db_session)
    data = _png_bytes()
    first = _save_generated_asset(db_session, candidates[0], data)
    db_session.commit()

    second = _save_generated_asset(db_session, candidates[1], data)
    db_session.commit()

    assert second.id == first.id
    rows = list(db_session.scalars(select(Asset).where(Asset.sha256 == _digest(data))))
    assert len(rows) == 1
    assert rows[0].deleted_at is None
    assert rows[0].kind == "page_candidate"


def test_page_save_returns_live_row_when_insert_collides(db_session, storage_root):
    """Concurrent dedupe: a live AI-generated row appearing after the lookup wins."""
    project, _chapter, _batch, candidates = _seed_page_flow(db_session)
    data = _png_bytes()
    # Simulate a concurrently committed row: pending in this transaction, so
    # autoflush=False hides it from the pre-insert lookup, but it occupies the
    # (project_id, sha256) slot at insert time.
    concurrent = Asset(
        project_id=project.id,
        kind="page_candidate",
        original_name="concurrent.png",
        storage_key=f"generated/{project.id}/other/concurrent.png",
        mime_type="image/png",
        byte_size=len(data),
        sha256=_digest(data),
        width=8,
        height=8,
        source="AI_GENERATED",
        status=AssetStatus.GENERATED,
    )
    db_session.add(concurrent)

    result = _save_generated_asset(db_session, candidates[0], data)
    db_session.commit()

    assert result.id == concurrent.id
    rows = list(db_session.scalars(select(Asset).where(Asset.sha256 == _digest(data))))
    assert [row.id for row in rows] == [concurrent.id]
    assert rows[0].deleted_at is None
    assert not list((storage_root / "generated").rglob("*.png"))


# ---------------------------------------------------------------------------
# Asset flow: _save_asset_candidate
# ---------------------------------------------------------------------------


def test_asset_save_revives_soft_deleted_generated_row(db_session, storage_root):
    project, batch, candidates = _seed_asset_flow(db_session)
    data = _png_bytes()
    first = _save_asset_candidate(db_session, candidates[0], project.id, data)
    db_session.commit()

    first.deleted_at = utcnow()
    db_session.commit()

    revived = _save_asset_candidate(db_session, candidates[1], project.id, data)
    db_session.commit()

    assert revived.id == first.id
    assert revived.deleted_at is None
    assert revived.source == "AI_GENERATED"
    assert revived.kind == "character"
    expected_key = (
        storage_root / "generated" / project.id / batch.id / f"{candidates[1].id}.png"
    ).relative_to(storage_root).as_posix()
    assert revived.storage_key == expected_key
    assert (storage_root / revived.storage_key).is_file()
    assert revived.thumbnail_320_key
    assert (storage_root / revived.thumbnail_320_key).is_file()

    rows = list(db_session.scalars(select(Asset).where(Asset.sha256 == _digest(data))))
    assert [row.id for row in rows] == [first.id]
    assert rows[0].deleted_at is None


def test_asset_save_never_attaches_user_upload_with_same_digest(db_session, storage_root):
    project, _batch, candidates = _seed_asset_flow(db_session)
    data = _png_bytes()
    upload = _upload_asset_row(project.id, data)
    db_session.add(upload)
    db_session.commit()

    with pytest.raises(IntegrityError):
        _save_asset_candidate(db_session, candidates[0], project.id, data)
    db_session.rollback()

    rows = list(db_session.scalars(select(Asset).where(Asset.sha256 == _digest(data))))
    assert [row.id for row in rows] == [upload.id]
    assert rows[0].source == "USER_UPLOAD"
    assert not list((storage_root / "generated").rglob("*.png"))


def test_asset_save_keeps_live_generated_dedupe(db_session, storage_root):
    _project, _batch, candidates = _seed_asset_flow(db_session)
    data = _png_bytes()
    first = _save_asset_candidate(db_session, candidates[0], _project.id, data)
    db_session.commit()

    second = _save_asset_candidate(db_session, candidates[1], _project.id, data)
    db_session.commit()

    assert second.id == first.id
    rows = list(db_session.scalars(select(Asset).where(Asset.sha256 == _digest(data))))
    assert len(rows) == 1
    assert rows[0].deleted_at is None
    assert rows[0].kind == "character"


def test_asset_save_never_reuses_other_generated_kind(db_session, storage_root):
    """A byte-identical AI-generated page_candidate must not become a character asset."""
    project, _chapter, _page_batch, page_candidates = _seed_page_flow(db_session)
    data = _png_bytes()
    page_asset = _save_generated_asset(db_session, page_candidates[0], data)
    db_session.commit()

    # An asset-generation batch for the same project, colliding with the page
    # candidate's digest. The kind filter must not hand the page row over, and
    # the hard UNIQUE(project_id, sha256) leaves no honest row to return.
    batch = GenerationBatch(
        project_id=project.id,
        ordinal=2,
        target_type="CHARACTER",
        target_id="character-1",
        generation_kind="CHARACTER",
    )
    db_session.add(batch)
    db_session.flush()
    asset_candidate = AssetCandidate(
        batch_id=batch.id,
        ordinal=1,
        model_alias="fake-model",
        resolution=Resolution.DRAFT_1K,
        variant="FRONT",
    )
    db_session.add(asset_candidate)
    db_session.commit()

    with pytest.raises(IntegrityError):
        _save_asset_candidate(db_session, asset_candidate, project.id, data)
    db_session.rollback()

    rows = list(db_session.scalars(select(Asset).where(Asset.sha256 == _digest(data))))
    assert [row.id for row in rows] == [page_asset.id]
    assert rows[0].kind == "page_candidate"
    assert not list((storage_root / "generated").rglob(f"{batch.id}/*.png"))
