import base64
import json
from datetime import UTC, datetime

import httpx
import pytest
from fastapi import HTTPException
from pydantic import BaseModel

from app.config import get_settings
from app.domain.states import JobStatus, Resolution
from app.model_adapters.base import ProviderAdapterError, StructuredRequest
from app.model_adapters.compatible import (
    AnthropicCompatibleAdapter,
    CompatibleRuntime,
    OpenAICompatibleAdapter,
    provider_http_client,
)
from app.models import (
    AIModel,
    Asset,
    AssetCandidate,
    GenerationBatch,
    GenerationJob,
    JobAssetReference,
    Project,
    ProviderConnection,
    ProviderHealth,
    ProviderKey,
    ProviderProfile,
    WorkflowDefinition,
    WorkflowNodeRun,
    WorkflowRun,
    WorkflowVersion,
)
from app.services.credential_crypto import (
    decrypt_secret,
    encrypt_secret,
    mark_key_failure,
    select_provider_key,
)
from app.services.job_service import cancel_job
from app.services.model_router import (
    model_operation_verified,
    model_supports_resolution,
    resolve_model,
)
from app.services.provider_catalog import _upsert_discovered_models
from app.services.provider_presets import ensure_provider_presets, proxy_url_for_connection


class SmokeResult(BaseModel):
    ok: bool


def test_development_creates_local_master_key_on_first_encryption(tmp_path):
    from app.config import Settings

    settings = Settings(
        environment="development",
        storage_root=tmp_path,
        mangaflow_credential_master_key=None,
    )

    assert settings.provider_credentials_writable is True
    token = encrypt_secret(settings, "local-development-secret")

    key_path = tmp_path / ".provider-credential-master-key"
    assert key_path.is_file()
    assert key_path.read_text(encoding="ascii").strip()
    assert decrypt_secret(settings, token) == "local-development-secret"


def test_production_refuses_to_generate_local_master_key(tmp_path):
    from app.config import Settings

    settings = Settings(
        environment="production",
        storage_root=tmp_path,
        mangaflow_credential_master_key=None,
    )

    assert settings.provider_credentials_writable is False
    with pytest.raises(HTTPException) as error:
        encrypt_secret(settings, "production-secret")

    assert error.value.status_code == 503
    assert not (tmp_path / ".provider-credential-master-key").exists()


def test_presets_seed_default_provider_catalog(client):
    response = client.get("/api/v1/providers")

    assert response.status_code == 200
    providers = response.json()
    keys = {item["preset_key"] for item in providers}
    assert {
        "vertex-ai",
        "openai",
        "anthropic",
        "deepseek",
        "openrouter",
        "opencode-zen",
        "siliconflow",
        "volcengine-ark",
        "zhipu",
        "dashscope",
    } <= keys
    assert len(providers) >= 20
    assert all(
        connection["protocol"] in {
            "OPENAI",
            "ANTHROPIC",
            "VERTEX_NATIVE",
            "GOOGLE_NATIVE",
        }
        for provider in providers
        for connection in provider["connections"]
    )
    connections = [connection for provider in providers for connection in provider["connections"]]
    assert all(
        connection["credential_source"] in {"CONNECTION_KEY", "ENV_SERVICE_ACCOUNT"}
        for connection in connections
    )
    assert all(connection["supported_model_types"] for connection in connections)
    anthropic = next(provider for provider in providers if provider["preset_key"] == "anthropic")
    assert anthropic["connections"][0]["supported_model_types"] == ["TEXT"]
    vertex = next(provider for provider in providers if provider["preset_key"] == "vertex-ai")
    assert vertex["connections"][0]["credential_source"] == "ENV_SERVICE_ACCOUNT"


def test_vertex_catalog_connection_inherits_legacy_health(
    db_session, monkeypatch, tmp_path
):
    settings = get_settings()
    credential_path = tmp_path / "vertex.json"
    credential_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(settings, "google_cloud_project", "test-project")
    monkeypatch.setattr(settings, "google_application_credentials", credential_path)
    health = ProviderHealth(
        provider="vertex-ai",
        configured=True,
        credential_file_present=True,
        health_state="HEALTHY",
        message="旧版验证已通过",
        latency_ms=321,
    )
    db_session.add(health)
    db_session.commit()

    ensure_provider_presets(db_session, settings)

    profile = db_session.query(ProviderProfile).filter_by(preset_key="vertex-ai").one()
    connection = (
        db_session.query(ProviderConnection).filter_by(provider_id=profile.id).one()
    )
    assert connection.enabled is True
    assert connection.health_state == "HEALTHY"
    assert connection.message == "旧版验证已通过"
    assert connection.latency_ms == 321


def test_custom_provider_key_is_encrypted_and_never_returned(
    client, db_session, monkeypatch
):
    settings = get_settings()
    master_key = base64.urlsafe_b64encode(b"m" * 32).decode("ascii")
    monkeypatch.setattr(settings, "mangaflow_credential_master_key", master_key)
    created = client.post(
        "/api/v1/providers",
        json={
            "name": "自建网关",
            "protocol": "OPENAI",
            "base_url": "https://gateway.example.com/v1",
            "use_responses_api": True,
        },
    )
    assert created.status_code == 201, created.text
    connection_id = created.json()["connections"][0]["id"]

    saved = client.put(
        f"/api/v1/providers/connections/{connection_id}/keys",
        json={"label": "primary", "api_key": "sk-provider-secret-value"},
    )

    assert saved.status_code == 201, saved.text
    assert "secret-value" not in saved.text
    row = db_session.query(ProviderKey).filter_by(connection_id=connection_id).one()
    assert "sk-provider-secret-value" not in row.encrypted_secret
    assert decrypt_secret(settings, row.encrypted_secret) == "sk-provider-secret-value"
    listed = client.get("/api/v1/providers")
    assert "sk-provider-secret-value" not in listed.text
    assert "encrypted_secret" not in listed.text


def test_multi_key_rotation_skips_rate_limited_key(client, db_session, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(
        settings,
        "mangaflow_credential_master_key",
        base64.urlsafe_b64encode(b"r" * 32).decode("ascii"),
    )
    created = client.post(
        "/api/v1/providers",
        json={
            "name": "轮换网关",
            "protocol": "ANTHROPIC",
            "base_url": "https://rotate.example.com/v1",
        },
    ).json()
    connection_id = created["connections"][0]["id"]
    for label in ("first", "second"):
        response = client.put(
            f"/api/v1/providers/connections/{connection_id}/keys",
            json={"label": label, "api_key": f"{label}-secret"},
        )
        assert response.status_code == 201

    first = select_provider_key(db_session, settings, connection_id)
    mark_key_failure(db_session, first.row, "RATE_LIMIT", retry_after_seconds=120)
    second = select_provider_key(db_session, settings, connection_id)

    assert second.row.id != first.row.id
    assert second.secret.endswith("-secret")


def test_custom_provider_rejects_insecure_public_http(client):
    response = client.post(
        "/api/v1/providers",
        json={
            "name": "不安全网关",
            "protocol": "OPENAI",
            "base_url": "http://gateway.example.com/v1",
        },
    )

    assert response.status_code == 422
    assert "HTTPS" in response.text


def test_balance_endpoint_rejects_cross_origin_url(client):
    provider = client.post(
        "/api/v1/providers",
        json={
            "name": "余额路径保护",
            "protocol": "OPENAI",
            "base_url": "https://balance.example.com/v1",
        },
    ).json()
    connection = provider["connections"][0]

    response = client.patch(
        f"/api/v1/providers/connections/{connection['id']}",
        json={
            "version": connection["version"],
            "balance_config": {
                "enabled": True,
                "path": "http://127.0.0.1:8000/api/v1/settings/runtime",
            },
        },
    )

    assert response.status_code == 422
    assert "站内绝对路径" in response.text


def test_operator_proxy_is_limited_to_unchanged_builtin_origin(
    db_session, monkeypatch
):
    settings = get_settings()
    monkeypatch.setattr(settings, "mangaflow_proxy_url", "http://127.0.0.1:7897")
    ensure_provider_presets(db_session, settings)
    profile = db_session.query(ProviderProfile).filter_by(preset_key="openai").one()
    connection = (
        db_session.query(ProviderConnection).filter_by(provider_id=profile.id).one()
    )

    assert proxy_url_for_connection(profile, connection, settings) == (
        "http://127.0.0.1:7897"
    )

    connection.base_url = "https://custom.example.com/v1"
    assert proxy_url_for_connection(profile, connection, settings) is None


def test_provider_http_client_pins_validated_dns_answer(monkeypatch):
    import app.model_adapters.compatible as compatible

    captured: dict[str, str] = {}
    monkeypatch.setattr(
        compatible.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (2, 1, 6, "", ("93.184.216.34", 443)),
        ],
    )

    def transport(hostname: str, address: str):
        captured.update(hostname=hostname, address=address)
        return httpx.MockTransport(
            lambda request: httpx.Response(200, json={"ok": True}, request=request)
        )

    monkeypatch.setattr(compatible, "_PinnedHTTPTransport", transport)
    http = provider_http_client(
        "https://provider.example.com/v1/models",
        timeout=httpx.Timeout(5.0),
    )
    try:
        assert http.get("https://provider.example.com/v1/models").status_code == 200
    finally:
        http.close()

    assert captured == {
        "hostname": "provider.example.com",
        "address": "93.184.216.34",
    }


def test_trusted_builtin_proxy_does_not_require_local_target_dns(monkeypatch):
    import app.model_adapters.compatible as compatible

    monkeypatch.setattr(
        compatible.socket,
        "getaddrinfo",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("代理目标不应在本机再次解析")
        ),
    )
    http = provider_http_client(
        "https://api.openai.com/v1/models",
        timeout=httpx.Timeout(5.0),
        proxy_url="http://127.0.0.1:7897",
    )
    http.close()


def test_compatible_adapter_passes_operator_proxy_for_api_origin(monkeypatch):
    import app.model_adapters.compatible as compatible

    captured: dict[str, str | None] = {}

    def client_factory(url: str, **kwargs):
        captured["url"] = url
        captured["proxy_url"] = kwargs.get("proxy_url")
        return httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={"choices": [{"message": {"content": '{"ok": true}'}}]},
                    request=request,
                )
            )
        )

    monkeypatch.setattr(compatible, "provider_http_client", client_factory)
    adapter = OpenAICompatibleAdapter(
        CompatibleRuntime(
            provider_name="OpenAI",
            protocol="OPENAI",
            base_url="https://api.openai.com/v1",
            api_key="openai-key",
            model_id="text-model",
            endpoint_templates={"chat": "/chat/completions"},
            proxy_url="http://127.0.0.1:7897",
        )
    )

    assert adapter.generate_structured(StructuredRequest(prompt="test"), SmokeResult).ok
    assert captured == {
        "url": "https://api.openai.com/v1/chat/completions",
        "proxy_url": "http://127.0.0.1:7897",
    }


def test_openai_and_anthropic_protocol_adapters_emit_expected_requests():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/messages"):
            return httpx.Response(
                200,
                json={"content": [{"type": "text", "text": '{"ok": true}'}]},
            )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"ok": true}'}}]},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    openai = OpenAICompatibleAdapter(
        CompatibleRuntime(
            provider_name="OpenAI-compatible",
            protocol="OPENAI",
            base_url="https://openai.example.com/v1",
            api_key="openai-key",
            model_id="text-model",
            endpoint_templates={"chat": "/chat/completions"},
        ),
        client=client,
    )
    anthropic = AnthropicCompatibleAdapter(
        CompatibleRuntime(
            provider_name="Anthropic-compatible",
            protocol="ANTHROPIC",
            base_url="https://anthropic.example.com/v1",
            api_key="anthropic-key",
            model_id="claude-model",
            endpoint_templates={"messages": "/messages"},
        ),
        client=client,
    )

    assert openai.generate_structured(StructuredRequest(prompt="test"), SmokeResult).ok
    assert anthropic.generate_structured(StructuredRequest(prompt="test"), SmokeResult).ok
    openai_body = json.loads(requests[0].content)
    anthropic_body = json.loads(requests[1].content)
    assert openai_body["model"] == "text-model"
    assert requests[0].headers["authorization"] == "Bearer openai-key"
    assert anthropic_body["model"] == "claude-model"
    assert requests[1].headers["x-api-key"] == "anthropic-key"
    assert requests[1].headers["anthropic-version"] == "2023-06-01"
    client.close()


@pytest.mark.parametrize("use_responses_api", [False, True])
def test_openai_adapter_omits_unsupported_optional_parameters(use_responses_api):
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        if use_responses_api:
            return httpx.Response(
                200,
                json={
                    "output": [
                        {
                            "content": [
                                {"type": "output_text", "text": '{"ok": true}'}
                            ]
                        }
                    ]
                },
                request=request,
            )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"ok": true}'}}]},
            request=request,
        )

    http = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = OpenAICompatibleAdapter(
        CompatibleRuntime(
            provider_name="limited-compatible",
            protocol="OPENAI",
            base_url="https://limited.example.com/v1",
            api_key="limited-key",
            model_id="limited-model",
            endpoint_templates={
                "chat": "/chat/completions",
                "responses": "/responses",
            },
            use_responses_api=use_responses_api,
            capabilities={"supported_parameters": ["max_tokens"]},
        ),
        client=http,
    )
    try:
        assert adapter.generate_structured(
            StructuredRequest(prompt="test", metadata={"max_output_tokens": 32}),
            SmokeResult,
        ).ok
    finally:
        http.close()

    payload = captured[0]
    assert "temperature" not in payload
    assert "response_format" not in payload
    assert "text" not in payload
    prompt = payload["input"] if use_responses_api else payload["messages"][-1]["content"]
    assert "JSON Schema" in prompt
    if use_responses_api:
        assert "max_output_tokens" not in payload
    else:
        assert payload["max_tokens"] == 32


def test_compatible_adapter_rejects_unapproved_loopback_target():
    adapter = OpenAICompatibleAdapter(
        CompatibleRuntime(
            provider_name="unsafe-local",
            protocol="OPENAI",
            base_url="http://127.0.0.1:9/v1",
            api_key="must-not-be-sent",
            model_id="text-model",
            endpoint_templates={"chat": "/chat/completions"},
        )
    )

    with pytest.raises(ProviderAdapterError, match="不允许的网络"):
        adapter.generate_structured(StructuredRequest(prompt="test"), SmokeResult)


def test_compatible_adapter_caps_provider_response_size():
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"x" * 65, request=request)
        )
    )
    adapter = OpenAICompatibleAdapter(
        CompatibleRuntime(
            provider_name="oversized-provider",
            protocol="OPENAI",
            base_url="https://oversized.example.com/v1",
            api_key="secret",
            model_id="text-model",
            endpoint_templates={"chat": "/chat/completions"},
            max_response_bytes=64,
        ),
        client=client,
    )

    with pytest.raises(ProviderAdapterError, match="超过允许的大小"):
        adapter.generate_structured(StructuredRequest(prompt="test"), SmokeResult)
    client.close()


def test_model_resync_preserves_matching_verified_capabilities(db_session):
    profile = ProviderProfile(name="同步供应商", category="CUSTOM", enabled=True)
    db_session.add(profile)
    db_session.flush()
    connection = ProviderConnection(
        provider_id=profile.id,
        name="同步连接",
        protocol="OPENAI",
        base_url="https://sync.example.com/v1",
        enabled=True,
        health_state="HEALTHY",
    )
    db_session.add(connection)
    db_session.flush()
    model = _upsert_discovered_models(db_session, connection, [{"id": "gpt-4o"}])[0]
    model.confidence = "VERIFIED"
    model.last_verified_at = datetime.now(UTC)
    db_session.commit()

    refreshed = _upsert_discovered_models(db_session, connection, [{"id": "gpt-4o"}])[0]

    assert refreshed.confidence == "VERIFIED"
    assert refreshed.last_verified_at is not None


def test_anthropic_connection_rejects_image_model_declaration(client):
    provider = client.post(
        "/api/v1/providers",
        json={
            "name": "Anthropic 图片误配",
            "protocol": "ANTHROPIC",
            "base_url": "https://anthropic-image.example.com/v1",
        },
    ).json()
    connection_id = provider["connections"][0]["id"]

    response = client.post(
        f"/api/v1/providers/connections/{connection_id}/models",
        json={
            "provider_model_id": "image-model",
            "model_type": "IMAGE",
            "operations": ["image_generate", "image_edit"],
        },
    )

    assert response.status_code == 422
    assert "Anthropic" in response.text


def test_model_creation_rejects_operations_that_do_not_match_model_type(client):
    provider = client.post(
        "/api/v1/providers",
        json={
            "name": "能力误配网关",
            "protocol": "OPENAI",
            "base_url": "https://capability-mismatch.example.com/v1",
        },
    ).json()
    connection_id = provider["connections"][0]["id"]

    response = client.post(
        f"/api/v1/providers/connections/{connection_id}/models",
        json={
            "provider_model_id": "invalid-image-model",
            "model_type": "IMAGE",
            "input_modalities": ["TEXT", "IMAGE"],
            "output_modalities": ["IMAGE"],
            "operations": ["structured_text"],
        },
    )

    assert response.status_code == 422
    assert "模型操作与模型类型不匹配" in response.text


def test_model_resolution_capability_is_enforced():
    model = AIModel(
        connection_id="connection",
        provider_model_id="one-k-only",
        display_name="One K",
        model_type="IMAGE",
        capabilities={"resolutions": ["1K"]},
    )

    assert model_supports_resolution(model, "1K") is True
    assert model_supports_resolution(model, "2K") is False


def test_operation_verification_is_scoped_to_the_tested_capability():
    model = AIModel(
        connection_id="connection",
        provider_model_id="partially-verified-image",
        display_name="Partially Verified Image",
        model_type="IMAGE",
        operations=["image_generate", "image_edit"],
        confidence="VERIFIED",
        capabilities={"verified_operations": ["image_generate"]},
    )

    assert model_operation_verified(model, "image_generate") is True
    assert model_operation_verified(model, "image_edit") is False


def test_text_auto_routing_only_uses_verified_models(db_session):
    profile = ProviderProfile(
        name="测试原生供应商",
        category="CUSTOM",
        enabled=True,
    )
    db_session.add(profile)
    db_session.flush()
    connection = ProviderConnection(
        provider_id=profile.id,
        name="测试连接",
        protocol="VERTEX_NATIVE",
        base_url="vertex://test",
        enabled=True,
        health_state="HEALTHY",
    )
    db_session.add(connection)
    db_session.flush()
    inferred = AIModel(
        connection_id=connection.id,
        provider_model_id="inferred-text",
        display_name="推断文字模型",
        model_type="TEXT",
        input_modalities=["TEXT"],
        output_modalities=["TEXT"],
        operations=["structured_text"],
        confidence="INFERRED",
        priority=100,
    )
    verified = AIModel(
        connection_id=connection.id,
        provider_model_id="verified-text",
        display_name="已验证文字模型",
        model_type="TEXT",
        input_modalities=["TEXT"],
        output_modalities=["TEXT"],
        operations=["structured_text"],
        confidence="VERIFIED",
        priority=10,
        last_verified_at=datetime.now(UTC),
    )
    db_session.add_all([inferred, verified])
    db_session.commit()

    resolved = resolve_model(
        db_session,
        get_settings(),
        operation="structured_text",
        explicit_reference="auto",
        task_kind="PAGE_GENERATE",
    )

    assert resolved.model.id == verified.id
    assert resolved.route_reason == "AUTO"


def test_model_availability_requires_an_enabled_provider_key(
    client, db_session, monkeypatch
):
    settings = get_settings()
    monkeypatch.setattr(
        settings,
        "mangaflow_credential_master_key",
        base64.urlsafe_b64encode(b"a" * 32).decode("ascii"),
    )
    created = client.post(
        "/api/v1/providers",
        json={
            "name": "可用性网关",
            "protocol": "OPENAI",
            "base_url": "https://availability.example.com/v1",
        },
    ).json()
    connection_id = created["connections"][0]["id"]
    model = client.post(
        f"/api/v1/providers/connections/{connection_id}/models",
        json={
            "provider_model_id": "text-model",
            "model_type": "TEXT",
            "operations": ["structured_text"],
        },
    ).json()

    listed = client.get("/api/v1/models").json()
    assert next(item for item in listed if item["catalog_id"] == model["id"])[
        "enabled"
    ] is False

    saved = client.put(
        f"/api/v1/providers/connections/{connection_id}/keys",
        json={"label": "default", "api_key": "available-key"},
    )
    assert saved.status_code == 201
    listed = client.get("/api/v1/models").json()
    assert next(item for item in listed if item["catalog_id"] == model["id"])[
        "enabled"
    ] is True

    key = db_session.query(ProviderKey).filter_by(connection_id=connection_id).one()
    mark_key_failure(db_session, key, "AUTHENTICATION")
    with pytest.raises(HTTPException, match="API Key"):
        resolve_model(
            db_session,
            settings,
            operation="structured_text",
            explicit_reference=model["id"],
        )
    listed = client.get("/api/v1/models").json()
    assert next(item for item in listed if item["catalog_id"] == model["id"])[
        "enabled"
    ] is False


def test_custom_provider_deletion_blocks_retryable_jobs(client, db_session):
    project = client.post("/api/v1/projects", json={"name": "供应商删除保护"}).json()
    created = client.post(
        "/api/v1/providers",
        json={
            "name": "待删除网关",
            "protocol": "ANTHROPIC",
            "base_url": "https://delete-guard.example.com/v1",
        },
    ).json()
    provider_id = created["id"]
    connection_id = created["connections"][0]["id"]
    model = AIModel(
        connection_id=connection_id,
        provider_model_id="retryable-model",
        display_name="Retryable Model",
        model_type="TEXT",
        operations=["structured_text"],
    )
    db_session.add(model)
    db_session.flush()
    job = GenerationJob(
        project_id=project["id"],
        target_type="PROJECT",
        target_id=project["id"],
        job_type="SCRIPT_PARSE",
        status=JobStatus.FAILED,
        catalog_model_id=model.id,
    )
    db_session.add(job)
    db_session.commit()

    blocked = client.delete(f"/api/v1/providers/{provider_id}")
    assert blocked.status_code == 409
    assert "可重试" in blocked.text

    job.status = JobStatus.CANCELLED
    db_session.commit()
    deleted = client.delete(f"/api/v1/providers/{provider_id}")
    assert deleted.status_code == 204


def test_active_job_reference_blocks_asset_deletion(client, db_session):
    project = client.post("/api/v1/projects", json={"name": "引用租约"}).json()
    asset = Asset(
        project_id=project["id"],
        kind="STYLE_REFERENCE",
        original_name="style.png",
        storage_key="style.png",
        mime_type="image/png",
        byte_size=1,
        sha256="a" * 64,
        source="USER_UPLOAD",
        status="UPLOADED",
    )
    db_session.add(asset)
    db_session.flush()
    job = GenerationJob(
        project_id=project["id"],
        target_type="STYLE",
        target_id="style-id",
        job_type="STYLE_ANALYZE",
        status=JobStatus.QUEUED,
    )
    db_session.add(job)
    db_session.flush()
    db_session.add(JobAssetReference(job_id=job.id, asset_id=asset.id))
    db_session.commit()

    blocked = client.delete(f"/api/v1/assets/{asset.id}")
    assert blocked.status_code == 409
    assert "生成任务" in blocked.text

    job.status = JobStatus.COMPLETED
    db_session.commit()
    deleted = client.delete(f"/api/v1/assets/{asset.id}")
    assert deleted.status_code == 204


def test_generated_asset_deletion_also_hides_its_asset_candidate(client, db_session):
    project = client.post("/api/v1/projects", json={"name": "删除生成素材"}).json()
    asset = Asset(
        project_id=project["id"],
        kind="CHARACTER_REFERENCE",
        original_name="character-sheet.png",
        storage_key="generated/character-sheet.png",
        mime_type="image/png",
        byte_size=1,
        sha256="c" * 64,
        source="GENERATED",
        status="GENERATED",
    )
    batch = GenerationBatch(
        project_id=project["id"],
        target_type="CHARACTER",
        target_id="character-id",
        ordinal=1,
        generation_kind="ASSET",
    )
    db_session.add_all([asset, batch])
    db_session.flush()
    candidate = AssetCandidate(
        batch_id=batch.id,
        ordinal=1,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        variant="SHEET",
        status="READY",
        asset_id=asset.id,
    )
    db_session.add(candidate)
    db_session.commit()

    deleted = client.delete(f"/api/v1/assets/{asset.id}")

    assert deleted.status_code == 204
    db_session.refresh(asset)
    db_session.refresh(candidate)
    assert asset.deleted_at is not None
    assert candidate.deleted_at is not None


def test_cancelling_completed_workflow_job_does_not_cancel_run(db_session):
    project = Project(name="完成态保护")
    db_session.add(project)
    db_session.flush()
    workflow = WorkflowDefinition(
        project_id=project.id,
        name="完成态保护",
        draft_graph={"schema_version": 2, "nodes": [], "edges": []},
    )
    db_session.add(workflow)
    db_session.flush()
    version = WorkflowVersion(
        workflow_id=workflow.id,
        revision=1,
        graph=workflow.draft_graph,
        graph_checksum="b" * 64,
        validation_report={},
    )
    db_session.add(version)
    db_session.flush()
    run = WorkflowRun(
        workflow_id=workflow.id,
        workflow_version_id=version.id,
        project_id=project.id,
        scope_type="PROJECT",
        status="RUNNING",
    )
    db_session.add(run)
    db_session.flush()
    job = GenerationJob(
        project_id=project.id,
        target_type="WORKFLOW_NODE",
        target_id="node-run",
        job_type="WORKFLOW_NODE",
        status=JobStatus.COMPLETED,
    )
    db_session.add(job)
    db_session.flush()
    node_run = WorkflowNodeRun(
        workflow_run_id=run.id,
        node_id="done",
        node_type="control.merge",
        status="COMPLETED",
        job_id=job.id,
    )
    db_session.add(node_run)
    db_session.commit()

    returned = cancel_job(db_session, job)

    assert returned.status == JobStatus.COMPLETED
    assert run.status == "RUNNING"
    assert node_run.status == "COMPLETED"
