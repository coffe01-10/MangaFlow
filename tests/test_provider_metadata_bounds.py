from __future__ import annotations

import gzip
import json

import httpx
import pytest
from fastapi import HTTPException

from sqlalchemy import select

from app.config import Settings
from app.models import AIModel, ProviderConnection, ProviderKey, ProviderProfile
from app.services.credential_crypto import encrypt_secret
from app.services.provider_catalog import discover_models, read_balance


def _settings(tmp_path, **overrides) -> Settings:
    values = {
        "environment": "development",
        "storage_root": tmp_path,
        "max_provider_metadata_bytes": 128,
        "max_discovered_models": 2,
    }
    values.update(overrides)
    return Settings(**values)


def _seed_connection(db_session, settings: Settings) -> ProviderConnection:
    profile = ProviderProfile(name="有界供应商", category="CUSTOM", enabled=True)
    db_session.add(profile)
    db_session.flush()
    connection = ProviderConnection(
        provider_id=profile.id,
        name="有界连接",
        protocol="OPENAI",
        base_url="https://bounded.example.com/v1",
        enabled=True,
        endpoint_templates={"models": "/models"},
        balance_config={"enabled": True, "path": "/balance", "result_path": "total"},
    )
    db_session.add(connection)
    db_session.flush()
    db_session.add(
        ProviderKey(
            connection_id=connection.id,
            encrypted_secret=encrypt_secret(settings, "sk-test"),
            key_hint="test",
            enabled=True,
        )
    )
    db_session.commit()
    return connection


def test_discover_models_stops_reading_oversized_json(db_session, tmp_path):
    settings = _settings(tmp_path)
    connection = _seed_connection(db_session, settings)
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"x" * 4096, request=request)
        )
    )

    with pytest.raises(HTTPException) as error:
        discover_models(db_session, settings, connection.id, client=client)

    client.close()
    db_session.refresh(connection)
    assert error.value.status_code == 502
    assert connection.health_state == "DEGRADED"
    assert list(db_session.scalars(select(AIModel))) == []


def test_discover_models_rejects_too_many_entries(db_session, tmp_path):
    settings = _settings(tmp_path)
    connection = _seed_connection(db_session, settings)
    payload = {"data": [{"id": f"model-{index}"} for index in range(5)]}
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=payload, request=request)
        )
    )

    with pytest.raises(HTTPException) as error:
        discover_models(db_session, settings, connection.id, client=client)

    client.close()
    db_session.refresh(connection)
    assert error.value.status_code == 502
    assert connection.health_state == "DEGRADED"
    assert list(db_session.scalars(select(AIModel))) == []


def test_discover_models_accepts_small_payload(db_session, tmp_path):
    settings = _settings(tmp_path, max_provider_metadata_bytes=4096)
    connection = _seed_connection(db_session, settings)
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"data": [{"id": "tiny-model"}]}, request=request
            )
        )
    )

    models = discover_models(db_session, settings, connection.id, client=client)
    client.close()
    db_session.refresh(connection)
    assert [item.provider_model_id for item in models] == ["tiny-model"]
    assert connection.health_state == "HEALTHY"


def test_discover_models_counts_decompressed_bytes(db_session, tmp_path):
    settings = _settings(tmp_path)
    connection = _seed_connection(db_session, settings)
    compressed = gzip.compress(json.dumps({"data": [{"id": "gz"}]}).encode() + b"x" * 4000)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-encoding": "gzip", "content-type": "application/json"},
            content=compressed,
            request=request,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(HTTPException):
        discover_models(db_session, settings, connection.id, client=client)
    client.close()
    db_session.refresh(connection)
    assert connection.health_state == "DEGRADED"
    assert list(db_session.scalars(select(AIModel))) == []


def test_read_balance_rejects_oversized_payload(db_session, tmp_path):
    settings = _settings(tmp_path)
    connection = _seed_connection(db_session, settings)
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, content=b'{"total": 1}' + b"x" * 4000, request=request
            )
        )
    )

    with pytest.raises(HTTPException) as error:
        read_balance(db_session, settings, connection.id, client=client)

    client.close()
    assert error.value.status_code == 502
