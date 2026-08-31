import base64
from types import SimpleNamespace

from app.config import get_settings
from app.models import AIModel, ModelProbe, ProviderConnection
from app.services.provider_presets import ensure_provider_presets


def _configure_key_storage(monkeypatch) -> None:
    monkeypatch.setattr(
        get_settings(),
        "mangaflow_credential_master_key",
        base64.urlsafe_b64encode(b"v" * 32).decode("ascii"),
    )


def test_connection_health_and_credential_verify_use_protocol_capabilities(
    client, db_session, monkeypatch
):
    _configure_key_storage(monkeypatch)
    provider = client.post(
        "/api/v1/providers",
        json={
            "name": "无目录协议",
            "protocol": "ANTHROPIC",
            "base_url": "https://anthropic-compatible.example.com/v1",
        },
    ).json()
    connection_id = provider["connections"][0]["id"]
    assert client.put(
        f"/api/v1/providers/connections/{connection_id}/keys",
        json={"label": "default", "api_key": "test-key"},
    ).status_code == 201

    def forbid_model_call(*_args, **_kwargs):
        raise AssertionError("CREDENTIALS verification must not bind a model adapter")

    monkeypatch.setattr(
        "app.services.connection_verifier.bind_adapter", forbid_model_call
    )
    response = client.post(
        f"/api/v1/providers/connections/{connection_id}/verify",
        json={"level": "CREDENTIALS"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["probe"]["status"] == "PASSED"
    assert payload["probe"]["model_id"] is None
    assert payload["health"]["configured"] is True
    assert payload["health"]["credential_source"] == "CONNECTION_KEY"
    assert payload["health"]["supports_model_discovery"] is False
    assert payload["health"]["supported_model_types"] == ["TEXT"]
    assert payload["health"]["health_state"] == "DEGRADED"
    assert db_session.query(AIModel).filter_by(connection_id=connection_id).count() == 0

    health = client.get(
        f"/api/v1/providers/connections/{connection_id}/health"
    )
    assert health.status_code == 200
    assert health.json() == payload["health"]
    discovery = client.post(
        f"/api/v1/providers/connections/{connection_id}/discover"
    )
    assert discovery.status_code == 422
    assert "不支持模型发现" in discovery.text


def test_credential_verify_without_usable_key_clears_checking_state(client):
    provider = client.post(
        "/api/v1/providers",
        json={
            "name": "无可用密钥",
            "protocol": "OPENAI",
            "base_url": "https://no-key.example.com/v1",
        },
    ).json()
    connection_id = provider["connections"][0]["id"]

    response = client.post(
        f"/api/v1/providers/connections/{connection_id}/verify",
        json={"level": "CREDENTIALS"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["probe"]["status"] == "FAILED"
    assert payload["probe"]["error_code"] == "NO_USABLE_KEY"
    assert payload["health"]["health_state"] == "UNCONFIGURED"
    assert payload["health"]["error_code"] == "NO_USABLE_KEY"


def test_model_smoke_updates_connection_model_and_probe(
    client, db_session, monkeypatch
):
    _configure_key_storage(monkeypatch)
    provider = client.post(
        "/api/v1/providers",
        json={
            "name": "冒烟网关",
            "protocol": "OPENAI",
            "base_url": "https://smoke.example.com/v1",
        },
    ).json()
    connection_id = provider["connections"][0]["id"]
    assert client.put(
        f"/api/v1/providers/connections/{connection_id}/keys",
        json={"label": "default", "api_key": "smoke-key"},
    ).status_code == 201
    model_response = client.post(
        f"/api/v1/providers/connections/{connection_id}/models",
        json={
            "provider_model_id": "smoke-text",
            "model_type": "TEXT",
            "operations": ["structured_text"],
        },
    )
    assert model_response.status_code == 201, model_response.text
    model = model_response.json()

    adapter = SimpleNamespace(
        generate_structured=lambda *_args, **_kwargs: SimpleNamespace(ok=True)
    )
    binding = SimpleNamespace(adapter=adapter, selected_key=None)
    monkeypatch.setattr(
        "app.services.connection_verifier.bind_adapter",
        lambda *_args, **_kwargs: binding,
    )

    response = client.post(
        f"/api/v1/providers/connections/{connection_id}/verify",
        json={"level": "MODEL_SMOKE", "catalog_model_id": model["id"]},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["health"]["health_state"] == "HEALTHY"
    assert payload["probe"]["probe_type"] == "MODEL_SMOKE"
    assert payload["probe"]["status"] == "PASSED"
    stored = db_session.get(AIModel, model["id"])
    assert stored.confidence == "VERIFIED"
    assert stored.capabilities["verified_operations"] == ["structured_text"]
    assert stored.last_verified_at is not None


def test_legacy_text_and_vision_smokes_preserve_requested_operation(
    client, db_session, monkeypatch
):
    _configure_key_storage(monkeypatch)
    provider = client.post(
        "/api/v1/providers",
        json={
            "name": "多模态冒烟网关",
            "protocol": "OPENAI",
            "base_url": "https://vision-smoke.example.com/v1",
        },
    ).json()
    connection_id = provider["connections"][0]["id"]
    assert client.put(
        f"/api/v1/providers/connections/{connection_id}/keys",
        json={"label": "default", "api_key": "vision-key"},
    ).status_code == 201
    model_response = client.post(
        f"/api/v1/providers/connections/{connection_id}/models",
        json={
            "provider_model_id": "vision-text",
            "model_type": "TEXT",
            "input_modalities": ["TEXT", "IMAGE"],
            "operations": ["structured_text", "multimodal_analysis"],
        },
    )
    assert model_response.status_code == 201, model_response.text
    model = model_response.json()
    calls = {"text": 0, "vision": 0}

    def text_call(*_args, **_kwargs):
        calls["text"] += 1

    def vision_call(*_args, **_kwargs):
        calls["vision"] += 1

    binding = SimpleNamespace(
        adapter=SimpleNamespace(
            generate_structured=text_call,
            analyze_multimodal=vision_call,
        ),
        selected_key=None,
    )
    monkeypatch.setattr(
        "app.services.connection_verifier.bind_adapter",
        lambda *_args, **_kwargs: binding,
    )

    text_probe = client.post(
        f"/api/v1/providers/connections/{connection_id}/test",
        json={"test_type": "TEXT", "model_id": model["id"]},
    )
    assert text_probe.status_code == 200, text_probe.text
    assert text_probe.json()["metrics"]["verified_operations"] == [
        "structured_text"
    ]
    assert db_session.get(AIModel, model["id"]).confidence == "PARTIAL"

    vision_probe = client.post(
        f"/api/v1/providers/connections/{connection_id}/test",
        json={"test_type": "VISION", "model_id": model["id"]},
    )
    assert vision_probe.status_code == 200, vision_probe.text
    assert vision_probe.json()["metrics"]["verified_operations"] == [
        "multimodal_analysis"
    ]
    stored = db_session.get(AIModel, model["id"])
    assert stored.confidence == "VERIFIED"
    assert stored.capabilities["verified_operations"] == [
        "multimodal_analysis",
        "structured_text",
    ]
    assert calls == {"text": 1, "vision": 1}


def test_image_smoke_makes_one_acknowledged_call(client, monkeypatch):
    _configure_key_storage(monkeypatch)
    provider = client.post(
        "/api/v1/providers",
        json={
            "name": "单次图片冒烟网关",
            "protocol": "OPENAI",
            "base_url": "https://image-smoke.example.com/v1",
        },
    ).json()
    connection_id = provider["connections"][0]["id"]
    assert client.put(
        f"/api/v1/providers/connections/{connection_id}/keys",
        json={"label": "default", "api_key": "image-key"},
    ).status_code == 201
    model_response = client.post(
        f"/api/v1/providers/connections/{connection_id}/models",
        json={
            "provider_model_id": "image-both",
            "model_type": "IMAGE",
            "input_modalities": ["TEXT", "IMAGE"],
            "output_modalities": ["IMAGE"],
            "operations": ["image_generate", "image_edit"],
        },
    )
    assert model_response.status_code == 201, model_response.text
    model = model_response.json()
    calls = {"generate": 0, "edit": 0}

    def generate(*_args, **_kwargs):
        calls["generate"] += 1

    def edit(*_args, **_kwargs):
        calls["edit"] += 1

    binding = SimpleNamespace(
        adapter=SimpleNamespace(generate_asset=generate, edit_region=edit),
        selected_key=None,
    )
    monkeypatch.setattr(
        "app.services.connection_verifier.bind_adapter",
        lambda *_args, **_kwargs: binding,
    )

    response = client.post(
        f"/api/v1/providers/connections/{connection_id}/verify",
        json={
            "level": "MODEL_SMOKE",
            "catalog_model_id": model["id"],
            "acknowledge_cost": True,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["probe"]["metrics"]["verified_operations"] == [
        "image_generate"
    ]
    assert calls == {"generate": 1, "edit": 0}


def test_legacy_vertex_verify_forwards_to_unified_connection_health(
    client, db_session, monkeypatch
):
    manager = SimpleNamespace(
        execute=lambda *_args, **_kwargs: True,
        token_expiry=lambda _settings: None,
    )
    monkeypatch.setattr(
        "app.services.vertex_health.get_vertex_credential_manager", lambda: manager
    )

    response = client.post(
        "/api/v1/settings/vertex/verify", json={"level": "CREDENTIALS"}
    )

    assert response.status_code == 200, response.text
    assert response.json()["health_state"] == "HEALTHY"
    connection = db_session.query(ProviderConnection).filter_by(
        protocol="VERTEX_NATIVE"
    ).one()
    assert connection.health_state == "HEALTHY"
    assert db_session.query(ModelProbe).filter_by(
        connection_id=connection.id, probe_type="CREDENTIALS"
    ).count() == 1
    assert client.get("/api/v1/models/vertex/status").status_code == 404


def test_preset_refresh_does_not_overwrite_existing_model_definition(db_session):
    settings = get_settings()
    ensure_provider_presets(db_session, settings, auto_commit=True)
    model = db_session.query(AIModel).filter_by(legacy_alias="text.fast").one()
    model.display_name = "用户命名"
    model.capabilities = {
        **dict(model.capabilities or {}),
        "verified_operations": ["structured_text"],
        "user_note": "preserve",
    }
    model.enabled = False
    db_session.commit()

    ensure_provider_presets(db_session, settings, auto_commit=True)
    db_session.refresh(model)

    assert model.display_name == "用户命名"
    assert model.capabilities["user_note"] == "preserve"
    assert model.capabilities["verified_operations"] == ["structured_text"]
    assert model.enabled is False
