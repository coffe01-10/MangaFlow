from __future__ import annotations

import struct
import zlib
from io import BytesIO
from pathlib import Path

from PIL import Image
from sqlalchemy import select

from app.config import get_settings
from app.models import Asset


def _png_bytes(size: tuple[int, int] = (8, 8)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, "white").save(buffer, format="PNG")
    return buffer.getvalue()


def _png_header(width: int, height: int) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IEND", b"")


def _limit_uploads(monkeypatch, directory: Path) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "upload_root", directory)
    monkeypatch.setattr(settings, "max_upload_bytes", 128)
    monkeypatch.setattr(settings, "upload_form_overhead_bytes", 256)
    monkeypatch.setattr(settings, "max_image_pixels", 10_000)


def test_upload_rejects_oversized_file_before_persisting(client, monkeypatch, tmp_path):
    _limit_uploads(monkeypatch, tmp_path)
    project = client.post("/api/v1/projects", json={"name": "超限上传"}).json()
    response = client.post(
        "/api/v1/assets/upload",
        data={"project_id": project["id"], "kind": "character"},
        files={"file": ("big.png", b"x" * 4096, "image/png")},
    )
    assert response.status_code == 413
    assert list(tmp_path.rglob("*.png")) == []
    assert client.get("/api/v1/assets", params={"project_id": project["id"]}).json() == []


def test_upload_rejects_extra_file_field(client, monkeypatch, tmp_path):
    _limit_uploads(monkeypatch, tmp_path)
    project = client.post("/api/v1/projects", json={"name": "多余文件"}).json()
    response = client.post(
        "/api/v1/assets/upload",
        data={"project_id": project["id"], "kind": "character"},
        files=[
            ("file", ("ok.png", _png_bytes(), "image/png")),
            ("extra", ("noise.bin", b"n" * 4096, "application/octet-stream")),
        ],
    )
    assert response.status_code in {413, 422}
    assert list(tmp_path.rglob("*.png")) == []
    assert client.get("/api/v1/assets", params={"project_id": project["id"]}).json() == []


def test_upload_rejects_chunked_oversized_body(client, monkeypatch, tmp_path):
    _limit_uploads(monkeypatch, tmp_path)
    project = client.post("/api/v1/projects", json={"name": "分块上传"}).json()
    boundary = "----MangaFlowLimit"
    payload = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="project_id"\r\n\r\n'
        f"{project['id']}\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="kind"\r\n\r\n'
        "character\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="big.bin"\r\n'
        "Content-Type: image/png\r\n\r\n"
    ).encode("utf-8") + (b"x" * 4096) + f"\r\n--{boundary}--\r\n".encode("ascii")

    def chunks():
        yield payload

    response = client.post(
        "/api/v1/assets/upload",
        headers={"content-type": f"multipart/form-data; boundary={boundary}"},
        content=chunks(),
    )
    assert response.status_code == 413
    assert list(tmp_path.rglob("*")) == [] or all(
        item.is_dir() for item in tmp_path.rglob("*")
    )


def test_upload_rejects_decompression_bomb_and_cleans_files(
    client, monkeypatch, tmp_path, db_session
):
    settings = get_settings()
    monkeypatch.setattr(settings, "upload_root", tmp_path)
    monkeypatch.setattr(settings, "max_image_pixels", 10_000)
    project = client.post("/api/v1/projects", json={"name": "解压炸弹"}).json()
    bomb = _png_header(100_000, 100_000)
    response = client.post(
        "/api/v1/assets/upload",
        data={"project_id": project["id"], "kind": "character"},
        files={"file": ("bomb.png", bomb, "image/png")},
    )
    assert response.status_code == 422
    assert "像素" in response.json()["detail"]
    assert list(tmp_path.rglob("*.png")) == []
    assert list(db_session.scalars(select(Asset))) == []


def test_normal_upload_still_registers_small_png(client, monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "upload_root", tmp_path)
    project = client.post("/api/v1/projects", json={"name": "正常上传"}).json()
    response = client.post(
        "/api/v1/assets/upload",
        data={"project_id": project["id"], "kind": "character"},
        files={"file": ("hero.png", _png_bytes((16, 12)), "image/png")},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["width"] == 16
    assert body["height"] == 12
    assert body["mime_type"] == "image/png"
    assert list(tmp_path.rglob("*.png"))
