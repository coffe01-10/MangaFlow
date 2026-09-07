"""Regression: export artifacts carry safe names and unique destinations.

Two failure classes: zip members were built verbatim from
``asset.original_name`` — stored with backslashes on a POSIX host, they
extract outside the target directory on Windows (zip-slip) — and the
deterministic destination name meant every re-export overwrote the file
earlier bundle rows still point at (stale integrity metadata; on Windows
``os.replace`` onto a file being downloaded failed the new export).
"""

import zipfile
from io import BytesIO

from PIL import Image


from app.api.routes import exports as exports_module
from app.config import get_settings
from app.models import Asset, Chapter, Project


def test_safe_archive_name_flattens_separators_and_controls():
    assert exports_module._safe_archive_name("..\\..\\evil.png") == "evil.png"
    assert exports_module._safe_archive_name("/abs/path/x.png") == "x.png"
    assert exports_module._safe_archive_name("normal-1.png") == "normal-1.png"
    assert exports_module._safe_archive_name("  ") == "page.png"
    assert exports_module._safe_archive_name("a\x00b.png") == "ab.png"


def test_repeated_exports_get_distinct_files_and_both_download(
    client, db_session, monkeypatch, tmp_path
):
    from PIL import Image

    project = Project(name="export-names")
    db_session.add(project)
    db_session.flush()
    chapter = Chapter(project_id=project.id, title="c1", ordinal=1)
    db_session.add(chapter)
    db_session.commit()

    class _Page:
        page_number = 1
        id = "page-1"
        source_coverage = {}

    class _Candidate:
        id = "candidate-1"
        generation_record_id = None
        model_alias = "image.test"
        resolution = "DRAFT_1K"

    stored = tmp_path / "stored.png"
    Image.new("RGB", (8, 8), color="navy").save(stored, format="PNG")
    asset = Asset(
        project_id=project.id,
        kind="page_candidate",
        original_name="..\\..\\evil.png",
        storage_key="stored.png",
        mime_type="image/png",
        byte_size=stored.stat().st_size,
        sha256="a" * 64,
        source="AI_GENERATED",
        status="GENERATED",
    )
    db_session.add(asset)
    db_session.commit()

    monkeypatch.setattr(
        exports_module, "_selected_pages", lambda db, chapter: [(_Page(), _Candidate(), asset)]
    )
    monkeypatch.setattr(exports_module, "_asset_path", lambda asset: stored)

    first = client.post(
        f"/api/v1/chapters/{chapter.id}/exports", json={"export_type": "PNG"}
    )
    second = client.post(
        f"/api/v1/chapters/{chapter.id}/exports", json={"export_type": "PNG"}
    )
    assert first.status_code == 201 and second.status_code == 201

    from app.models import ExportBundle

    first_row = db_session.get(ExportBundle, first.json()["id"])
    second_row = db_session.get(ExportBundle, second.json()["id"])
    assert first_row is not None and second_row is not None
    assert (
        first_row.storage_key != second_row.storage_key
    ), "a re-export must not overwrite the first bundle"

    for payload in (first.json(), second.json()):
        downloaded = client.get(payload["download_url"])
        assert downloaded.status_code == 200
        with zipfile.ZipFile(BytesIO(downloaded.content)) as archive:
            (member_name,) = archive.namelist()
        assert "\\" not in member_name
        assert ".." not in member_name
        assert member_name.endswith("evil.png")


def test_export_retention_caps_bundles_per_chapter_and_type(
    client, db_session, monkeypatch, tmp_path
):
    """Old bundle rows and their files are pruned beyond the keep cap."""

    from datetime import UTC, datetime, timedelta

    from app.models import ExportBundle

    class _Page:
        page_number = 1
        id = "page-1"
        source_coverage = {}

    class _Candidate:
        id = "candidate-1"
        generation_record_id = None
        model_alias = "image.test"
        resolution = "DRAFT_1K"

    stored = tmp_path / "stored.png"
    Image.new("RGB", (8, 8), color="navy").save(stored, format="PNG")
    project = Project(name="export-retention")
    db_session.add(project)
    db_session.flush()
    chapter = Chapter(project_id=project.id, title="c1", ordinal=1)
    db_session.add(chapter)
    asset = Asset(
        project_id=project.id,
        kind="page_candidate",
        original_name="page.png",
        storage_key="stored.png",
        mime_type="image/png",
        byte_size=stored.stat().st_size,
        sha256="b" * 64,
        source="AI_GENERATED",
        status="GENERATED",
    )
    db_session.add(asset)
    db_session.commit()

    # Pre-existing old bundles that must be pruned once the cap is exceeded.
    exports_dir = tmp_path / "storage" / "exports" / project.id / chapter.id
    exports_dir.mkdir(parents=True, exist_ok=True)
    for index in range(25):
        bundle = ExportBundle(
            project_id=project.id,
            chapter_id=chapter.id,
            export_type="PNG",
            storage_key=f"exports/{project.id}/{chapter.id}/old-{index}.zip",
            byte_size=1,
            sha256="c" * 64,
            page_count=1,
            created_at=datetime.now(UTC) - timedelta(minutes=100 - index),
        )
        db_session.add(bundle)
        (exports_dir / f"old-{index}.zip").write_bytes(b"PK\x05\x06")
    db_session.commit()

    monkeypatch.setattr(
        exports_module, "_selected_pages", lambda db, chapter: [(_Page(), _Candidate(), asset)]
    )
    monkeypatch.setattr(exports_module, "_asset_path", lambda asset: stored)
    monkeypatch.setattr(get_settings(), "storage_root", tmp_path / "storage")

    created = client.post(
        f"/api/v1/chapters/{chapter.id}/exports", json={"export_type": "PNG"}
    )
    assert created.status_code == 201

    remaining = (
        db_session.query(ExportBundle)
        .filter(
            ExportBundle.chapter_id == chapter.id,
            ExportBundle.export_type == "PNG",
        )
        .all()
    )
    assert len(remaining) == 20
    # The fresh bundle survives; only the oldest were pruned with their files.
    assert any(row.id == created.json()["id"] for row in remaining)
    assert not (exports_dir / "old-0.zip").exists()
    assert (exports_dir / "old-24.zip").exists()
