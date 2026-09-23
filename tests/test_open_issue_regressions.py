"""Offline regressions for provider responses, image options, and page exports."""

import base64
import gzip
import io
import json
import zipfile

import httpx
import pytest
from app.config import get_settings
from app.model_adapters.base import ImageRequest, ProviderAdapterError
from app.model_adapters.compatible import CompatibleRuntime, OpenAICompatibleAdapter
from app.models import Asset
from app.services.worker_handlers.page_generate import _save_generated_asset
from PIL import Image
from test_export_node_idempotency import _seed_export_ready_chapter


def _runtime(extra_body=None, *, max_response_bytes=4_000_000):
    return CompatibleRuntime(
        provider_name="offline",
        protocol="OPENAI",
        base_url="https://example.invalid/v1",
        api_key="offline",
        model_id="image-test",
        endpoint_templates={
            "images_generate": "images/generations",
            "images_edit": "images/edits",
        },
        capabilities={"extra_body": extra_body or {}},
        max_response_bytes=max_response_bytes,
    )


@pytest.mark.parametrize("compressed", [False, True])
def test_provider_response_decoded_once(compressed):
    body = json.dumps({"data": [{"b64_json": base64.b64encode(b"offline").decode()}]}).encode()
    calls = []

    def handler(request):
        calls.append(request)
        wire = gzip.compress(body) if compressed else body
        headers = {"content-type": "application/json", "content-length": str(len(wire))}
        if compressed:
            headers["content-encoding"] = "gzip"
        return httpx.Response(200, headers=headers, stream=httpx.ByteStream(wire))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = OpenAICompatibleAdapter(_runtime(), client=client).generate_page(
            ImageRequest(prompt="test")
        )
    assert result.images == (b"offline",)
    assert len(calls) == 1


def test_provider_response_enforces_decompressed_limit():
    body = json.dumps({"data": [{"b64_json": "a" * 4000}]}).encode()

    def handler(_request):
        return httpx.Response(
            200,
            headers={"content-encoding": "gzip"},
            stream=httpx.ByteStream(gzip.compress(body)),
        )

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(ProviderAdapterError) as raised,
    ):
        OpenAICompatibleAdapter(_runtime(max_response_bytes=200), client=client).generate_page(
            ImageRequest(prompt="test")
        )
    assert raised.value.code == "INVALID_OUTPUT"


def test_image_edit_sends_scalar_options_and_keeps_one_image():
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json={"data": [{"b64_json": "aGk="}]})

    extras = {"quality": "high", "output_format": "jpeg", "n": 9, "moderation": False}
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        adapter = OpenAICompatibleAdapter(_runtime(extras), client=client)
        adapter.generate_page(ImageRequest(prompt="test"))
        adapter.generate_page(
            ImageRequest(
                prompt="test",
                reference_images=(b"reference",),
                reference_mime_types=("image/png",),
            )
        )
    plain = json.loads(captured[0].content)
    assert plain["quality"] == "high" and plain["output_format"] == "jpeg"
    assert plain["n"] == 1
    edited = captured[1].content
    for field, value in (("quality", "high"), ("output_format", "jpeg"), ("moderation", "false"), ("n", "1")):
        assert f'name="{field}"'.encode() in edited
        assert value.encode() in edited
    assert b'name="image[]"' in edited


def test_image_edit_rejects_nested_form_option():
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200))) as client:
        adapter = OpenAICompatibleAdapter(_runtime({"custom": {"nested": 1}}), client=client)
        with pytest.raises(ProviderAdapterError) as raised:
            adapter.generate_page(
                ImageRequest(
                    prompt="test",
                    reference_images=(b"reference",),
                    reference_mime_types=("image/png",),
                )
            )
    assert raised.value.code == "INVALID_INPUT"


@pytest.mark.parametrize(
    ("image_format", "mime", "suffix"),
    [("PNG", "image/png", "png"), ("JPEG", "image/jpeg", "jpg"), ("WEBP", "image/webp", "webp")],
)
def test_generated_page_keeps_format_through_storage_and_exports(
    client, db_session, monkeypatch, tmp_path, image_format, mime, suffix
):
    settings = get_settings()
    monkeypatch.setattr(settings, "storage_root", tmp_path / "storage")
    monkeypatch.setattr(settings, "upload_root", tmp_path / "uploads")
    seeded = _seed_export_ready_chapter(db_session)
    stream = io.BytesIO()
    Image.new("RGB", (16, 16), "red").save(stream, format=image_format)
    data = stream.getvalue()
    asset = _save_generated_asset(db_session, seeded["candidate"], data)
    seeded["candidate"].asset_id = asset.id
    db_session.commit()

    assert asset.mime_type == mime
    assert asset.original_name.endswith(f".{suffix}")
    assert asset.storage_key.endswith(f".{suffix}")
    assert (settings.storage_root / asset.storage_key).read_bytes() == data

    single = client.get(f"/api/v1/pages/{seeded['page'].id}/export.png")
    assert single.status_code == 200
    assert single.headers["content-type"].startswith(mime)
    assert f".{suffix}" in single.headers["content-disposition"]
    assert Image.open(io.BytesIO(single.content)).format == image_format

    response = client.post(
        f"/api/v1/chapters/{seeded['chapter'].id}/exports", json={"export_type": "PNG"}
    )
    assert response.status_code == 201, response.text
    archive_response = client.get(response.json()["download_url"])
    with zipfile.ZipFile(io.BytesIO(archive_response.content)) as archive:
        name, = archive.namelist()
        assert name.endswith(f".{suffix}")
        assert Image.open(io.BytesIO(archive.read(name))).format == image_format


def test_legacy_jpeg_png_name_is_corrected_in_zip(client, db_session, monkeypatch, tmp_path):
    settings = get_settings()
    monkeypatch.setattr(settings, "storage_root", tmp_path / "storage")
    seeded = _seed_export_ready_chapter(db_session)
    asset = db_session.get(Asset, seeded["candidate"].asset_id)
    stream = io.BytesIO()
    Image.new("RGB", (8, 8), "blue").save(stream, format="JPEG")
    path = settings.storage_root / asset.storage_key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(stream.getvalue())
    asset.mime_type = "image/jpeg"
    asset.source = "AI_GENERATED"
    db_session.commit()
    response = client.post(
        f"/api/v1/chapters/{seeded['chapter'].id}/exports", json={"export_type": "PNG"}
    )
    assert response.status_code == 201, response.text
    with zipfile.ZipFile(io.BytesIO(client.get(response.json()["download_url"]).content)) as archive:
        assert archive.namelist()[0].endswith(".jpg")


def test_generated_page_rejects_unsupported_image_without_writing(db_session, monkeypatch, tmp_path):
    settings = get_settings()
    monkeypatch.setattr(settings, "storage_root", tmp_path / "storage")
    seeded = _seed_export_ready_chapter(db_session)
    stream = io.BytesIO()
    Image.new("RGB", (8, 8), "green").save(stream, format="GIF")
    with pytest.raises(ProviderAdapterError) as raised:
        _save_generated_asset(db_session, seeded["candidate"], stream.getvalue())
    assert raised.value.code == "INVALID_OUTPUT"
    assert not (settings.storage_root / "generated").exists()
