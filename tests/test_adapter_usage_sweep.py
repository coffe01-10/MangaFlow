"""Red-team defect regressions for issues #204/#205/#207/#209/#243.

Covers the provider-usage plumbing sweep (#204), truncation classification
(#205), image b64/download-usage billing (#207), the ledger key-set and
Vertex credential-manager retry fixes (#209), and the settings/provider
cluster (#243). Truncation-finish-reason classification tests for the three
compatible protocols and the Google blocked-shape tests live in
``test_adapter_error_classification.py`` alongside the original contract.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from pydantic import BaseModel

from app.config import Settings, get_settings
from app.model_adapters.base import (
    ImageRequest,
    ModelResponse,
    MultimodalRequest,
    ProviderAdapterError,
    StructuredRequest,
    attach_provider_usage,
)
from app.model_adapters.compatible import (
    AnthropicCompatibleAdapter,
    CompatibleRuntime,
    OpenAICompatibleAdapter,
)
from app.models import (
    AIModel,
    AppSetting,
    GenerationJob,
    ModelCallAttempt,
    ProviderConnection,
    ProviderKey,
    ProviderProfile,
)
from app.services.credential_crypto import (
    CredentialDecryptError,
    encrypt_secret,
    select_provider_key,
)
from app.services.model_costs import _normalized_usage
from app.services.model_registry import build_registry
from app.services.provider_catalog import (
    connection_config_fingerprint,
    update_connection,
)
from app.services.provider_errors import (
    CREDENTIAL_DECRYPT_FAILED,
    OUTPUT_TRUNCATED,
)
from app.services.provider_presets import ensure_provider_presets
from app.services.runtime_settings import update_runtime_settings
from app.services.usage_ledger import normalize_usage
from app.services.vertex_credentials import (
    VertexCredentialManager,
    classify_vertex_failure,
)
from app.settings_schemas import RuntimeSettingsUpdate
from app.provider_schemas import ConnectionUpdate


class SmokeReply(BaseModel):
    ok: bool


# ---------------------------------------------------------------------------
# Shared fixtures/helpers
# ---------------------------------------------------------------------------


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
            "images_generate": "/images/generations",
        },
    )
    base.update(overrides)
    return CompatibleRuntime(**base)


def _chat_adapter(handler, **runtime_overrides) -> OpenAICompatibleAdapter:
    return OpenAICompatibleAdapter(
        _runtime(**runtime_overrides),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def _anthropic_adapter(handler) -> AnthropicCompatibleAdapter:
    return AnthropicCompatibleAdapter(
        _runtime(protocol="ANTHROPIC"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


class _UsageMetadata:
    """google-genai-shaped usage metadata stub."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def model_dump(self, exclude_none: bool = False) -> dict:
        return dict(self._payload)


# ---------------------------------------------------------------------------
# #204: text adapters structurally drop provider usage
# ---------------------------------------------------------------------------


def test_chat_text_result_carries_provider_usage():
    adapter = _chat_adapter(
        lambda request: httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "stop", "message": {"content": '{"ok": true}'}}
                ],
                "usage": {"prompt_tokens": 11, "completion_tokens": 7},
            },
            request=request,
        )
    )
    result = adapter.generate_structured(StructuredRequest(prompt="x"), SmokeReply)
    assert result.ok
    assert getattr(result, "provider_usage", None) == {
        "prompt_tokens": 11,
        "completion_tokens": 7,
    }


def test_responses_text_result_carries_provider_usage():
    adapter = _chat_adapter(
        lambda request: httpx.Response(
            200,
            json={
                "output": [
                    {"content": [{"type": "output_text", "text": '{"ok": true}'}]}
                ],
                "usage": {"input_tokens": 5, "output_tokens": 6},
            },
            request=request,
        ),
        use_responses_api=True,
    )
    result = adapter.generate_structured(StructuredRequest(prompt="x"), SmokeReply)
    assert getattr(result, "provider_usage", None) == {
        "input_tokens": 5,
        "output_tokens": 6,
    }


def test_anthropic_text_result_carries_provider_usage():
    adapter = _anthropic_adapter(
        lambda request: httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": '{"ok": true}'}],
                "usage": {"input_tokens": 4, "output_tokens": 9},
            },
            request=request,
        )
    )
    result = adapter.generate_structured(StructuredRequest(prompt="x"), SmokeReply)
    assert getattr(result, "provider_usage", None) == {
        "input_tokens": 4,
        "output_tokens": 9,
    }


def test_chat_multimodal_result_carries_provider_usage():
    adapter = _chat_adapter(
        lambda request: httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "stop", "message": {"content": '{"ok": true}'}}
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 3},
            },
            request=request,
        )
    )
    request = MultimodalRequest(
        prompt="inspect", images=(b"img",), mime_types=("image/png",)
    )
    result = adapter.analyze_multimodal(request, SmokeReply)
    assert getattr(result, "provider_usage", None) == {
        "prompt_tokens": 12,
        "completion_tokens": 3,
    }


def test_vertex_text_result_carries_provider_usage(monkeypatch):
    from app.model_adapters.vertex import VertexTextAdapter

    settings = Settings(
        google_cloud_project="test-project",
        google_application_credentials=Path(__file__),
    )
    adapter = VertexTextAdapter(settings, build_registry(settings)["text.fast"])
    response = SimpleNamespace(
        candidates=[],
        prompt_feedback=None,
        text='{"ok": true}',
        usage_metadata=_UsageMetadata(
            {"prompt_token_count": 8, "candidates_token_count": 6}
        ),
    )
    monkeypatch.setattr(adapter, "_execute", lambda operation: response)
    result = adapter.generate_structured(StructuredRequest(prompt="x"), SmokeReply)
    assert getattr(result, "provider_usage", None) == {
        "prompt_token_count": 8,
        "candidates_token_count": 6,
    }


def test_google_text_result_carries_provider_usage(monkeypatch):
    from app.model_adapters.google import GoogleRuntime, GoogleTextAdapter

    adapter = GoogleTextAdapter(
        GoogleRuntime(api_key="k", model_id="m", display_name="m")
    )
    response = SimpleNamespace(
        candidates=[],
        prompt_feedback=None,
        text='{"ok": true}',
        usage_metadata=_UsageMetadata(
            {"prompt_token_count": 8, "candidates_token_count": 6}
        ),
    )
    monkeypatch.setattr(adapter, "_execute", lambda operation: response)
    result = adapter.generate_structured(StructuredRequest(prompt="x"), SmokeReply)
    assert getattr(result, "provider_usage", None) == {
        "prompt_token_count": 8,
        "candidates_token_count": 6,
    }


def test_usage_absent_result_has_no_provider_usage():
    adapter = _chat_adapter(
        lambda request: httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"ok": true}'}}]},
            request=request,
        )
    )
    result = adapter.generate_structured(StructuredRequest(prompt="x"), SmokeReply)
    assert getattr(result, "provider_usage", None) is None


def test_attach_provider_usage_ignores_empty_and_non_dict():
    result = SmokeReply(ok=True)
    attach_provider_usage(result, {})
    attach_provider_usage(result, None)
    attach_provider_usage(result, "not-a-dict")  # type: ignore[arg-type]
    assert getattr(result, "provider_usage", None) is None


def _job_binding(db_session, adapter):
    """A minimal job context + fake binding for _invoke_provider tests."""

    from app.models import Chapter, Project
    from app.services.model_router import AdapterBinding, ResolvedModel
    from app.services.worker_handlers.provider import _invoke_provider

    project = Project(name="Usage Sweep Project")
    db_session.add(project)
    db_session.flush()
    chapter = Chapter(project_id=project.id, title="第一章", ordinal=0)
    db_session.add(chapter)
    db_session.flush()
    job = GenerationJob(
        project_id=project.id,
        target_type="CHAPTER",
        target_id=chapter.id,
        job_type="SOURCE_PARSE",
        status="PREPARING",
        attempt_count=1,
        model_alias="text.fast",
    )
    db_session.add(job)
    db_session.flush()
    db_session.info["job_id"] = job.id
    resolved = ResolvedModel(
        # Scalar ids stay None: the audit row's catalog_model_id/connection_id
        # columns carry FKs, and SimpleNamespace fakes have no catalog rows.
        model=SimpleNamespace(
            id=None,
            provider_model_id="provider-model",
            operations=["structured_text"],
        ),
        connection=SimpleNamespace(id=None, protocol="OPENAI"),
        provider=SimpleNamespace(preset_key="openai", name="OpenAI"),
    )
    binding = AdapterBinding(resolved=resolved, adapter=adapter, selected_key=None)

    def run(callback):
        return _invoke_provider(db_session, binding, callback)

    return job, run


def test_invoke_provider_records_text_usage_on_success(db_session):
    class _TextAdapter:
        def generate(self, adapter):
            raise AssertionError("unused")

    job, run = _job_binding(db_session, _TextAdapter())
    reply = SmokeReply(ok=True)
    attach_provider_usage(reply, {"prompt_tokens": 10, "completion_tokens": 20})
    result = run(lambda adapter: reply)
    assert result is reply
    attempt = db_session.query(ModelCallAttempt).filter_by(job_id=job.id).one()
    assert attempt.outcome == "SUCCEEDED"
    assert attempt.usage == {"prompt_tokens": 10, "completion_tokens": 20}
    assert attempt.usage_status == "COMPLETE"
    assert int(attempt.input_tokens) == 10
    assert int(attempt.output_tokens) == 20


def test_invoke_provider_keeps_null_usage_when_adapter_reported_none(db_session):
    """The NULL-usage discriminator (recovery-sweep upgrade) must survive:
    a text result without provider_usage records NULL, not a fabricated 0."""

    job, run = _job_binding(db_session, SimpleNamespace())
    result = run(lambda adapter: SmokeReply(ok=True))
    assert result.ok
    attempt = db_session.query(ModelCallAttempt).filter_by(job_id=job.id).one()
    assert attempt.outcome == "SUCCEEDED"
    assert attempt.usage is None
    assert attempt.input_tokens is None
    assert attempt.usage_status == "UNKNOWN"


def test_invoke_provider_records_image_usage_and_empty_stays_null(db_session):
    job, run = _job_binding(db_session, SimpleNamespace())
    response = ModelResponse(
        model_id="img-model",
        request_id="req-1",
        usage={"output_images": 1},
        images=(b"img",),
    )
    run(lambda adapter: response)
    attempt = db_session.query(ModelCallAttempt).filter_by(job_id=job.id).one()
    assert attempt.usage == {"output_images": 1}
    assert int(attempt.output_images) == 1

    job2, run2 = _job_binding(db_session, SimpleNamespace())
    run2(
        lambda adapter: ModelResponse(
            model_id="img-model", request_id="req-2", usage={}, images=(b"img",)
        )
    )
    attempt2 = db_session.query(ModelCallAttempt).filter_by(job_id=job2.id).one()
    assert attempt2.usage is None


# ---------------------------------------------------------------------------
# #205: truncation finish reasons classify as OUTPUT_TRUNCATED
# (chat/responses/anthropic protocol cases; see also the classification file)
# ---------------------------------------------------------------------------


def _truncated_chat_body():
    return {
        "choices": [
            {
                "finish_reason": "length",
                "message": {"content": '{"ok": tr'},  # truncated JSON
            }
        ]
    }


def test_chat_length_finish_reason_is_output_truncated_not_invalid_output():
    adapter = _chat_adapter(
        lambda request: httpx.Response(
            200, json=_truncated_chat_body(), request=request
        )
    )
    with pytest.raises(ProviderAdapterError) as excinfo:
        adapter.generate_structured(StructuredRequest(prompt="x"), SmokeReply)
    assert excinfo.value.code == OUTPUT_TRUNCATED
    assert excinfo.value.retryable is False


def test_responses_incomplete_status_is_output_truncated():
    adapter = _chat_adapter(
        lambda request: httpx.Response(
            200,
            json={"status": "incomplete", "output": []},
            request=request,
        ),
        use_responses_api=True,
    )
    with pytest.raises(ProviderAdapterError) as excinfo:
        adapter.generate_structured(StructuredRequest(prompt="x"), SmokeReply)
    assert excinfo.value.code == OUTPUT_TRUNCATED
    assert excinfo.value.retryable is False


def test_responses_incomplete_details_reason_is_output_truncated():
    adapter = _chat_adapter(
        lambda request: httpx.Response(
            200,
            json={
                "status": "completed",
                "incomplete_details": {"reason": "max_output_tokens"},
                "output": [
                    {"content": [{"type": "output_text", "text": '{"ok": tr'}]}
                ],
            },
            request=request,
        ),
        use_responses_api=True,
    )
    with pytest.raises(ProviderAdapterError) as excinfo:
        adapter.generate_structured(StructuredRequest(prompt="x"), SmokeReply)
    assert excinfo.value.code == OUTPUT_TRUNCATED


def test_anthropic_max_tokens_stop_reason_is_output_truncated():
    adapter = _anthropic_adapter(
        lambda request: httpx.Response(
            200,
            json={
                "stop_reason": "max_tokens",
                "content": [{"type": "text", "text": '{"ok": tr'}],
            },
            request=request,
        )
    )
    with pytest.raises(ProviderAdapterError) as excinfo:
        adapter.generate_structured(StructuredRequest(prompt="x"), SmokeReply)
    assert excinfo.value.code == OUTPUT_TRUNCATED
    assert excinfo.value.retryable is False


def test_anthropic_max_tokens_truncation_preempts_json_validation():
    adapter = _anthropic_adapter(
        lambda request: httpx.Response(
            200,
            json={"stop_reason": "max_tokens", "content": []},
            request=request,
        )
    )
    with pytest.raises(ProviderAdapterError) as excinfo:
        adapter.generate_structured(StructuredRequest(prompt="x"), SmokeReply)
    assert excinfo.value.code == OUTPUT_TRUNCATED


# ---------------------------------------------------------------------------
# #207: image generation b64 default + billed usage on download failure
# ---------------------------------------------------------------------------


def _image_ok(encoded: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={"data": [{"b64_json": encoded}], "usage": {"output_images": 1}},
    )


def test_image_generate_requests_b64_json_by_default():
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return _image_ok(base64.b64encode(b"img").decode("ascii"))

    adapter = _chat_adapter(handler)
    adapter.generate_asset(ImageRequest(prompt="p", resolution="1K"))
    assert captured[0]["response_format"] == "b64_json"


def test_image_generate_extra_body_overrides_response_format():
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return _image_ok(base64.b64encode(b"img").decode("ascii"))

    adapter = _chat_adapter(
        handler,
        capabilities={"extra_body": {"response_format": "url"}},
    )
    adapter.generate_asset(ImageRequest(prompt="p", resolution="1K"))
    assert captured[0]["response_format"] == "url"


def test_image_download_failure_keeps_billed_usage_as_timeout(monkeypatch):
    import app.model_adapters.compatible as compatible

    monkeypatch.setattr(
        compatible.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(2, 1, 6, "", ("93.184.216.34", 443))],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/images/generations"):
            return httpx.Response(
                200,
                json={
                    "data": [{"url": "https://cdn.provider.test/image.png"}],
                    "usage": {"output_images": 1},
                },
            )
        raise httpx.ReadTimeout("download timed out")

    adapter = _chat_adapter(handler)
    with pytest.raises(ProviderAdapterError) as excinfo:
        adapter.generate_asset(ImageRequest(prompt="p", resolution="1K"))
    assert excinfo.value.code == "TIMEOUT"
    assert excinfo.value.usage == {"output_images": 1}


def test_invoke_provider_records_usage_on_failed_download(db_session):
    """POST succeeded and billed, then the download timed out: the attempt row
    must carry the error as TIMEOUT with the spent usage attached (#207)."""

    job, run = _job_binding(db_session, SimpleNamespace())

    def failing(adapter):
        raise ProviderAdapterError(
            "TIMEOUT", "供应商请求超时", retryable=True, usage={"output_images": 1}
        )

    with pytest.raises(ProviderAdapterError) as excinfo:
        run(failing)
    assert excinfo.value.code == "TIMEOUT"
    attempt = db_session.query(ModelCallAttempt).filter_by(job_id=job.id).one()
    assert attempt.outcome == "FAILED"
    assert attempt.error_code == "TIMEOUT"
    assert attempt.usage == {"output_images": 1}
    assert int(attempt.output_images) == 1


def test_invoke_provider_failure_without_usage_stays_null(db_session):
    job, run = _job_binding(db_session, SimpleNamespace())

    def failing(adapter):
        raise ProviderAdapterError("RATE_LIMIT", "limited", retryable=True)

    with pytest.raises(ProviderAdapterError):
        run(failing)
    attempt = db_session.query(ModelCallAttempt).filter_by(job_id=job.id).one()
    assert attempt.outcome == "FAILED"
    assert attempt.usage is None


# ---------------------------------------------------------------------------
# #209: ledger key sets, credential-manager dispatch stamping, SDK retry pin
# ---------------------------------------------------------------------------


def test_thinking_token_row_reaches_complete():
    normalized = normalize_usage(
        {
            "prompt_token_count": 10,
            "candidates_token_count": 20,
            "thoughts_token_count": 30,
            "total_token_count": 60,
        }
    )
    assert normalized.usage_status == "COMPLETE"
    assert normalized.input_tokens == 10
    assert normalized.output_tokens == 20


def test_reasoning_tokens_act_as_last_resort_output_bucket():
    normalized = normalize_usage({"prompt_tokens": 5, "reasoning_tokens": 7})
    assert normalized.usage_status == "COMPLETE"
    assert normalized.output_tokens == 7


def test_cache_read_tokens_land_in_cached_bucket():
    normalized = normalize_usage(
        {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "cache_read_tokens": 40,
            "output_images": None,
        }
    )
    assert normalized.cached_input_tokens == 40
    assert normalized.cache_hit is True
    assert normalized.usage_status == "COMPLETE"


def test_model_costs_mirrors_thinking_and_cached_keys():
    quantities, has_unmapped = _normalized_usage(
        {
            "prompt_token_count": 10,
            "candidates_token_count": 20,
            "thoughts_token_count": 30,
        }
    )
    assert has_unmapped is False
    assert quantities["output_tokens"] == 20

    quantities, _ = _normalized_usage(
        {"prompt_tokens": 100, "completion_tokens": 50, "cache_read_tokens": 40}
    )
    assert quantities["cached_input_tokens"] == 40

    quantities, has_unmapped = _normalized_usage(
        {"input_tokens": 1, "thinking_tokens": 4}
    )
    assert has_unmapped is False
    assert quantities["output_tokens"] == 4


def test_execute_stamps_dispatch_count_within_one_call():
    manager = VertexCredentialManager(max_attempts=3, base_backoff_seconds=0)
    calls: list[int] = []
    dispatches: list[int] = []

    class _Flaky(Exception):
        status_code = 503

    def operation(client):
        calls.append(1)
        if len(calls) < 2:
            raise _Flaky("upstream unavailable")
        return "done"

    settings = get_settings()
    assert (
        manager.execute(
            settings,
            operation,
            client_factory=lambda: SimpleNamespace(close=lambda: None),
            dispatch_counter=dispatches,
        )
        == "done"
    )
    assert len(dispatches) == 2
    assert len(calls) == 2


def test_execute_dispatch_count_is_per_call_not_shared():
    manager = VertexCredentialManager(max_attempts=2, base_backoff_seconds=0)
    settings = get_settings()
    first: list[int] = []
    second: list[int] = []
    manager.execute(
        settings,
        lambda client: "ok",
        client_factory=lambda: SimpleNamespace(close=lambda: None),
        dispatch_counter=first,
    )
    manager.execute(
        settings,
        lambda client: "ok",
        client_factory=lambda: SimpleNamespace(close=lambda: None),
        dispatch_counter=second,
    )
    assert len(first) == 1 and len(second) == 1
    assert not hasattr(manager, "last_dispatch_count"), (
        "the racy process-global counter must stay deleted (concurrent local "
        "executor jobs used to stomp it)"
    )


def test_unknown_exception_is_not_blindly_retryable():
    failure = classify_vertex_failure(RuntimeError("totally unknown bug"))
    assert failure.retryable is False

    transport = classify_vertex_failure(ConnectionError("peer reset"))
    assert transport.retryable is True


def test_google_sdk_namespace_error_stays_retryable():
    from google.genai import errors as genai_errors

    # A 4xx-shaped SDK error carries no message token the specific branches
    # match, so it reaches the narrowed catch-all: its google.genai namespace
    # must keep transient (retryable) semantics there.
    client_error = genai_errors.ClientError(400, {"error": {"message": "bad shape"}})
    failure = classify_vertex_failure(client_error)
    assert failure.retryable is True

    # The same catch-all must refuse retry for a non-SDK local defect.
    assert classify_vertex_failure(RuntimeError("unknown local bug")).retryable is False
    assert classify_vertex_failure(KeyError("missing")).retryable is False


def test_google_client_factory_pins_sdk_retry_attempts(monkeypatch):
    import google.genai as genai_module

    from app.model_adapters.google import GoogleRuntime, GoogleTextAdapter

    captured: dict[str, object] = {}

    class _FakeClient:
        def close(self):
            pass

    def fake_client(**kwargs):
        captured.update(kwargs)
        return _FakeClient()

    monkeypatch.setattr(genai_module, "Client", fake_client)
    GoogleTextAdapter(GoogleRuntime(api_key="k", model_id="m", display_name="m"))._client()
    http_options = captured["http_options"]
    assert http_options.retry_options.attempts == 1
    assert http_options.timeout == 90_000


def test_vertex_client_factory_pins_sdk_retry_attempts(monkeypatch, tmp_path):
    import google.genai as genai_module

    credentials_file = tmp_path / "service-account.json"
    credentials_file.write_text("{}", encoding="utf-8")

    class _StubCredentials:
        token = "tok"
        expiry = datetime.now(UTC) + timedelta(hours=1)

        def refresh(self, request):
            raise AssertionError("valid stub credentials must not refresh")

    monkeypatch.setattr(
        "google.oauth2.service_account.Credentials.from_service_account_file",
        classmethod(lambda cls, path, scopes=None: _StubCredentials()),
    )
    captured: dict[str, object] = {}

    class _FakeClient:
        def close(self):
            pass

    monkeypatch.setattr(
        genai_module,
        "Client",
        lambda **kwargs: captured.update(kwargs) or _FakeClient(),
    )
    manager = VertexCredentialManager()
    settings = Settings(
        google_cloud_project="p",
        google_application_credentials=credentials_file,
    )
    manager.create_client(settings)
    http_options = captured["http_options"]
    assert http_options.retry_options.attempts == 1
    assert http_options.timeout == 90_000


# ---------------------------------------------------------------------------
# #243-1: credential decrypt failure is terminal, health goes FAILED
# ---------------------------------------------------------------------------


def _decrypt_failed_setup(db_session, tmp_path):
    settings = Settings(
        environment="development",
        storage_root=tmp_path,
        mangaflow_credential_master_key=None,
    )
    profile = ProviderProfile(name="Decrypt Probe", preset_key=None)
    db_session.add(profile)
    db_session.flush()
    connection = ProviderConnection(
        provider_id=profile.id,
        name="默认连接",
        protocol="OPENAI",
        base_url="https://api.provider.test/v1",
        health_state="HEALTHY",
        message="凭据与模型目录连接验证成功",
    )
    db_session.add(connection)
    db_session.flush()
    # Encrypted with a DIFFERENT master key (fresh tmp_path) than the one the
    # runtime settings use below, so decryption must fail.
    other_root = tmp_path / "other"
    other_root.mkdir()
    other_settings = Settings(
        environment="development",
        storage_root=other_root,
        mangaflow_credential_master_key=None,
    )
    key = ProviderKey(
        connection_id=connection.id,
        label="default",
        encrypted_secret=encrypt_secret(other_settings, "rotated-away-secret"),
        key_hint="••••cret",
        health_state="HEALTHY",
    )
    db_session.add(key)
    db_session.commit()
    return settings, connection, key


def test_select_provider_key_raises_decrypt_error_with_row_context(
    db_session, tmp_path
):
    settings, connection, key = _decrypt_failed_setup(db_session, tmp_path)
    with pytest.raises(CredentialDecryptError) as excinfo:
        select_provider_key(db_session, settings, connection.id)
    assert excinfo.value.connection_id == connection.id
    assert excinfo.value.key_id == key.id


def test_binding_converges_decrypt_failure_to_terminal_code(
    db_session, tmp_path, monkeypatch
):
    from app.services.worker_handlers import provider as worker_provider

    settings, connection, key = _decrypt_failed_setup(db_session, tmp_path)

    def failing_bind(*args, **kwargs):
        error = CredentialDecryptError("供应商凭据无法解密")
        error.connection_id = connection.id
        error.key_id = key.id
        raise error

    monkeypatch.setattr(worker_provider, "bind_adapter", failing_bind)

    with pytest.raises(ProviderAdapterError) as excinfo:
        worker_provider._binding(
            db_session,
            operation="structured_text",
            project_id="project-1",
            explicit_reference=None,
            task_kind="TEXT",
        )
    assert excinfo.value.code == CREDENTIAL_DECRYPT_FAILED
    assert excinfo.value.retryable is False

    # The FAILED health transition committed on the scoped diagnostics session
    # and must be visible despite nothing committing on the caller's session.
    db_session.expire_all()
    refreshed_key = db_session.get(ProviderKey, key.id)
    refreshed_connection = db_session.get(ProviderConnection, connection.id)
    assert refreshed_key.health_state == "FAILED"
    assert refreshed_key.last_error_code == "CREDENTIAL_DECRYPT_FAILED"
    assert refreshed_connection.health_state == "FAILED"
    assert refreshed_connection.error_code == "CREDENTIAL_DECRYPT_FAILED"


def test_mark_credential_decrypt_failed_restorable_by_key_write(db_session, tmp_path):
    """The FAILED state is operational (not a deletion): re-saving the key
    must be able to restore UNKNOWN health."""

    from app.services.provider_catalog import mark_credential_decrypt_failed

    settings, connection, key = _decrypt_failed_setup(db_session, tmp_path)
    mark_credential_decrypt_failed(
        db_session, connection_id=connection.id, key_id=key.id
    )
    db_session.expire_all()
    assert db_session.get(ProviderKey, key.id).health_state == "FAILED"
    assert key.enabled is True  # not disabled: operator re-save is the fix


# ---------------------------------------------------------------------------
# #243-2: seeded Vertex provider_model_id refresh on boot
# ---------------------------------------------------------------------------


def _vertex_seeded_connection(db_session, settings):
    ensure_provider_presets(db_session, settings)
    profile = db_session.query(ProviderProfile).filter_by(preset_key="vertex-ai").one()
    connection = db_session.query(ProviderConnection).filter_by(
        provider_id=profile.id
    ).one()
    return connection


def test_vertex_preset_model_id_refreshes_from_current_settings(
    db_session, monkeypatch, tmp_path
):
    settings = get_settings()
    credentials = tmp_path / "vertex.json"
    credentials.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(settings, "google_application_credentials", credentials)
    monkeypatch.setattr(settings, "google_cloud_project", "refresh-project")

    _vertex_seeded_connection(db_session, settings)
    text_model = db_session.query(AIModel).filter_by(legacy_alias="text.fast").one()
    assert text_model.provider_model_id == settings.vertex_text_model

    # Operator sets a new model after first boot: the row still equals the
    # seeded default, so the next boot refreshes it.
    monkeypatch.setattr(settings, "vertex_text_model", "gemini-4-university")
    ensure_provider_presets(db_session, settings)
    db_session.expire_all()
    text_model = db_session.query(AIModel).filter_by(legacy_alias="text.fast").one()
    assert text_model.provider_model_id == "gemini-4-university"


def test_vertex_preset_user_edited_model_id_is_not_clobbered(
    db_session, monkeypatch, tmp_path
):
    settings = get_settings()
    credentials = tmp_path / "vertex.json"
    credentials.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(settings, "google_application_credentials", credentials)
    monkeypatch.setattr(settings, "google_cloud_project", "edit-project")

    _vertex_seeded_connection(db_session, settings)
    text_model = db_session.query(AIModel).filter_by(legacy_alias="text.fast").one()
    text_model.provider_model_id = "my-fine-tuned-model"
    db_session.commit()

    monkeypatch.setattr(settings, "vertex_text_model", "gemini-4-university")
    ensure_provider_presets(db_session, settings)
    db_session.expire_all()
    text_model = db_session.query(AIModel).filter_by(legacy_alias="text.fast").one()
    assert text_model.provider_model_id == "my-fine-tuned-model"


# ---------------------------------------------------------------------------
# #243-3: connection-edit health reset + config fingerprint stamping
# ---------------------------------------------------------------------------


def _catalog_connection(db_session):
    profile = ProviderProfile(name="Health Reset", preset_key=None)
    db_session.add(profile)
    db_session.flush()
    connection = ProviderConnection(
        provider_id=profile.id,
        name="默认连接",
        protocol="OPENAI",
        base_url="https://api.provider.test/v1",
        enabled=True,
        health_state="HEALTHY",
        error_code=None,
        message="凭据与模型目录连接验证成功",
        endpoint_templates={"models": "/models", "chat": "/chat/completions"},
        extra_headers={},
        balance_config={},
        nonsecret_config={},
    )
    db_session.add(connection)
    db_session.commit()
    return connection


@pytest.mark.parametrize(
    "field,value",
    [
        ("base_url", "https://api.relocated.test/v1"),
        ("endpoint_templates", {"chat": "/v2/chat/completions"}),
        ("extra_headers", {"x-custom": "1"}),
        ("use_responses_api", True),
    ],
)
def test_connection_edit_resets_health_to_unknown(db_session, field, value):
    connection = _catalog_connection(db_session)
    stamped_fingerprint = "fingerprint-of-verified-config"
    connection.nonsecret_config = {"verified_config_fingerprint": stamped_fingerprint}
    db_session.commit()

    updated = update_connection(
        db_session,
        connection.id,
        ConnectionUpdate(version=connection.version, **{field: value}),
    )
    assert updated.health_state == "UNKNOWN"
    assert updated.error_code is None
    assert updated.message == "连接配置已更改，等待重新验证"
    # The stale verdict fingerprint is dropped with the verdict itself.
    assert "verified_config_fingerprint" not in (updated.nonsecret_config or {})


def test_connection_fingerprint_tracks_editing_fields_only(db_session):
    connection = _catalog_connection(db_session)
    before = connection_config_fingerprint(connection)

    # A pure enable toggle or name change does not change the fingerprint.
    connection.enabled = False
    connection.name = "重命名"
    assert connection_config_fingerprint(connection) == before

    connection.base_url = "https://api.changed.test/v1"
    assert connection_config_fingerprint(connection) != before


def test_probe_verdict_stamps_config_fingerprint(db_session, tmp_path):
    from app.services.provider_catalog import probe_connection_credentials

    settings = Settings(
        environment="development",
        storage_root=tmp_path,
        mangaflow_credential_master_key=None,
        allow_private_provider_networks=False,
    )
    connection = _catalog_connection(db_session)
    db_session.add(
        ProviderKey(
            connection_id=connection.id,
            label="default",
            encrypted_secret=encrypt_secret(settings, "provider-key"),
            key_hint="••••-key",
            health_state="UNKNOWN",
        )
    )
    db_session.commit()

    http = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"data": []})
        )
    )
    try:
        verdict = probe_connection_credentials(
            db_session, settings, connection, client=http
        )
    finally:
        http.close()

    assert verdict["remote_verified"] is True
    fingerprint = verdict["config_fingerprint"]
    assert fingerprint
    db_session.expire_all()
    assert (
        db_session.get(ProviderConnection, connection.id).nonsecret_config[
            "verified_config_fingerprint"
        ]
        == fingerprint
    )


# ---------------------------------------------------------------------------
# #243-4: runtime PATCH conditional bump + unknown-key preservation
# ---------------------------------------------------------------------------


def test_runtime_patch_preserves_unknown_override_keys(db_session):
    settings = Settings(environment="development")
    db_session.add(
        AppSetting(
            key="runtime",
            value={
                "queue_mode": "LOCAL",
                "future_field_added_by_newer_deployment": 42,
            },
            version=3,
        )
    )
    db_session.commit()

    result = update_runtime_settings(
        db_session,
        settings,
        RuntimeSettingsUpdate(version=3, max_auto_repairs=2),
    )
    assert result.version == 4
    row = db_session.get(AppSetting, "runtime")
    assert row.value["future_field_added_by_newer_deployment"] == 42
    assert row.value["queue_mode"] == "LOCAL"
    assert row.value["max_auto_repairs"] == 2


def test_runtime_patch_version_bump_is_conditional(db_session, monkeypatch):
    """A concurrent PATCH landing between the version check and the claim must
    lose with 409 instead of silently overwriting the winner's value."""

    import app.services.runtime_settings as runtime_module
    from fastapi import HTTPException

    settings = Settings(environment="development")
    db_session.add(AppSetting(key="runtime", value={"queue_mode": "AUTO"}, version=1))
    db_session.commit()

    real_update = runtime_module.update

    def racing_update(*args, **kwargs):
        # The concurrent PATCH commits its bump between the loser's version
        # check and its conditional claim.
        db_session.execute(
            real_update(AppSetting)
            .where(AppSetting.key == "runtime")
            .values(
                value={"queue_mode": "LOCAL", "max_auto_repairs": 5},
                version=AppSetting.version + 1,
            )
        )
        db_session.commit()
        return real_update(*args, **kwargs)

    monkeypatch.setattr(runtime_module, "update", racing_update)

    with pytest.raises(HTTPException) as excinfo:
        update_runtime_settings(
            db_session,
            settings,
            RuntimeSettingsUpdate(version=1, max_auto_repairs=1),
        )
    assert excinfo.value.status_code == 409
    row = db_session.get(AppSetting, "runtime")
    assert row.version == 2
    assert row.value == {"queue_mode": "LOCAL", "max_auto_repairs": 5}


def test_runtime_patch_first_write_survives_concurrent_insert(db_session):
    settings = Settings(environment="development")
    db_session.add(AppSetting(key="runtime", value={"queue_mode": "LOCAL"}, version=2))
    db_session.commit()

    result = update_runtime_settings(
        db_session,
        settings,
        RuntimeSettingsUpdate(version=2, default_concurrency=2),
    )
    assert result.default_concurrency == 2
    assert result.version == 3
    assert result.queue_mode == "LOCAL"
