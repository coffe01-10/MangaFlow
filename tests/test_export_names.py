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


from app.api.routes import exports as exports_module
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
