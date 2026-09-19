"""Regression: the NUI-6/7 acceptance seed must stay consumable by the API.

The real-machine download of a seeded export bundle failed with HTTP 500:
``download_export`` looks up ``media_types[bundle.export_type]``, and the seed
wrote the invented value ``PNG_ZIP`` (the request schema only accepts
PNG/PDF/JSON). A key error on the download path is invisible to the dataset
author because nothing in the offline suite read the seed through the API.
"""

import hashlib
import sys
import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from app.config import get_settings  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import ExportBundle  # noqa: E402
from nui67_seed import DATASET_TAG, seed_fixed_dataset  # noqa: E402

EXPORT_TYPES = {"PNG", "PDF", "JSON"}


@pytest.fixture
def seeded_client(tmp_path, monkeypatch):
    db_path = tmp_path / "acceptance.db"
    storage_root = tmp_path / "storage"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    seed_fixed_dataset(f"sqlite:///{db_path}", storage_root)

    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_db():
        with SessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(get_settings(), "storage_root", storage_root)
    with TestClient(app) as client:
        yield client, SessionLocal, storage_root
    app.dependency_overrides.clear()


def test_seeded_export_bundle_stays_inside_the_api_enum(seeded_client):
    _, SessionLocal, storage_root = seeded_client
    with SessionLocal() as session:
        bundles = list(session.scalars(select(ExportBundle)))
    assert len(bundles) == 1
    bundle = bundles[0]
    assert bundle.export_type in EXPORT_TYPES, (
        "the seed must only write export types the download route can serve; "
        "any other value raises KeyError inside download_export"
    )
    on_disk = (storage_root / bundle.storage_key).read_bytes()
    assert bundle.byte_size == len(on_disk)
    assert bundle.sha256 == hashlib.sha256(on_disk).hexdigest()


def test_seeded_export_bundle_downloads_and_opens(seeded_client):
    client, SessionLocal, storage_root = seeded_client
    with SessionLocal() as session:
        bundle = session.scalars(select(ExportBundle)).one()
        project_id = bundle.project_id

    response = client.get(
        f"/api/v1/exports/{bundle.id}/download", params={"project_id": project_id}
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/zip")
    assert response.headers["content-disposition"].endswith(
        f'{DATASET_TAG.lower()}-chapter1.zip"'
    )

    body = response.content
    assert len(body) == bundle.byte_size
    assert hashlib.sha256(body).hexdigest() == bundle.sha256
    assert bundle.page_count == 3

    with zipfile.ZipFile(BytesIO(body)) as archive:
        assert archive.testzip() is None
        assert archive.namelist() == [
            "chapter1/page-001.png",
            "chapter1/page-002.png",
            "chapter1/page-003.png",
        ]
        assert all(archive.read(name)[:8] == b"\x89PNG\r\n\x1a\n" for name in archive.namelist())
    assert (storage_root / bundle.storage_key).is_file()
