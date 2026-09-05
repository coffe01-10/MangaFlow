"""Adapter error/output classification contract (issues #121 / #151).

Refusal-shaped HTTP 200 bodies must surface as CONTENT_POLICY so the designed
per-segment split-retry engages; schema near-misses are deterministic
(INVALID_OUTPUT, non-retryable) on every adapter; JSON decode failures
(gateway HTML pages, truncated bodies) stay retryable.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from pydantic import BaseModel

from app.config import Settings
from app.model_adapters.base import (
    MultimodalRequest,
    ProviderAdapterError,
    StructuredRequest,
)
from app.model_adapters.compatible import (
    AnthropicCompatibleAdapter,
    CompatibleRuntime,
    OpenAICompatibleAdapter,
)
from app.model_adapters.google import GoogleTextAdapter
from app.model_adapters.vertex import VertexAdapterError, VertexTextAdapter
from app.services.model_registry import build_registry


class SmokeReply(BaseModel):
    ok: bool


def _vertex_text_adapter() -> VertexTextAdapter:
    settings = Settings(
        google_cloud_project="test-project",
        google_application_credentials=Path(__file__),
    )
    return VertexTextAdapter(settings, build_registry(settings)["text.fast"])


def test_vertex_translate_error_forwards_retryable_flag():
    class QuotaError(Exception):
        status_code = 429

    error = VertexTextAdapter._translate_error(QuotaError("quota exceeded"))
    assert error.code == "RATE_LIMIT"
    assert error.retryable is True


def test_vertex_blocked_response_maps_to_content_policy(monkeypatch):
    adapter = _vertex_text_adapter()
    blocked = SimpleNamespace(
        candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name="SAFETY"))],
        prompt_feedback=None,
    )
    monkeypatch.setattr(adapter, "_execute", lambda operation: blocked)
    with pytest.raises(VertexAdapterError) as excinfo:
        adapter.generate_structured(StructuredRequest(prompt="x"), SmokeReply)
    assert excinfo.value.code == "CONTENT_POLICY"
    assert excinfo.value.retryable is False


def test_vertex_prompt_block_maps_to_content_policy(monkeypatch):
    adapter = _vertex_text_adapter()
    blocked = SimpleNamespace(
        candidates=[],
        prompt_feedback=SimpleNamespace(block_reason=SimpleNamespace(name="SAFETY")),
    )
    monkeypatch.setattr(adapter, "_execute", lambda operation: blocked)
    with pytest.raises(VertexAdapterError) as excinfo:
        adapter.generate_structured(StructuredRequest(prompt="x"), SmokeReply)
    assert excinfo.value.code == "CONTENT_POLICY"


def test_vertex_schema_near_miss_is_terminal(monkeypatch):
    adapter = _vertex_text_adapter()
    response = SimpleNamespace(
        candidates=[],
        prompt_feedback=None,
        text='{"ok": "not-a-bool"}',
    )
    monkeypatch.setattr(adapter, "_execute", lambda operation: response)
    with pytest.raises(VertexAdapterError) as excinfo:
        adapter.generate_structured(StructuredRequest(prompt="x"), SmokeReply)
    assert excinfo.value.code == "INVALID_OUTPUT"
    assert excinfo.value.retryable is False


def test_vertex_invalid_json_is_retryable(monkeypatch):
    adapter = _vertex_text_adapter()
    response = SimpleNamespace(candidates=[], prompt_feedback=None, text="<html>oops")
    monkeypatch.setattr(adapter, "_execute", lambda operation: response)
    with pytest.raises(VertexAdapterError) as excinfo:
        adapter.generate_structured(StructuredRequest(prompt="x"), SmokeReply)
    assert excinfo.value.code == "INVALID_OUTPUT"
    assert excinfo.value.retryable is True


def _runtime(**overrides) -> CompatibleRuntime:
    base = dict(
        provider_name="OpenAI",
        protocol="OPENAI",
        base_url="https://api.provider.test/v1",
        api_key="key",
        model_id="m",
        endpoint_templates={
            "chat": "/chat/completions",
            "responses": "/responses",
            "messages": "/v1/messages",
        },
    )
    base.update(overrides)
    return CompatibleRuntime(**base)


def _chat_adapter(handler) -> OpenAICompatibleAdapter:
    return OpenAICompatibleAdapter(
        _runtime(), client=httpx.Client(transport=httpx.MockTransport(handler))
    )


def _chat_body(content, finish_reason="stop", refusal=None):
    message = {"content": content}
    if refusal is not None:
        message["refusal"] = refusal
    return {"choices": [{"finish_reason": finish_reason, "message": message}]}


def test_compatible_content_filter_refusal_maps_to_content_policy():
    adapter = _chat_adapter(
        lambda request: httpx.Response(
            200, json=_chat_body(None, finish_reason="content_filter"), request=request
        )
    )
    with pytest.raises(ProviderAdapterError) as excinfo:
        adapter.generate_structured(StructuredRequest(prompt="x"), SmokeReply)
    assert excinfo.value.code == "CONTENT_POLICY"
    assert excinfo.value.retryable is False


def test_compatible_refusal_field_maps_to_content_policy():
    adapter = _chat_adapter(
        lambda request: httpx.Response(
            200,
            json=_chat_body("部分内容", refusal="cannot help"),
            request=request,
        )
    )
    with pytest.raises(ProviderAdapterError) as excinfo:
        adapter.generate_structured(StructuredRequest(prompt="x"), SmokeReply)
    assert excinfo.value.code == "CONTENT_POLICY"


def test_compatible_responses_api_refusal_maps_to_content_policy():
    adapter = OpenAICompatibleAdapter(
        _runtime(use_responses_api=True),
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "output": [
                            {"content": [{"type": "refusal", "refusal": "no"}]}
                        ]
                    },
                    request=request,
                )
            )
        ),
    )
    with pytest.raises(ProviderAdapterError) as excinfo:
        adapter.generate_structured(StructuredRequest(prompt="x"), SmokeReply)
    assert excinfo.value.code == "CONTENT_POLICY"


def test_compatible_fenced_json_is_repaired():
    adapter = _chat_adapter(
        lambda request: httpx.Response(
            200, json=_chat_body("```json\n{\"ok\": true}\n```"), request=request
        )
    )
    assert adapter.generate_structured(StructuredRequest(prompt="x"), SmokeReply).ok


def test_compatible_schema_near_miss_is_terminal():
    adapter = _chat_adapter(
        lambda request: httpx.Response(
            200, json=_chat_body('{"ok": "definitely-not-a-bool"}'), request=request
        )
    )
    with pytest.raises(ProviderAdapterError) as excinfo:
        adapter.generate_structured(StructuredRequest(prompt="x"), SmokeReply)
    assert excinfo.value.code == "INVALID_OUTPUT"
    assert excinfo.value.retryable is False


def test_compatible_html_body_on_200_is_retryable():
    adapter = _chat_adapter(
        lambda request: httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=b"<html>gateway error page</html>",
            request=request,
        )
    )
    with pytest.raises(ProviderAdapterError) as excinfo:
        adapter.generate_structured(StructuredRequest(prompt="x"), SmokeReply)
    assert excinfo.value.code == "INVALID_OUTPUT"
    assert excinfo.value.retryable is True


def test_compatible_multimodal_decode_failure_is_retryable():
    adapter = _chat_adapter(
        lambda request: httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=b"<html>proxy page</html>",
            request=request,
        )
    )
    request = MultimodalRequest(
        prompt="inspect",
        images=(b"image-bytes",),
        mime_types=("image/png",),
    )
    with pytest.raises(ProviderAdapterError) as excinfo:
        adapter.analyze_multimodal(request, SmokeReply)
    assert excinfo.value.code == "INVALID_OUTPUT"
    assert excinfo.value.retryable is True


def test_anthropic_refusal_stop_reason_maps_to_content_policy():
    adapter = AnthropicCompatibleAdapter(
        _runtime(protocol="ANTHROPIC"),
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={"stop_reason": "refusal", "content": []},
                    request=request,
                )
            )
        ),
    )
    with pytest.raises(ProviderAdapterError) as excinfo:
        adapter.generate_structured(StructuredRequest(prompt="x"), SmokeReply)
    assert excinfo.value.code == "CONTENT_POLICY"


def test_google_schema_near_miss_is_terminal_and_decode_retryable(monkeypatch):
    from app.model_adapters.google import GoogleRuntime

    adapter = GoogleTextAdapter(
        GoogleRuntime(api_key="k", model_id="m", display_name="m")
    )

    near_miss = SimpleNamespace(text='{"ok": "not-a-bool"}')
    monkeypatch.setattr(adapter, "_execute", lambda operation: near_miss)
    with pytest.raises(ProviderAdapterError) as excinfo:
        adapter.generate_structured(StructuredRequest(prompt="x"), SmokeReply)
    assert excinfo.value.code == "INVALID_OUTPUT"
    assert excinfo.value.retryable is False

    broken = SimpleNamespace(text="<html>oops")
    monkeypatch.setattr(adapter, "_execute", lambda operation: broken)
    with pytest.raises(ProviderAdapterError) as excinfo:
        adapter.generate_structured(StructuredRequest(prompt="x"), SmokeReply)
    assert excinfo.value.code == "INVALID_OUTPUT"
    assert excinfo.value.retryable is True


def test_story_parse_rewrap_preserves_retryable_flag(client, db_session, monkeypatch):
    """The multi-chunk re-wrap must forward retryable (issue #121 instance 2)."""

    from app.models import Chapter, GenerationJob, SourceSegment
    from app.services.worker_handlers.story_parse import _run_story_parse

    project = client.post(
        "/api/v1/projects", json={"name": "解析重包装保真"}
    ).json()
    imported = client.post(
        f"/api/v1/projects/{project['id']}/sources/import",
        json={"title": "第一章", "text": "顾川推开门。\n\n他看向窗边。"},
    ).json()
    chapter = db_session.get(Chapter, imported["chapters"][0]["id"])
    segments = (
        db_session.query(SourceSegment)
        .filter(SourceSegment.source_revision_id == chapter.current_source_revision_id)
        .all()
    )

    class FlakyAdapter:
        def generate_structured(self, request, schema):
            raise ProviderAdapterError(
                "RATE_LIMIT", "上游限流", retryable=True, retry_after_seconds=30
            )

    monkeypatch.setattr("app.worker_tasks._adapter", lambda alias: FlakyAdapter())
    job = GenerationJob(
        project_id=project["id"],
        target_type="CHAPTER",
        target_id=chapter.id,
        job_type="SOURCE_PARSE",
        status="PREPARING",
        model_alias="text.fast",
    )
    db_session.add(job)
    db_session.flush()

    with pytest.raises(ProviderAdapterError) as excinfo:
        _run_story_parse(db_session, job)

    assert excinfo.value.code == "RATE_LIMIT"
    assert excinfo.value.retryable is True
    assert excinfo.value.retry_after_seconds == 30
