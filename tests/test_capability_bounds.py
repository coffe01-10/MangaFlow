"""Regression: malformed capability values must not brick catalog or workers.

``GET /models``, vertex capability construction, worker reference-capacity
checks and the google adapter all used bare ``int(...)`` on the free-form
``capabilities`` JSON. One admin write with a non-numeric
``max_reference_images`` (or a string ``resolutions``) crashed every read
path instead of being rejected or ignored.
"""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.models import AIModel
from app.provider_schemas import ProviderModelCreate, ProviderModelUpdate
from app.services.model_capabilities import capability_reference_limit


def test_capability_reference_limit_reads_declared_values():
    assert capability_reference_limit({"max_reference_images": 5}) == 5
    assert capability_reference_limit({"max_reference_images": "3"}) == 3
    assert capability_reference_limit({"max_reference_images": 0}) == 0
    assert capability_reference_limit({}) is None
    assert capability_reference_limit(None) is None


def test_capability_reference_limit_reads_garbage_as_undeclared():
    for garbage in ("four", -1, 2.9, True, [4], {"n": 1}, 10**6, None):
        assert capability_reference_limit({"max_reference_images": garbage}) is None


def test_model_supports_resolution_treats_string_as_single_declaration():
    from app.services.model_router import model_supports_resolution

    class _Model:
        capabilities: dict

        def __init__(self, capabilities):
            self.capabilities = capabilities

    undeclared = _Model({})
    assert model_supports_resolution(undeclared, "4K")

    declared = _Model({"resolutions": ["1K"]})
    assert model_supports_resolution(declared, "1K")
    assert not model_supports_resolution(declared, "4K")

    single_string = _Model({"resolutions": "1K"})
    assert model_supports_resolution(single_string, "1K")
    assert not model_supports_resolution(single_string, "4K")

    malformed = _Model({"resolutions": {"1K": True}})
    assert not model_supports_resolution(malformed, "1K")


def test_provider_model_schema_rejects_malformed_capability_values():
    base = {"provider_model_id": "m", "operations": ["image_generate"]}

    with pytest.raises(ValidationError):
        ProviderModelCreate(
            **base, capabilities={"max_reference_images": "four"}
        )
    with pytest.raises(ValidationError):
        ProviderModelCreate(**base, capabilities={"max_reference_images": -1})
    with pytest.raises(ValidationError):
        ProviderModelCreate(**base, capabilities={"max_reference_images": True})
    with pytest.raises(ValidationError):
        ProviderModelCreate(**base, capabilities={"resolutions": {"1K": True}})

    accepted = ProviderModelCreate(
        **base,
        capabilities={"max_reference_images": "5", "resolutions": "1K"},
    )
    assert accepted.capabilities["max_reference_images"] == "5"
    assert accepted.capabilities["resolutions"] == ["1K"]

    with pytest.raises(ValidationError):
        ProviderModelUpdate(capabilities={"max_reference_images": "four"})


def test_catalog_endpoint_survives_poisoned_capabilities(
    client: TestClient, db_session
):
    from app.models import ProviderConnection, ProviderProfile

    profile = ProviderProfile(
        preset_key="p-poison",
        name="poison",
        category="COMPATIBLE",
        enabled=True,
    )
    db_session.add(profile)
    db_session.flush()
    connection = ProviderConnection(
        provider_id=profile.id,
        name="poison-conn",
        protocol="OPENAI",
        base_url="https://example.invalid",
        enabled=True,
        health_state="HEALTHY",
    )
    db_session.add(connection)
    db_session.flush()
    db_session.add(
        AIModel(
            connection_id=connection.id,
            provider_model_id="poisoned",
            display_name="poisoned",
            model_type="IMAGE",
            operations=["image_generate"],
            capabilities={"max_reference_images": "four", "resolutions": "1K2K"},
        )
    )
    db_session.commit()

    response = client.get("/api/v1/models")
    assert response.status_code == 200
    poisoned = next(
        item for item in response.json() if item["model_id"] == "poisoned"
    )
    assert poisoned["max_reference_images"] == 0
    assert poisoned["resolutions"] == ["1K2K"]
