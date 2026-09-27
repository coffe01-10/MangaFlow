"""Regression tests for provider-output validation and honest naming (#1010, #1011).

``_save_asset_candidate`` used to swallow ``Image.open`` failures, persist
bytes under a hardcoded ``.png`` name and let ``create_thumbnails`` re-raise
the decode error — the worker's generic path then classified a deterministic
bad output as retryable WORKER_ERROR and re-billed the provider call up to
``max_attempts`` times. ``_save_generated_asset`` validated the format but
still named the soft-delete-revived row ``.png``. Both paths now run the
upload-grade ``inspect_image_bytes`` gate and name files by the real suffix.
"""

import io

import pytest
from app.config import get_settings
from app.domain.states import Resolution
from app.model_adapters.base import ProviderAdapterError
from app.models import (
    Asset,
    AssetCandidate,
    Chapter,
    GenerationBatch,
    MangaPage,
    PageCandidate,
    Project,
)
from app.services.worker_handlers.asset_generate import _save_asset_candidate
from app.services.worker_handlers.page_generate import _save_generated_asset
from PIL import Image
from sqlalchemy import select


def _image_bytes(fmt: str, size: int = 8) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (size, size), (100, 100, 100)).save(buffer, format=fmt)
    return buffer.getvalue()


@pytest.fixture
def storage_root(tmp_path, monkeypatch):
    root = tmp_path / "storage"
    monkeypatch.setattr(get_settings(), "storage_root", root)
    return root


def _seed_page_flow(db):
    project = Project(name="输出校验项目")
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
    candidate = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=1,
        model_alias="fake-model",
        resolution=Resolution.DRAFT_1K,
    )
    db.add(candidate)
    db.flush()
    return project, candidate


def _seed_asset_flow(db):
    project = Project(name="资产输出校验项目")
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
    candidate = AssetCandidate(
        batch_id=batch.id,
        ordinal=1,
        model_alias="fake-model",
        resolution=Resolution.DRAFT_1K,
        variant="FRONT",
    )
    db.add(candidate)
    db.flush()
    return project, batch, candidate


def _assert_invalid_output_rejected(db, storage_root, save):
    with pytest.raises(ProviderAdapterError) as caught:
        save()
    error = caught.value
    assert error.code == "INVALID_OUTPUT"
    # The worker maps retryable=False to a terminal FAILED: no paid re-call.
    assert error.retryable is False
    assert list(db.scalars(select(Asset))) == []
    assert not list((storage_root / "generated").rglob("*.*"))


def test_asset_save_rejects_undecodable_output_terminally(db_session, storage_root):
    """#1010: garbage bytes must fail INVALID_OUTPUT, not retryable WORKER_ERROR."""
    project, _batch, candidate = _seed_asset_flow(db_session)
    _assert_invalid_output_rejected(
        db_session,
        storage_root,
        lambda: _save_asset_candidate(
            db_session, candidate, project.id, b"not-an-image"
        ),
    )


def test_asset_save_rejects_off_whitelist_format_terminally(
    db_session, storage_root
):
    """#1010: decodable but unsupported formats (GIF) must not be stored."""
    project, _batch, candidate = _seed_asset_flow(db_session)
    _assert_invalid_output_rejected(
        db_session,
        storage_root,
        lambda: _save_asset_candidate(
            db_session, candidate, project.id, _image_bytes("GIF")
        ),
    )


def test_page_save_rejects_undecodable_output_terminally(db_session, storage_root):
    """#1010: the page path keeps the same terminal contract."""
    _project, candidate = _seed_page_flow(db_session)
    _assert_invalid_output_rejected(
        db_session,
        storage_root,
        lambda: _save_generated_asset(db_session, candidate, b"not-an-image"),
    )


def test_page_save_rejects_degenerate_tiny_output(db_session, storage_root):
    """Outputs below the minimum side are rejected before thumbnailing."""
    _project, candidate = _seed_page_flow(db_session)
    _assert_invalid_output_rejected(
        db_session,
        storage_root,
        lambda: _save_generated_asset(
            db_session, candidate, _image_bytes("PNG", size=4)
        ),
    )


def test_asset_save_names_jpeg_output_honestly(db_session, storage_root):
    """#1011: storage_key/original_name/mime follow the real byte format."""
    project, _batch, candidate = _seed_asset_flow(db_session)
    data = _image_bytes("JPEG")
    asset = _save_asset_candidate(db_session, candidate, project.id, data)
    db_session.commit()

    assert asset.mime_type == "image/jpeg"
    assert asset.storage_key.endswith(".jpg")
    assert asset.original_name.endswith(".jpg")
    assert (storage_root / asset.storage_key).is_file()
    with Image.open(storage_root / asset.storage_key) as stored:
        assert stored.format == "JPEG"


def test_page_save_names_webp_output_honestly(db_session, storage_root):
    """#1011: the page candidate path names by the decoded suffix too."""
    _project, candidate = _seed_page_flow(db_session)
    data = _image_bytes("WEBP")
    asset = _save_generated_asset(db_session, candidate, data)
    db_session.commit()

    assert asset.mime_type == "image/webp"
    assert asset.storage_key.endswith(".webp")
    assert asset.original_name.endswith(".webp")
    assert (storage_root / asset.storage_key).is_file()
