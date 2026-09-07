"""Regression: balance-query failures must not disable the generation key.

``read_balance`` ran the billing endpoint with the generation key and piped
401/403 into ``mark_key_failure``, which set ``enabled=False`` permanently —
the only recovery was re-saving the same secret. One click of a read-only
diagnostic could take down the paid path on gateways whose billing endpoint
requires different scopes. Diagnostic surfaces now use ``degrade_only``:
record DEGRADED + error code, never disable, never start a cooldown.
"""


import httpx

from app.config import get_settings
from app.models import ProviderConnection, ProviderKey, ProviderProfile
from app.services.credential_crypto import encrypt_secret, mark_key_failure


def test_degrade_only_never_disables_or_cools(db_session):
    profile = ProviderProfile(preset_key="p-degrade", name="degrade")
    db_session.add(profile)
    db_session.flush()
    connection = ProviderConnection(
        provider_id=profile.id,
        name="degrade-conn",
        protocol="OPENAI",
        base_url="https://example.invalid",
    )
    db_session.add(connection)
    db_session.flush()
    key = ProviderKey(
        connection_id=connection.id,
        label="k",
        encrypted_secret="enc",
    )
    db_session.add(key)
    db_session.commit()

    mark_key_failure(db_session, key, "AUTHENTICATION", degrade_only=True)
    assert key.enabled is True
    assert key.health_state == "DEGRADED"
    assert key.last_error_code == "AUTHENTICATION"
    assert key.cooldown_until is None

    mark_key_failure(db_session, key, "RATE_LIMIT", retry_after_seconds=30, degrade_only=True)
    assert key.enabled is True
    assert key.cooldown_until is None

    # Strict mode keeps the documented disable semantics.
    mark_key_failure(db_session, key, "AUTHENTICATION")
    assert key.enabled is False
    assert key.health_state == "DENIED"


def test_balance_failure_keeps_generation_key_enabled(db_session):
    settings = get_settings()
    profile = ProviderProfile(preset_key="p-balance", name="balance")
    db_session.add(profile)
    db_session.flush()
    connection = ProviderConnection(
        provider_id=profile.id,
        name="balance-conn",
        protocol="OPENAI",
        base_url="https://example.invalid",
        balance_config={
            "enabled": True,
            "path": "/v1/balance",
            "currency": "USD",
        },
    )
    db_session.add(connection)
    db_session.flush()
    key = ProviderKey(
        connection_id=connection.id,
        label="gen",
        encrypted_secret=encrypt_secret(settings, "sk-generation"),
    )
    db_session.add(key)
    db_session.commit()

    from fastapi import HTTPException

    from app.services.provider_catalog import read_balance

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="billing scope denied")

    client = httpx.Client(transport=httpx.MockTransport(handler))

    import pytest as _pytest

    with _pytest.raises(HTTPException) as exc_info:
        read_balance(db_session, settings, connection.id, client=client)
    assert exc_info.value.status_code == 502

    db_session.refresh(key)
    assert key.enabled is True
    assert key.health_state == "DEGRADED"
    assert key.last_error_code == "PERMISSION"
    assert key.cooldown_until is None
    assert key.last_used_at is not None
