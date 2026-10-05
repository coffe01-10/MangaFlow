"""P0-1 structured capability dimensions (matrix §9).

Regression coverage for the edit_modes / resolution_tiers / media contract:
fail-closed reads with legacy-bit fallback, write-boundary validation,
preset declarations, discovery fingerprint preservation, the router's
task-kind edit-mode gate, and GET /models serialization.
"""

from datetime import UTC, datetime

import pytest
from app.config import get_settings
from app.models import AIModel
from app.provider_schemas import ProviderModelCreate, ProviderModelUpdate
from app.services.model_capabilities import (
    CAPABILITY_SOURCE_UNSPECIFIED,
    EDIT_MODE_IN_IMAGE_TEXT,
    EDIT_MODE_KEYS,
    capability_resolution_supported,
    declare_edit_modes,
    declare_resolution_tiers,
    edit_mode_source,
    edit_mode_summary,
    edit_mode_supported,
    media_capability_summary,
    merge_capability_fragments,
    model_edit_mode_supported,
    resolution_tier_map,
)
from app.services.provider_presets import ensure_provider_presets
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select


class _Model:
    def __init__(self, capabilities):
        self.capabilities = capabilities


# ---------------------------------------------------------------------------
# Structured edit_modes: fail-closed, legacy fallback, provenance.
# ---------------------------------------------------------------------------


def test_edit_modes_structured_read_is_authoritative_when_declared():
    caps = {
        "edit_modes": {
            "mask": {"supported": True, "source": "DECLARED"},
            "in_image_text_edit": {"supported": True, "source": "VERIFIED"},
        },
        "capability_sources": {"edit_modes.mask": "DECLARED"},
    }
    assert edit_mode_supported(caps, "mask") is True
    assert edit_mode_supported(caps, "in_image_text_edit") is True
    # Modes absent from a declared map do NOT fall through to legacy bits.
    assert edit_mode_supported(caps, "whole_image_reference") is False
    assert edit_mode_supported(caps, "instruction_region") is False


def test_edit_modes_fall_back_to_legacy_bits_when_absent():
    caps = {
        "accepts_explicit_mask": True,
        "whole_image_reference_only": True,
        "region_capability_sources": {"accepts_explicit_mask": "VERIFIED"},
    }
    assert edit_mode_supported(caps, "mask") is True
    assert edit_mode_source(caps, "mask") == "VERIFIED"
    assert edit_mode_supported(caps, "whole_image_reference") is True
    # in_image_text_edit has no legacy bit and stays unsupported.
    assert edit_mode_supported(caps, "in_image_text_edit") is False
    assert edit_mode_source(caps, "in_image_text_edit") == "UNSPECIFIED"


def test_edit_modes_read_survives_poisoned_values():
    for garbage in ("yes", 1, ["mask"], None, {"mask": "yes"}, {"mask": {"supported": "y"}}):
        assert edit_mode_supported({"edit_modes": garbage}, "mask") is False
    assert edit_mode_supported({}, "mask") is False
    assert edit_mode_supported(None, "mask") is False
    with pytest.raises(ValueError):
        edit_mode_supported({}, "not_a_mode")


def test_edit_mode_summary_serializes_every_mode():
    summary = edit_mode_summary({"accepts_explicit_mask": True})
    assert set(summary) == set(EDIT_MODE_KEYS)
    assert summary["mask"] == {"supported": True, "source": "UNSPECIFIED"}
    assert summary["in_image_text_edit"]["supported"] is False


def test_declare_edit_modes_marks_unspecified_modes_honestly():
    fragment = declare_edit_modes(
        whole_image_reference=True,
        unspecified=(EDIT_MODE_IN_IMAGE_TEXT,),
    )
    modes = fragment["edit_modes"]
    assert modes["whole_image_reference"] == {"supported": True, "source": "DECLARED"}
    assert modes["mask"] == {"supported": False, "source": "DECLARED"}
    assert modes["in_image_text_edit"] == {
        "supported": False,
        "source": "UNSPECIFIED",
    }
    assert fragment["capability_sources"]["edit_modes.in_image_text_edit"] == (
        CAPABILITY_SOURCE_UNSPECIFIED
    )
    with pytest.raises(ValueError):
        declare_edit_modes(source="MAYBE")


def test_merge_capability_fragments_unions_provenance_maps():
    left = declare_edit_modes(mask=True)
    right = declare_resolution_tiers({"1K": True})
    merged = merge_capability_fragments(left, right)
    assert merged["edit_modes"]["mask"]["supported"] is True
    assert merged["resolution_tiers"]["1K"]["supported"] is True
    assert merged["capability_sources"]["edit_modes.mask"] == "DECLARED"
    assert merged["capability_sources"]["resolution_tiers.1K"] == "DECLARED"


# ---------------------------------------------------------------------------
# resolution_tiers + media slots.
# ---------------------------------------------------------------------------


def test_resolution_tier_map_structured_is_authoritative():
    caps = {
        "resolution_tiers": {
            "1K": {"supported": True, "source": "DECLARED"},
            "4K": {"supported": False, "source": "DECLARED"},
        }
    }
    tiers = resolution_tier_map(caps)
    assert tiers["1K"]["supported"] is True
    assert tiers["4K"]["supported"] is False
    assert capability_resolution_supported(caps, "1K") is True
    assert capability_resolution_supported(caps, "4K") is False
    assert capability_resolution_supported(caps, "2K") is False


def test_resolution_tier_map_falls_back_to_legacy_list():
    tiers = resolution_tier_map({"resolutions": ["1K", "2K"]})
    assert tiers["1K"] == {"supported": True, "source": "UNSPECIFIED"}
    assert tiers["2K"]["supported"] is True
    assert capability_resolution_supported({"resolutions": ["1K"]}, "1K") is None
    assert capability_resolution_supported({}, "1K") is None


def test_media_slots_default_unspecified_and_read_poisoned_closed():
    summary = media_capability_summary({})
    assert summary == {
        "video": {"supported": False, "source": "UNSPECIFIED"},
        "audio": {"supported": False, "source": "UNSPECIFIED"},
    }
    assert media_capability_summary({"media": "garbage"})["video"]["supported"] is False
    declared = media_capability_summary(
        {"media": {"video": {"supported": True, "source": "VERIFIED"}}}
    )
    assert declared["video"] == {"supported": True, "source": "VERIFIED"}
    assert declared["audio"]["supported"] is False


# ---------------------------------------------------------------------------
# Write-boundary validation.
# ---------------------------------------------------------------------------


def test_capability_payload_validates_structured_dimensions():
    base = {"provider_model_id": "m", "operations": ["image_generate"]}
    accepted = ProviderModelCreate(
        **base,
        capabilities={
            "edit_modes": {"mask": {"supported": True, "source": "DECLARED"}},
            "resolution_tiers": {"1K": {"supported": True, "source": "VERIFIED"}},
            "media": {"video": {"supported": False, "source": "UNSPECIFIED"}},
            "capability_sources": {"edit_modes.mask": "DECLARED"},
        },
    )
    assert accepted.capabilities["edit_modes"]["mask"]["supported"] is True

    for bad in (
        {"edit_modes": {"mask": "yes"}},
        {"edit_modes": {"not_a_mode": {"supported": True, "source": "DECLARED"}}},
        {"edit_modes": {"mask": {"supported": "yes", "source": "DECLARED"}}},
        {"edit_modes": {"mask": {"supported": True, "source": "GUESSED"}}},
        {"resolution_tiers": {"1K": "yes"}},
        {"resolution_tiers": {"": {"supported": True}}},
        {"media": {"holo": {"supported": True}}},
        {"capability_sources": {"x": "PROBABLY"}},
        {"region_capability_sources": {"accepts_explicit_mask": "PROBABLY"}},
    ):
        with pytest.raises(ValidationError):
            ProviderModelCreate(**base, capabilities=bad)
    with pytest.raises(ValidationError):
        ProviderModelUpdate(
            capabilities={"edit_modes": {"mask": {"supported": 1}}}
        )


# ---------------------------------------------------------------------------
# Preset declarations: adapter-verifiable modes DECLARED, text-edit UNSPECIFIED.
# ---------------------------------------------------------------------------


def test_presets_declare_structured_dimensions_honestly(db_session):
    ensure_provider_presets(db_session, get_settings(), auto_commit=False)
    db_session.commit()
    models = list(
        db_session.scalars(
            select(AIModel).where(
                AIModel.legacy_alias.in_(
                    ["image.nano_banana_2", "image.nano_banana_pro"]
                )
            )
        )
    )
    assert len(models) == 2
    for model in models:
        caps = model.capabilities or {}
        modes = caps.get("edit_modes") or {}
        assert modes["whole_image_reference"]["supported"] is True
        assert modes["whole_image_reference"]["source"] == "DECLARED"
        assert modes["mask"]["supported"] is False
        assert modes["in_image_text_edit"]["supported"] is False
        assert modes["in_image_text_edit"]["source"] == "UNSPECIFIED"
        tiers = caps.get("resolution_tiers") or {}
        assert tiers["1K"]["supported"] is True
        assert tiers["4K"]["supported"] is True
        media = caps.get("media") or {}
        assert media["video"] == {"supported": False, "source": "UNSPECIFIED"}
        assert media["audio"]["supported"] is False


# ---------------------------------------------------------------------------
# Discovery fingerprint: admin-declared dims must not wipe VERIFIED.
# ---------------------------------------------------------------------------


def test_discovery_fingerprint_preserves_verified_through_declared_dims(
    db_session,
):
    from app.models import ProviderConnection, ProviderProfile
    from app.services.provider_catalog import _upsert_discovered_models

    profile = ProviderProfile(
        preset_key="p-fp",
        name="fp",
        category="COMPATIBLE",
        enabled=True,
    )
    db_session.add(profile)
    db_session.flush()
    connection = ProviderConnection(
        provider_id=profile.id,
        name="fp-conn",
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
            provider_model_id="fp-image",
            display_name="fp-image",
            model_type="IMAGE",
            input_modalities=["TEXT", "IMAGE"],
            output_modalities=["IMAGE"],
            operations=["image_generate", "image_edit"],
            api_surfaces=["IMAGES"],
            capabilities={
                "structured_output_mode": "JSON_MODE",
                "supported_parameters": [],
                "context_length": None,
                "resolutions": ["1K"],
                "max_reference_images": 1,
                "size_map": {"1K": "1024x1536"},
                # Admin-declared P0-1 dimensions the discovery payload never
                # produces — these must not read as capability drift.
                "edit_modes": {
                    "mask": {"supported": True, "source": "DECLARED"},
                },
                "capability_sources": {"edit_modes.mask": "DECLARED"},
                "media": {"video": {"supported": False, "source": "UNSPECIFIED"}},
            },
            source="DISCOVERED",
            confidence="VERIFIED",
            enabled=True,
            last_verified_at=datetime.now(UTC),
        )
    )
    db_session.commit()

    models = _upsert_discovered_models(
        db_session,
        connection,
        [
            {
                "id": "fp-image",
                "architecture": {
                    "input_modalities": ["text", "image"],
                    "output_modalities": ["image"],
                },
                "capabilities": True,
            }
        ],
    )
    db_session.commit()
    model = models[0]
    # Discovery inferred the same capability keys, so the VERIFIED verdict
    # and the admin-declared dimensions survive the merge.
    assert model.confidence == "VERIFIED"
    assert model.capabilities["edit_modes"]["mask"]["supported"] is True
    assert model.capabilities["capability_sources"]["edit_modes.mask"] == "DECLARED"


# ---------------------------------------------------------------------------
# Router gate + /models serialization.
# ---------------------------------------------------------------------------


def test_router_region_task_refuses_model_without_mask_edit_mode(
    db_session,
):
    from app.config import get_settings
    from app.models import ProviderConnection, ProviderProfile
    from app.services.model_router import resolve_model

    profile = ProviderProfile(
        preset_key="p-gate", name="gate", category="COMPATIBLE", enabled=True
    )
    db_session.add(profile)
    db_session.flush()
    connection = ProviderConnection(
        provider_id=profile.id,
        name="gate-conn",
        protocol="OPENAI",
        base_url="https://example.invalid",
        enabled=True,
        health_state="HEALTHY",
    )
    db_session.add(connection)
    db_session.flush()
    model = AIModel(
        connection_id=connection.id,
        provider_model_id="gate-image",
        display_name="gate-image",
        model_type="IMAGE",
        operations=["image_generate", "image_edit"],
        capabilities={"edit_modes": {
            "whole_image_reference": {"supported": True, "source": "DECLARED"},
        }},
        source="DISCOVERED",
        confidence="VERIFIED",
        enabled=True,
    )
    db_session.add(model)
    db_session.commit()

    with pytest.raises(Exception) as excinfo:
        resolve_model(
            db_session,
            get_settings(),
            operation="image_edit",
            explicit_reference=model.id,
            task_kind="PAGE_REGION_REGENERATE",
        )
    assert getattr(excinfo.value, "status_code", None) == 422
    assert "编辑模式" in str(excinfo.value.detail)

    # The same model is still eligible for a task kind without a required
    # edit mode... but credential checks may stop it; the point is the
    # edit-mode gate must not be the reason.
    try:
        resolve_model(
            db_session,
            get_settings(),
            operation="image_edit",
            explicit_reference=model.id,
            task_kind="PAGE_REPAIR",
        )
    except Exception as error:  # assert gate is not the 422 edit-mode refusal
        assert not (
            getattr(error, "status_code", None) == 422
            and "编辑模式" in str(error.detail)
        )


def test_model_supports_resolution_prefers_structured_tiers():
    from app.services.model_router import model_supports_resolution

    declared = _Model(
        {
            "resolution_tiers": {
                "1K": {"supported": True, "source": "DECLARED"},
                "4K": {"supported": False, "source": "DECLARED"},
            },
            # The legacy list disagrees — the structured map is authoritative.
            "resolutions": ["4K"],
        }
    )
    assert model_supports_resolution(declared, "1K") is True
    assert model_supports_resolution(declared, "4K") is False

    legacy_only = _Model({"resolutions": ["1K"]})
    assert model_supports_resolution(legacy_only, "1K") is True
    assert model_supports_resolution(legacy_only, "4K") is False
    assert model_supports_resolution(_Model({}), "4K") is True


def test_models_endpoint_serializes_structured_dimensions(
    client: TestClient, db_session
):
    ensure_provider_presets(db_session, get_settings(), auto_commit=False)
    db_session.commit()
    response = client.get("/api/v1/models")
    assert response.status_code == 200
    rows = response.json()
    preset = next(item for item in rows if item["logical_alias"] == "image.nano_banana_2")
    assert preset["edit_modes"]["whole_image_reference"] == {
        "supported": True,
        "source": "DECLARED",
    }
    assert preset["edit_modes"]["in_image_text_edit"]["source"] == "UNSPECIFIED"
    tier_names = {tier["name"]: tier for tier in preset["resolution_tiers"]}
    assert tier_names["1K"]["supported"] is True
    assert preset["media"]["video"] == {"supported": False, "source": "UNSPECIFIED"}

    text_model = next(item for item in rows if item["logical_alias"] == "text.fast")
    for mode in EDIT_MODE_KEYS:
        assert text_model["edit_modes"][mode]["supported"] is False
        assert text_model["edit_modes"][mode]["source"] == "UNSPECIFIED"
    assert text_model["media"]["audio"]["supported"] is False


def test_model_edit_mode_supported_wrapper():
    model = _Model({"accepts_explicit_mask": True})
    assert model_edit_mode_supported(model, "mask") is True
    assert model_edit_mode_supported(model, "in_image_text_edit") is False
