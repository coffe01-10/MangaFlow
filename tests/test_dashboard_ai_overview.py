from app.models import AIModel, ProviderConnection, ProviderKey, ProviderProfile


def _connection(db_session, *, name, protocol, health_state):
    profile = ProviderProfile(name=name, category="CUSTOM", enabled=True)
    db_session.add(profile)
    db_session.flush()
    connection = ProviderConnection(
        provider_id=profile.id,
        name=f"{name}连接",
        protocol=protocol,
        base_url="https://example.com/v1",
        enabled=True,
        health_state=health_state,
    )
    db_session.add(connection)
    db_session.flush()
    return connection


def test_dashboard_reports_ai_overview_defaults(client):
    response = client.get("/api/v1/projects/dashboard")

    assert response.status_code == 200
    overview = response.json()["ai_overview"]
    assert overview["enabled_model_count"] == 0
    assert overview["healthy_connection_count"] == 0
    assert overview["configured_connection_count"] == 0


def test_dashboard_counts_enabled_models_and_connection_health(client, db_session):
    healthy = _connection(db_session, name="健康", protocol="OPENAI", health_state="HEALTHY")
    keyed = _connection(db_session, name="密钥", protocol="ANTHROPIC", health_state="UNKNOWN")
    _connection(db_session, name="降级", protocol="OPENAI", health_state="DEGRADED")
    db_session.add(
        ProviderKey(connection_id=keyed.id, label="primary", encrypted_secret="secret")
    )
    db_session.add_all(
        [
            AIModel(
                connection_id=healthy.id,
                provider_model_id="gpt-test",
                display_name="GPT Test",
                model_type="TEXT",
                enabled=True,
            ),
            AIModel(
                connection_id=healthy.id,
                provider_model_id="gpt-disabled",
                display_name="GPT Disabled",
                model_type="TEXT",
                enabled=False,
            ),
        ]
    )
    db_session.commit()

    response = client.get("/api/v1/projects/dashboard")

    assert response.status_code == 200
    overview = response.json()["ai_overview"]
    assert overview["enabled_model_count"] == 1
    assert overview["healthy_connection_count"] == 1
    # configured 只统计带启用密钥（或 Vertex 原生已配置）的连接，与供应商列表口径一致。
    assert overview["configured_connection_count"] == 1
