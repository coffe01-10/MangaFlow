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
    model = client.post(
        f"/api/v1/providers/connections/{connection_id}/models",
        json={
            "provider_model_id": "smoke-text",
            "model_type": "TEXT",
            "operations": ["structured_text"],
        },
    ).json()

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
