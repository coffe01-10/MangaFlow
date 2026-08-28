from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.config import get_settings
from app.models import AIModel, ProviderConnection, ProviderKey, ProviderProfile


def _connection(
    db_session,
    *,
    name,
    protocol="OPENAI",
    health_state="HEALTHY",
    enabled=True,
    profile_enabled=True,
):
    profile = ProviderProfile(name=name, category="CUSTOM", enabled=profile_enabled)
    db_session.add(profile)
    db_session.flush()
    connection = ProviderConnection(
        provider_id=profile.id,
        name=f"{name}连接",
        protocol=protocol,
        base_url="https://example.com/v1",
        enabled=enabled,
        health_state=health_state,
    )
    db_session.add(connection)
    db_session.flush()
    return connection


def _model(db_session, connection, *, provider_model_id, enabled=True):
    model = AIModel(
        connection_id=connection.id,
        provider_model_id=provider_model_id,
        display_name=provider_model_id,
        model_type="TEXT",
        enabled=enabled,
    )
    db_session.add(model)
    db_session.flush()
    return model


def _key(db_session, connection, *, label="primary", enabled=True, cooldown_until=None):
    key = ProviderKey(
        connection_id=connection.id,
        label=label,
        encrypted_secret="secret",
        enabled=enabled,
        cooldown_until=cooldown_until,
    )
    db_session.add(key)
    db_session.flush()
    return key


def _overview(client):
    response = client.get("/api/v1/projects/dashboard")
    assert response.status_code == 200
    return response.json()["ai_overview"]


def _catalog_by_id(client):
    response = client.get("/api/v1/models")
    assert response.status_code == 200
    listed = response.json()
    return listed, {item["catalog_id"]: item for item in listed}


def _assert_matches_catalog(client, listed):
    overview = _overview(client)
    assert overview["enabled_model_count"] == sum(1 for item in listed if item["enabled"])
    return overview


def test_dashboard_reports_ai_overview_defaults(client):
    overview = _overview(client)
    assert overview["enabled_model_count"] == 0
    assert overview["healthy_connection_count"] == 0
    assert overview["configured_connection_count"] == 0


def test_dashboard_does_not_seed_provider_presets(client, db_session):
    overview = _overview(client)

    assert overview["enabled_model_count"] == 0
    assert db_session.query(ProviderProfile).count() == 0
    assert db_session.query(AIModel).count() == 0


def test_dashboard_counts_enabled_models_and_connection_health(client, db_session):
    healthy = _connection(db_session, name="健康", protocol="OPENAI", health_state="HEALTHY")
    keyed = _connection(db_session, name="密钥", protocol="ANTHROPIC", health_state="UNKNOWN")
    _connection(db_session, name="降级", protocol="OPENAI", health_state="DEGRADED")
    _key(db_session, healthy)
    _key(db_session, keyed)
    available = _model(db_session, healthy, provider_model_id="gpt-test", enabled=True)
    _model(db_session, healthy, provider_model_id="gpt-disabled", enabled=False)
    db_session.commit()

    overview = _overview(client)
    assert overview["enabled_model_count"] == 1
    assert overview["healthy_connection_count"] == 1
    # configured 只统计带启用密钥（或 Vertex 原生已配置）的连接，与供应商列表口径一致。
    assert overview["configured_connection_count"] == 2

    listed, by_id = _catalog_by_id(client)
    assert by_id[available.id]["enabled"] is True
    _assert_matches_catalog(client, listed)


def test_dashboard_excludes_models_without_keys(client, db_session):
    connection = _connection(db_session, name="无密钥")
    model = _model(db_session, connection, provider_model_id="no-key")
    db_session.commit()

    overview = _overview(client)
    assert overview["enabled_model_count"] == 0
    assert overview["configured_connection_count"] == 0

    listed, by_id = _catalog_by_id(client)
    assert by_id[model.id]["enabled"] is False
    _assert_matches_catalog(client, listed)


def test_dashboard_excludes_disabled_keys(client, db_session):
    connection = _connection(db_session, name="禁用密钥")
    _key(db_session, connection, enabled=False)
    model = _model(db_session, connection, provider_model_id="disabled-key")
    db_session.commit()

    overview = _overview(client)
    assert overview["enabled_model_count"] == 0
    assert overview["configured_connection_count"] == 0

    listed, by_id = _catalog_by_id(client)
    assert by_id[model.id]["enabled"] is False
    _assert_matches_catalog(client, listed)


def test_dashboard_excludes_keys_in_cooldown_and_includes_expired_cooldown(
    client, db_session
):
    cooled = _connection(db_session, name="冷却中")
    expired = _connection(db_session, name="冷却过期")
    _key(
        db_session,
        cooled,
        cooldown_until=datetime.now(UTC) + timedelta(hours=1),
    )
    _key(
        db_session,
        expired,
        cooldown_until=datetime.now(UTC) - timedelta(minutes=1),
    )
    cooled_model = _model(db_session, cooled, provider_model_id="cooling")
    expired_model = _model(db_session, expired, provider_model_id="cooled-out")
    db_session.commit()

    overview = _overview(client)
    assert overview["enabled_model_count"] == 1
    # 冷却中的启用密钥仍算已配置，不等于当前可用。
    assert overview["configured_connection_count"] == 2

    listed, by_id = _catalog_by_id(client)
    assert by_id[cooled_model.id]["enabled"] is False
    assert by_id[expired_model.id]["enabled"] is True
    _assert_matches_catalog(client, listed)


def test_dashboard_excludes_disabled_connections(client, db_session):
    connection = _connection(db_session, name="禁用连接", enabled=False)
    _key(db_session, connection)
    model = _model(db_session, connection, provider_model_id="disabled-connection")
    db_session.commit()

    overview = _overview(client)
    assert overview["enabled_model_count"] == 0
    assert overview["configured_connection_count"] == 1

    listed, by_id = _catalog_by_id(client)
    assert by_id[model.id]["enabled"] is False
    _assert_matches_catalog(client, listed)


def test_dashboard_excludes_disabled_providers(client, db_session):
    connection = _connection(db_session, name="禁用供应商", profile_enabled=False)
    _key(db_session, connection)
    model = _model(db_session, connection, provider_model_id="disabled-provider")
    db_session.commit()

    overview = _overview(client)
    assert overview["enabled_model_count"] == 0
    assert overview["configured_connection_count"] == 1

    listed, by_id = _catalog_by_id(client)
    assert by_id[model.id]["enabled"] is False
    _assert_matches_catalog(client, listed)


def test_dashboard_excludes_compatible_models_when_credentials_are_read_only(
    client, db_session, monkeypatch
):
    settings = get_settings()
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "mangaflow_credential_master_key", None)
    assert settings.provider_credentials_writable is False

    connection = _connection(db_session, name="只读凭据")
    _key(db_session, connection)
    model = _model(db_session, connection, provider_model_id="read-only")
    db_session.commit()

    overview = _overview(client)
    assert overview["enabled_model_count"] == 0
    assert overview["configured_connection_count"] == 1

    listed, by_id = _catalog_by_id(client)
    assert by_id[model.id]["enabled"] is False
    _assert_matches_catalog(client, listed)


def test_dashboard_excludes_disabled_models(client, db_session):
    connection = _connection(db_session, name="禁用模型")
    _key(db_session, connection)
    model = _model(db_session, connection, provider_model_id="off", enabled=False)
    db_session.commit()

    overview = _overview(client)
    assert overview["enabled_model_count"] == 0
    assert overview["configured_connection_count"] == 1

    listed, by_id = _catalog_by_id(client)
    assert by_id[model.id]["enabled"] is False
    _assert_matches_catalog(client, listed)


def test_dashboard_counts_native_vertex_without_keys(client, db_session):
    connection = _connection(
        db_session,
        name="原生Vertex",
        protocol="VERTEX_NATIVE",
        health_state="DEGRADED",
    )
    model = _model(db_session, connection, provider_model_id="gemini-native")
    db_session.commit()

    overview = _overview(client)
    assert overview["enabled_model_count"] == 1
    assert overview["configured_connection_count"] == 1
    assert overview["healthy_connection_count"] == 0

    listed, by_id = _catalog_by_id(client)
    assert by_id[model.id]["enabled"] is True
    _assert_matches_catalog(client, listed)


def test_dashboard_counts_native_vertex_when_credentials_are_read_only(
    client, db_session, monkeypatch
):
    settings = get_settings()
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "mangaflow_credential_master_key", None)
    assert settings.provider_credentials_writable is False

    connection = _connection(
        db_session,
        name="只读Vertex",
        protocol="VERTEX_NATIVE",
        health_state="HEALTHY",
    )
    model = _model(db_session, connection, provider_model_id="vertex-readonly")
    db_session.commit()

    overview = _overview(client)
    assert overview["enabled_model_count"] == 1
    assert overview["healthy_connection_count"] == 1
    assert overview["configured_connection_count"] == 1

    listed, by_id = _catalog_by_id(client)
    assert by_id[model.id]["enabled"] is True
    _assert_matches_catalog(client, listed)


def test_dashboard_does_not_double_count_mixed_models_and_keys(client, db_session):
    mixed = _connection(db_session, name="混合连接", health_state="HEALTHY")
    extra = _connection(db_session, name="第二连接", health_state="UNKNOWN")
    _key(db_session, mixed, label="a")
    _key(db_session, mixed, label="b")
    _key(db_session, extra, label="only")
    first = _model(db_session, mixed, provider_model_id="mixed-1")
    second = _model(db_session, mixed, provider_model_id="mixed-2")
    _model(db_session, mixed, provider_model_id="mixed-off", enabled=False)
    third = _model(db_session, extra, provider_model_id="extra-1")
    db_session.commit()

    overview = _overview(client)
    assert overview["enabled_model_count"] == 3
    assert overview["healthy_connection_count"] == 1
    assert overview["configured_connection_count"] == 2

    listed, by_id = _catalog_by_id(client)
    assert by_id[first.id]["enabled"] is True
    assert by_id[second.id]["enabled"] is True
    assert by_id[third.id]["enabled"] is True
    _assert_matches_catalog(client, listed)


def test_homepage_keeps_dashboard_and_vertex_status_queries():
    source = (Path(__file__).resolve().parents[1] / "apps" / "web" / "app" / "page.tsx").read_text(
        encoding="utf-8"
    )
    assert 'queryKey: ["dashboard"]' in source
    assert 'queryKey: ["vertex-status"]' in source
    assert 'queryKey: ["models"]' not in source
    assert 'queryKey: ["providers"]' not in source
    assert "api.models" not in source
    assert "api.providers" not in source
