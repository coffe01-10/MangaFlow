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


def test_seeded_workflow_draft_graph_is_canonical(seeded_client):
    """P4-3b 回归：seed 的工作流草稿图必须是 canonical v2 形状。

    旧形状（label/params 节点、source/target 边）GET 原样透传后，native 端
    加载出无名无端口的节点，且任何编辑触发的全量 PATCH 都被 WorkflowGraph
    校验 422 拒绝——native 工作流编辑器保存链整体瘫痪。
    """
    from app.services.workflow_engine.catalog import canonical_graph

    client, SessionLocal, _ = seeded_client
    with SessionLocal() as session:
        from app.models import WorkflowDefinition

        definition = session.scalars(select(WorkflowDefinition)).one()
        workflow_id = definition.id

    response = client.get(f"/api/v1/workflows/{workflow_id}")
    assert response.status_code == 200, response.text
    graph = response.json()["draft_graph"]

    # 能过 API 自身的 canonical 校验（与 PATCH 同一契约）
    canonical_graph(graph)
    assert graph["nodes"], "seed 必须带节点"
    for node in graph["nodes"]:
        assert node.get("name"), f"节点 {node.get('id')} 缺 name（native 渲染无标题、PATCH 422）"
    for edge in graph["edges"]:
        assert edge.get("source_node") and edge.get("target_node"), (
            f"边 {edge.get('id')} 缺 canonical 端点键，native 无法重建连线"
        )


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
