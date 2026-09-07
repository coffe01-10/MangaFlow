"""Regression: provider Retry-After hints must never crash the failure path.

A gateway returning ``429`` with a huge numeric ``Retry-After`` (epoch-style
echo) used to overflow ``timedelta``/``datetime`` inside
``mark_key_failure``. The OverflowError propagated out of the worker's
failure handler as WORKER_ERROR: the key was never cooled down, every retry
re-hit the limit and re-crashed. A superscript-digit hint (``isdigit()``
true, ``int()`` raising) crashed ``_provider_error`` itself. Valid but huge
hints silently cooled a key for decades.
"""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.model_adapters.compatible import _provider_error
from app.services.credential_crypto import mark_key_failure
from app.services.provider_errors import (
    MAX_RETRY_AFTER_SECONDS,
    parse_retry_after_seconds,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("90", 90),
        (None, None),
        ("", None),
        ("²", None),
        ("-5", None),
        ("0", None),
        ("Fri, 31 Dec 9999 23:59:59 GMT", None),
        ("1780000000000", MAX_RETRY_AFTER_SECONDS),
        ("9" * 30, MAX_RETRY_AFTER_SECONDS),
    ],
)
def test_parse_retry_after_bounds(raw, expected):
    assert parse_retry_after_seconds(raw) == expected


def test_provider_error_survives_garbage_retry_after():
    # Raw latin-1 byte from a broken server: decodes to "²", isdigit() is
    # True and the old int() call raised ValueError out of _provider_error.
    response = httpx.Response(429, headers={"retry-after": b"\xb2"})
    error = _provider_error(response)
    assert error.code == "RATE_LIMIT"
    assert error.retry_after_seconds is None

    epoch = httpx.Response(429, headers={"retry-after": "1780000000000"})
    error = _provider_error(epoch)
    assert error.retry_after_seconds == MAX_RETRY_AFTER_SECONDS


def test_mark_key_failure_clamps_huge_cooldown(db_session):
    from app.models import ProviderConnection, ProviderKey, ProviderProfile

    profile = ProviderProfile(preset_key="p-cooldown", name="cooldown")
    db_session.add(profile)
    db_session.flush()
    connection = ProviderConnection(
        provider_id=profile.id,
        name="cooldown-conn",
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

    mark_key_failure(db_session, key, "RATE_LIMIT", retry_after_seconds=10**13)

    assert key.cooldown_until is not None
    assert key.cooldown_until - datetime.now(UTC) <= timedelta(
        seconds=MAX_RETRY_AFTER_SECONDS + 5
    )
