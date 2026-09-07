"""Single-image requests are pinned and multi-image responses are surfaced.

The product persists and inspects exactly one image per candidate, so a
request that would return several images only inflates the bill: extra_body
must not raise ``n``, the edit form pins ``n=1``, the Google config pins
``candidate_count=1``, and a provider that still returns multiple images
produces a warning while only the first image is persisted.
"""

import json

import pytest

import httpx

from app.model_adapters.base import ImageRequest
from app.model_adapters.compatible import CompatibleRuntime, OpenAICompatibleAdapter


@pytest.fixture
def storage_root(tmp_path, monkeypatch):

    from app.config import get_settings

    root = tmp_path / "storage"
    monkeypatch.setattr(get_settings(), "storage_root", root)
    return root


def _multipart_field(body: bytes, name: str) -> str | None:
    """Extract one url-encoded multipart form-data part's value."""

    text = body.decode("utf-8", errors="replace")
    marker = f'name="{name}"'
    start = text.find(marker)
    if start == -1:
        return None
    value_start = text.find("\r\n\r\n", start) + 4
    value_end = text.find("\r\n--", value_start)
    return text[value_start:value_end]


def _image_runtime(extra_capabilities: dict | None = None) -> CompatibleRuntime:
    return CompatibleRuntime(
        provider_name="OpenAI-compatible",
        protocol="OPENAI",
        base_url="https://images.example.com/v1",
        api_key="key",
        model_id="image-model",
        endpoint_templates={"images_generate": "/images/generations"},
        capabilities=extra_capabilities or {},
    )


def test_extra_body_cannot_raise_the_image_count():
    """T1 (failing-first): a configured extra_body n is dropped, n stays 1."""

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": [{"b64_json": "aGk="}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = OpenAICompatibleAdapter(
        _image_runtime({"extra_body": {"n": 3}}),
        client=client,
    )
    response = adapter.generate_page(ImageRequest(prompt="单图"))
    client.close()

    body = json.loads(requests[0].content)
    assert body["n"] == 1
    assert response.images == (b"hi",)


def test_images_edit_form_pins_n_to_one():
    """T2: the edit multipart form carries n=1."""

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": [{"b64_json": "aGk="}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    runtime = CompatibleRuntime(
        provider_name="OpenAI-compatible",
        protocol="OPENAI",
        base_url="https://images.example.com/v1",
        api_key="key",
        model_id="image-model",
        endpoint_templates={"images_edit": "/images/edits"},
    )
    adapter = OpenAICompatibleAdapter(runtime, client=client)
    adapter.generate_page(
        ImageRequest(
            prompt="编辑",
            reference_images=(b"ref",),
            reference_mime_types=("image/png",),
        )
    )
    client.close()

    assert _multipart_field(requests[0].content, "n") == "1"


def test_page_handler_warns_and_persists_only_first_image(
    db_session, storage_root, caplog, monkeypatch
):
    """T4: a multi-image response logs a warning; exactly one asset lands."""

    import io
    import logging

    from PIL import Image
    from sqlalchemy import select as sa_select

    from app.domain.states import PageStatus, Resolution
    from app.models import (
        Asset,
        Chapter,
        GenerationBatch,
        GenerationJob,
        JobStatus,
        MangaPage,
        PageCandidate,
        Project,
        utcnow,
    )
    from app.model_adapters.base import ModelResponse as MR
    from app.worker_tasks import _run_page_generate

    def _png(seed: int) -> bytes:
        out = io.BytesIO()
        Image.new("RGB", (8, 8), (seed, 10, 20)).save(out, format="PNG")
        return out.getvalue()

    project = Project(name="多图返回项目")
    db_session.add(project)
    db_session.flush()
    chapter = Chapter(project_id=project.id, title="第一章", ordinal=1)
    db_session.add(chapter)
    db_session.flush()
    page = MangaPage(
        chapter_id=chapter.id,
        page_number=1,
        storyboard_version=2,
        scene_ids=["scene"],
        beat_ids=["beat"],
        source_coverage={"complete": True},
        status=PageStatus.DRAFT_GENERATING,
    )
    db_session.add(page)
    db_session.flush()
    batch = GenerationBatch(
        project_id=project.id, chapter_id=chapter.id, page_id=page.id, ordinal=1
    )
    db_session.add(batch)
    db_session.flush()
    candidate = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=1,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        status="GENERATING",
        based_on_storyboard_version=2,
        job_id=None,
    )
    db_session.add(candidate)
    db_session.flush()
    job = GenerationJob(
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_GENERATE",
        status=JobStatus.PREPARING,
        model_alias="image.nano_banana_2",
    )
    db_session.add(job)
    db_session.flush()
    candidate.job_id = job.id
    db_session.commit()

    class TwoImageAdapter:
        def generate_page(self, request):
            return MR(
                model_id="image-model",
                request_id="req-multi",
                usage={"fake": True},
                images=(_png(10), _png(200)),
            )

    monkeypatch.setattr(
        "app.worker_tasks._adapter", lambda alias: TwoImageAdapter()
    )
    from datetime import timedelta

    job.lease_owner = "owner-multi"
    job.lease_expires_at = utcnow() + timedelta(minutes=5)
    db_session.info["job_id"] = job.id
    db_session.info["job_lease_owner"] = "owner-multi"
    job.attempt_count = 1
    db_session.commit()

    with caplog.at_level(
        logging.WARNING, logger="mangaflow.worker.page_generate"
    ):
        _run_page_generate(db_session, job)
    db_session.commit()

    warnings = [
        record
        for record in caplog.records
        if record.levelno >= logging.WARNING and "仅持久化第 1 张" in record.getMessage()
    ]
    assert warnings, "a multi-image response must produce an overflow warning"
    assets = list(db_session.scalars(sa_select(Asset)))
    assert len(assets) == 1, "only the first returned image may be persisted"
    db_session.expire_all()
    assert db_session.get(PageCandidate, candidate.id).asset_id == assets[0].id
