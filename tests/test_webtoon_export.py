"""PUB-01C WEBTOON 条漫导出回归：契约 docs/pub-01b-webtoon-export-contract.md。"""

import hashlib
import json
import zipfile
from io import BytesIO

import pytest
from app.api.routes import exports as exports_module
from app.config import get_settings
from app.models import Asset, Chapter, ExportBundle, Project
from app.services.manga_export import (
    WebtoonExportError,
    compute_slices,
    resolve_webtoon_params,
    scaled_page_height,
)
from PIL import Image
from sqlalchemy import select

# ---------- 切片算法（契约 §3 金样例） ----------


def test_compute_slices_gap_preferred_golden():
    slices = compute_slices([1000, 1000, 1000], gap_px=16, max_slice_height=1100)
    assert len(slices) == 3
    for index, slice_ in enumerate(slices):
        assert [part.page_index for part in slice_.parts] == [index]
        assert slice_.height == 1000
        assert slice_.hard_cut is False


def test_compute_slices_packs_until_overflow():
    slices = compute_slices([500, 400, 400], gap_px=16, max_slice_height=1000)
    assert len(slices) == 2
    assert [part.page_index for part in slices[0].parts] == [0, 1]
    assert slices[0].height == 916  # 500 + 16 + 400
    assert [part.page_index for part in slices[1].parts] == [2]


def test_compute_slices_hard_cuts_oversized_page():
    slices = compute_slices([300, 5000, 300], gap_px=0, max_slice_height=1000)
    assert len(slices) == 7
    hard = [slice_ for slice_ in slices if slice_.hard_cut]
    assert len(hard) == 5
    assert [slice_.height for slice_ in hard] == [1000] * 5
    assert all(len(slice_.parts) == 1 and slice_.parts[0].page_index == 1 for slice_ in hard)
    assert [part.src_top for part in (slice_.parts[0] for slice_ in hard)] == [
        0,
        1000,
        2000,
        3000,
        4000,
    ]
    assert slices[0].parts[0].page_index == 0
    assert slices[-1].parts[0].page_index == 2


def test_compute_slices_oversized_remainder_forms_tail_slice():
    slices = compute_slices([5300], gap_px=0, max_slice_height=1000)
    assert [slice_.height for slice_ in slices] == [1000, 1000, 1000, 1000, 1000, 300]
    assert all(slice_.hard_cut for slice_ in slices)


def test_scaled_page_height_missing_dimensions_rejected():
    with pytest.raises(WebtoonExportError):
        scaled_page_height(None, 2160, 1080)
    with pytest.raises(WebtoonExportError):
        scaled_page_height(0, 2160, 1080)


def test_resolve_params_defaults_and_overrides():
    params = resolve_webtoon_params(
        preset=None, width=None, format=None, quality=None, gap_px=None, max_slice_height=None
    )
    assert (params.width, params.format, params.quality, params.gap_px) == (1080, "JPEG", 88, 16)
    custom = resolve_webtoon_params(
        preset="COMPACT", width=1200, format="PNG", quality=50, gap_px=8, max_slice_height=2048
    )
    # PNG 不带 quality；显式 width/gap/max 覆盖预设值。
    assert (custom.width, custom.format, custom.quality, custom.gap_px, custom.max_slice_height) == (
        1200,
        "PNG",
        None,
        8,
        2048,
    )
    with pytest.raises(WebtoonExportError):
        resolve_webtoon_params(
            preset="NOPE", width=None, format=None, quality=None, gap_px=None, max_slice_height=None
        )


# ---------- 端到端导出 ----------


def _seed_chapter_with_pages(db_session, tmp_path, monkeypatch, sizes):
    project = Project(name="条漫导出")
    db_session.add(project)
    db_session.flush()
    chapter = Chapter(project_id=project.id, title="第一章", ordinal=1)
    db_session.add(chapter)
    db_session.commit()

    storage = tmp_path / "storage"
    storage.mkdir()
    monkeypatch.setattr(get_settings(), "storage_root", storage)

    pages, assets = [], []
    for index, (width, height) in enumerate(sizes):
        blob = tmp_path / f"page-{index}.png"
        Image.new("RGB", (width, height), color="navy").save(blob, format="PNG")
        asset = Asset(
            project_id=project.id,
            kind="page_candidate",
            original_name=f"page-{index}.png",
            storage_key=blob.name,
            mime_type="image/png",
            byte_size=blob.stat().st_size,
            sha256=f"{index:064x}",
            source="AI_GENERATED",
            status="GENERATED",
            width=width,
            height=height,
        )
        db_session.add(asset)
        blob.rename(storage / blob.name)

        page = type("P", (), {})()
        page.id = f"page-{index}"
        page.page_number = index + 1
        pages.append(page)
        assets.append(asset)
    db_session.commit()

    class _Candidate:
        id = "cand"
        generation_record_id = None
        model_alias = "image.test"
        resolution = "DRAFT_1K"

    monkeypatch.setattr(
        exports_module,
        "_selected_pages",
        lambda db, chapter: [(pages[i], _Candidate(), assets[i]) for i in range(len(pages))],
    )
    monkeypatch.setattr(
        exports_module,
        "_asset_path",
        lambda asset: get_settings().storage_root / asset.storage_key,
    )
    return project, chapter


def test_webtoon_export_writes_ordered_slices_and_manifest(
    client, db_session, tmp_path, monkeypatch
):
    project, chapter = _seed_chapter_with_pages(
        db_session, tmp_path, monkeypatch, [(1440, 2160), (1440, 2160)]
    )
    response = client.post(
        f"/api/v1/chapters/{chapter.id}/exports",
        json={
            "export_type": "WEBTOON",
            "width": 1080,
            "gap_px": 16,
            "max_slice_height": 4096,
        },
    )
    assert response.status_code == 201, response.json()
    bundle = db_session.get(ExportBundle, response.json()["id"])
    path = get_settings().storage_root / bundle.storage_key
    assert path.is_file()

    with zipfile.ZipFile(path) as archive:
        assert archive.namelist() == ["slice-0001.jpg", "manifest.json"]
        manifest = json.loads(archive.read("manifest.json"))
        pixels = archive.read("slice-0001.jpg")

    # 两页各缩放到 1620，加页间距 16 → 单片 3256。
    with Image.open(BytesIO(pixels)) as img:
        assert img.size == (1080, 3256)
        assert img.format == "JPEG"

    assert manifest["export_type"] == "WEBTOON"
    assert manifest["params"] == {
        "preset": "STANDARD",
        "width": 1080,
        "format": "JPEG",
        "quality": 88,
        "gap_px": 16,
        "max_slice_height": 4096,
    }
    assert manifest["page_count"] == 2
    assert manifest["total_height"] == 3256
    assert len(manifest["slices"]) == 1
    entry = manifest["slices"][0]
    assert entry["file"] == "slice-0001.jpg"
    assert entry["sha256"] == hashlib.sha256(pixels).hexdigest()
    assert entry["hard_cut"] is False
    assert [p["page_number"] for p in entry["pages"]] == [1, 2]
    assert entry["pages"][0]["dst_rect"] == [0, 0, 1080, 1620]
    assert entry["pages"][1]["dst_rect"] == [0, 1636, 1080, 1620]

    # 下载路径可用且 media type 正确。
    download = client.get(f"/api/v1/exports/{bundle.id}/download")
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("application/zip")


def test_webtoon_export_hard_cut_page_produces_multiple_slices(
    client, db_session, tmp_path, monkeypatch
):
    _, chapter = _seed_chapter_with_pages(
        db_session, tmp_path, monkeypatch, [(800, 800), (800, 3600)]
    )
    response = client.post(
        f"/api/v1/chapters/{chapter.id}/exports",
        json={"export_type": "WEBTOON", "width": 800, "gap_px": 0, "max_slice_height": 1000},
    )
    assert response.status_code == 201, response.json()
    bundle = db_session.get(ExportBundle, response.json()["id"])
    with zipfile.ZipFile(get_settings().storage_root / bundle.storage_key) as archive:
        names = archive.namelist()
        manifest = json.loads(archive.read("manifest.json"))
    # 第 1 页 800 高独占一片；第 2 页 3600 高硬切 4 段。
    assert names == [
        "slice-0001.jpg",
        "slice-0002.jpg",
        "slice-0003.jpg",
        "slice-0004.jpg",
        "slice-0005.jpg",
        "manifest.json",
    ]
    heights = [entry["height"] for entry in manifest["slices"]]
    assert heights == [800, 1000, 1000, 1000, 600]
    assert [entry["hard_cut"] for entry in manifest["slices"]] == [False] + [True] * 4
    page2_rects = [
        entry["pages"][0]["src_rect"]
        for entry in manifest["slices"]
        if entry["hard_cut"]
    ]
    assert page2_rects == [[0, 0, 800, 1000], [0, 1000, 800, 1000], [0, 2000, 800, 1000], [0, 3000, 800, 600]]


def test_webtoon_params_rejected_for_other_types(client, db_session):
    project = Project(name="参数门禁")
    db_session.add(project)
    db_session.flush()
    chapter = Chapter(project_id=project.id, title="c", ordinal=1)
    db_session.add(chapter)
    db_session.commit()
    response = client.post(
        f"/api/v1/chapters/{chapter.id}/exports",
        json={"export_type": "PNG", "width": 1080},
    )
    assert response.status_code == 422


def test_webtoon_export_missing_dimensions_is_409(
    client, db_session, tmp_path, monkeypatch
):
    project, chapter = _seed_chapter_with_pages(db_session, tmp_path, monkeypatch, [(800, 800)])
    asset = db_session.scalars(select(Asset)).first()
    asset.width = None
    db_session.commit()
    response = client.post(
        f"/api/v1/chapters/{chapter.id}/exports", json={"export_type": "WEBTOON"}
    )
    assert response.status_code == 409
    assert "尺寸" in response.json()["detail"]
