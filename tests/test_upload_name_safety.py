"""Regression: upload names are sanitized and corrupt payloads clean up.

Three failure classes: stored filenames kept backslashes/control
characters (they flow into zip members and Content-Disposition), filenames
longer than the DB column turned a valid upload into 文件保存失败 after the
file was fully written, and a corrupt PNG (valid header, broken chunk CRC)
made Pillow raise SyntaxError which escaped upload handling as a 500 and
left the file orphaned on disk.
"""

from __future__ import annotations

import struct
import zlib
from io import BytesIO

from PIL import Image

from app.config import get_settings
from app.services.media import sanitize_stored_filename


def _png_bytes(size: tuple[int, int] = (8, 8)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, "white").save(buffer, format="PNG")
    return buffer.getvalue()


def _corrupt_crc_png() -> bytes:
    """PNG with a valid header but a broken IDAT checksum."""

    def chunk(tag: bytes, data: bytes, crc: int) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00" + b"\xff" * 8 * 8)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr, zlib.crc32(b"IHDR" + ihdr) & 0xFFFFFFFF)
        + chunk(b"IDAT", idat, 0xDEADBEEF)
        + chunk(b"IEND", b"", zlib.crc32(b"IEND") & 0xFFFFFFFF)
    )


def _uploads(monkeypatch, tmp_path) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "upload_root", tmp_path / "uploads")
    monkeypatch.setattr(settings, "max_upload_bytes", 20 * 1024 * 1024)
    monkeypatch.setattr(settings, "upload_form_overhead_bytes", 4096)
    monkeypatch.setattr(settings, "max_image_pixels", 10_000_000)


def test_corrupt_crc_upload_is_422_and_leaves_no_file(client, monkeypatch, tmp_path):
    _uploads(monkeypatch, tmp_path)
    project = client.post("/api/v1/projects", json={"name": "损坏CRC"}).json()
    response = client.post(
        "/api/v1/assets/upload",
        data={"project_id": project["id"], "kind": "character"},
        files={"file": ("corrupt.png", _corrupt_crc_png(), "image/png")},
    )
    assert response.status_code == 422
    assert list((tmp_path / "uploads").rglob("*.png")) == []


def test_hostile_filename_is_sanitized_before_storage(client, monkeypatch, tmp_path):
    _uploads(monkeypatch, tmp_path)
    project = client.post("/api/v1/projects", json={"name": "恶意文件名"}).json()
    hostile = "..\\..\\..\\windows\\evil .png "
    response = client.post(
        "/api/v1/assets/upload",
        data={"project_id": project["id"], "kind": "character"},
        files={"file": (hostile, _png_bytes(), "image/png")},
    )
    assert response.status_code == 201
    stored_name = response.json()["original_name"]
    assert "\\" not in stored_name
    assert ".." not in stored_name
    assert stored_name == "evil .png"


def test_sanitize_stored_filename_bounds():
    assert sanitize_stored_filename("..\\..\\evil.png") == "evil.png"
    assert sanitize_stored_filename("a\x00b.png") == "ab.png"
    assert sanitize_stored_filename("name . png . ") == "name . png"
    assert sanitize_stored_filename("   ") == "upload"
    assert sanitize_stored_filename("x" * 400, max_length=255) == "x" * 255
    assert sanitize_stored_filename("inline/name.png") == "name.png"
    assert sanitize_stored_filename("keep.png", default="page.png") == "keep.png"
    assert sanitize_stored_filename("  ", default="page.png") == "page.png"


def test_sanitizer_neutralizes_ads_and_truncation_edges():
    # A colon would create an NTFS alternate data stream.
    assert sanitize_stored_filename("file.png:ads") == "file.pngads"
    assert sanitize_stored_filename("C:name.png") == "Cname.png"
    # Truncation must not re-create the trailing dot/space it strips.
    assert not sanitize_stored_filename("a" * 254 + "." + "z").endswith((".", " "))
    assert not sanitize_stored_filename("a" * 254 + " " + "z").endswith((".", " "))
    truncated = sanitize_stored_filename("b" * 254 + ". ")
    assert truncated == "b" * 254
