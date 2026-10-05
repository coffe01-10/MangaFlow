"""P0-2 模型目录生命周期管理 regression coverage.

Pins the three-state contract from docs/market-research/功能计划建议.md:

- ``lifecycle`` column + ``sunset_at`` on ``ai_models`` with the
  ``ck_ai_models_lifecycle`` CHECK; the hand-maintained sunset table in
  ``services/model_sunsets.py`` overrides the stored column.
- Explicit dispatch of a DEPRECATED/EOL row for a new-artifact task kind is
  a 409 carrying a migration hint; derived-maintenance kinds
  (PAGE_REPAIR/PAGE_UPSCALE/PAGE_REGION_REGENERATE) pass with a WARN; AUTO
  routing excludes non-ACTIVE rows unconditionally.
- ``catalog_model_is_available`` keeps catalog-semantics counting for
  retired rows while ``phase="dispatch"`` refuses them.
- GET /models serializes lifecycle/sunset_at/successor; the admin PATCH
  accepts ``lifecycle``/``sunset_at``; MODEL_SMOKE verification skips EOL
  rows without touching connection health.
"""

from datetime import UTC, date, datetime
from unittest.mock import patch

import pytest
from app.config import get_settings
from app.models import AIModel, ProviderConnection, ProviderKey, ProviderProfile
from app.provider_schemas import ProviderModelUpdate
from app.services.model_availability import catalog_model_is_available
from app.services.model_router import resolve_model
from app.services.model_sunsets import (
    LIFECYCLE_ACTIVE,
    LIFECYCLE_DEPRECATED,
    LIFECYCLE_EOL,
    ModelSunset,
    lifecycle_sunset_at,
    model_lifecycle_state,
)
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import update


def _connection(
    db_session, *, protocol: str = "OPENAI", with_key: bool = True
) -> ProviderConnection:
    profile = ProviderProfile(
        name=f"生命周期供应商-{protocol}",
        category="CUSTOM",
        enabled=True,
    )
    db_session.add(profile)
    db_session.flush()
    connection = ProviderConnection(
        provider_id=profile.id,
        name="生命周期连接",
        protocol=protocol,
        base_url="https://lifecycle.example.com/v1",
        enabled=True,
        health_state="HEALTHY",
    )
    db_session.add(connection)
    db_session.flush()
    if with_key:
        # AUTO/explicit resolution gates on a usable non-cooled key for
        # CONNECTION_KEY protocols; the secret content is never read.
        db_session.add(
            ProviderKey(
                connection_id=connection.id,
                label="default",
                encrypted_secret="not-a-real-secret",
                enabled=True,
            )
        )
        db_session.flush()
    return connection


def _model(
    db_session,
    connection: ProviderConnection,
    provider_model_id: str,
    *,
    lifecycle: str = LIFECYCLE_ACTIVE,
    model_type: str = "IMAGE",
    operations: list[str] | None = None,
    capabilities: dict | None = None,
) -> AIModel:
    model = AIModel(
        connection_id=connection.id,
        provider_model_id=provider_model_id,
        display_name=provider_model_id,
        model_type=model_type,
        input_modalities=["TEXT", "IMAGE"] if model_type == "IMAGE" else ["TEXT"],
        output_modalities=["IMAGE"] if model_type == "IMAGE" else ["TEXT"],
        operations=operations or ["image_generate", "image_edit"],
        capabilities=capabilities or {},
        source="DISCOVERED",
        confidence="VERIFIED",
        enabled=True,
        lifecycle=lifecycle,
        last_verified_at=datetime.now(UTC),
    )
    db_session.add(model)
    db_session.commit()
    return model


# ---------------------------------------------------------------------------
# model_lifecycle_state: sunset table overrides the column.
# ---------------------------------------------------------------------------


def test_lifecycle_state_reads_column_then_sunset_table():
    class _Row:
        provider_model_id = "retired-model"
        lifecycle = LIFECYCLE_DEPRECATED
        sunset_at = None

    assert model_lifecycle_state(_Row()) == LIFECYCLE_DEPRECATED
    assert model_lifecycle_state(_Row(), today=date(2026, 10, 5)) == LIFECYCLE_DEPRECATED

    active = _Row()
    active.lifecycle = LIFECYCLE_ACTIVE
    entry = ModelSunset(eol_date=date(2026, 10, 1), source_url="https://example.invalid/announce")
    with patch(
        "app.services.model_sunsets.MODEL_SUNSETS", {"retired-model": entry}
    ):
        # Column says ACTIVE but the sunset table forces EOL once past the date.
        assert model_lifecycle_state(active, today=date(2026, 10, 5)) == LIFECYCLE_EOL
        # Before the EOL date the table is silent and the column wins.
        assert model_lifecycle_state(active, today=date(2026, 9, 1)) == LIFECYCLE_ACTIVE


def test_lifecycle_sunset_between_dates_reads_deprecated():
    class _Row:
        provider_model_id = "sunsetting"
        lifecycle = LIFECYCLE_ACTIVE
        sunset_at = None

    entry = ModelSunset(
        sunset_date=date(2026, 10, 1), eol_date=date(2026, 12, 31)
    )
    with patch("app.services.model_sunsets.MODEL_SUNSETS", {"sunsetting": entry}):
        assert model_lifecycle_state(_Row(), today=date(2026, 10, 5)) == LIFECYCLE_DEPRECATED
        assert model_lifecycle_state(_Row(), today=date(2026, 9, 30)) == LIFECYCLE_ACTIVE
        # Serialization falls back to the table's sunset date when the column is null.
        assert lifecycle_sunset_at(_Row()) == date(2026, 10, 1)


def test_lifecycle_state_unknown_column_reads_active():
    class _Row:
        provider_model_id = "plain"
        lifecycle = "GARBAGE"
        sunset_at = None

    assert model_lifecycle_state(_Row()) == LIFECYCLE_ACTIVE


def test_lifecycle_state_takes_stricter_of_column_and_sunset_table():
    """Severity only escalates: an operator-set EOL column must not be read
    back as the table's weaker DEPRECATED, and a table EOL overrides a stored
    DEPRECATED."""
    class _Row:
        provider_model_id = "windowed"
        lifecycle = LIFECYCLE_EOL
        sunset_at = None

    # Table is mid-window (DEPRECATED) but the operator already confirmed EOL.
    entry = ModelSunset(
        sunset_date=date(2026, 10, 1), eol_date=date(2027, 1, 1)
    )
    with patch("app.services.model_sunsets.MODEL_SUNSETS", {"windowed": entry}):
        assert model_lifecycle_state(_Row(), today=date(2026, 10, 5)) == LIFECYCLE_EOL

        deprecated_row = _Row()
        deprecated_row.lifecycle = LIFECYCLE_DEPRECATED
        assert (
            model_lifecycle_state(deprecated_row, today=date(2026, 10, 5))
            == LIFECYCLE_DEPRECATED
        )

    # Reverse direction: table EOL past its date outranks a stored DEPRECATED.
    eol_entry = ModelSunset(eol_date=date(2026, 10, 1))
    stored_deprecated = _Row()
    stored_deprecated.lifecycle = LIFECYCLE_DEPRECATED
    with patch("app.services.model_sunsets.MODEL_SUNSETS", {"windowed": eol_entry}):
        assert (
            model_lifecycle_state(stored_deprecated, today=date(2026, 10, 5))
            == LIFECYCLE_EOL
        )


def test_discovery_rediscovery_keeps_declared_capability_dimensions(db_session):
    """P0-1: rediscovery merges fresh inference with admin/preset declarations.

    ``_infer_model`` rewrites the discovery-owned keys only — declaration-only
    dimensions (edit_modes, media, region bits, provenance maps) must survive
    on every row, including DECLARED-confidence rows that never enter the
    preserve_verification branch.
    """
    connection = _connection(db_session)
    model = _model(
        db_session,
        connection,
        "rediscovered-image",
        capabilities={
            "resolutions": ["1K"],
            "edit_modes": {
                "mask": {"supported": True, "source": "DECLARED"},
            },
            "capability_sources": {"edit_modes.mask": "DECLARED"},
            "accepts_explicit_mask": True,
        },
    )
    from app.services.provider_catalog import _upsert_discovered_models

    _upsert_discovered_models(
        db_session,
        connection,
        [
            {
                "id": "rediscovered-image",
                "architecture": {
                    "input_modalities": ["text", "image"],
                    "output_modalities": ["image"],
                },
            }
        ],
    )
    db_session.commit()
    db_session.refresh(model)
    assert model.capabilities["edit_modes"]["mask"]["supported"] is True
    assert model.capabilities["accepts_explicit_mask"] is True
    assert model.capabilities["capability_sources"]["edit_modes.mask"] == "DECLARED"
    # Fresh inference still rewrites the discovery-owned keys.
    assert model.capabilities["resolutions"] == ["1K"]


# ---------------------------------------------------------------------------
# Availability phases: catalog counts retired rows, dispatch refuses them.
# ---------------------------------------------------------------------------


def test_availability_phases_differ_on_retired_rows(db_session):
    connection = _connection(db_session)
    profile = db_session.get(ProviderProfile, connection.provider_id)
    deprecated = _model(db_session, connection, "deprecated-row", lifecycle="DEPRECATED")

    # CONNECTION_KEY protocol: a usable key + writable store means the row is
    # "available" for catalog purposes; only the dispatch phase also gates on
    # lifecycle.
    kwargs = dict(
        credentials_writable=True,
        has_usable_key=True,
        environment_credentials_ready=False,
    )
    assert catalog_model_is_available(
        deprecated, connection, profile, **kwargs
    ) is True
    assert catalog_model_is_available(
        deprecated, connection, profile, phase="dispatch", **kwargs
    ) is False


# ---------------------------------------------------------------------------
# Router: explicit new-artifact 409, derived WARN pass-through, AUTO exclude.
# ---------------------------------------------------------------------------


def test_deprecated_explicit_new_generation_refuses_with_successor(
    db_session,
):
    connection = _connection(db_session)
    deprecated = _model(
        db_session, connection, "old-image", lifecycle=LIFECYCLE_DEPRECATED
    )
    # A same-connection ACTIVE+VERIFIED row covering the same operations is
    # offered as the migration successor.
    successor = _model(db_session, connection, "new-image")
    with pytest.raises(HTTPException) as excinfo:
        resolve_model(
            db_session,
            get_settings(),
            operation="image_edit",
            explicit_reference=deprecated.id,
            task_kind="PAGE_GENERATE",
        )
    assert excinfo.value.status_code == 409
    detail = excinfo.value.detail
    assert detail["code"] == "MODEL_DEPRECATED"
    assert detail["lifecycle"] == "DEPRECATED"
    assert detail["successor"] in {successor.id, successor.provider_model_id,
                                   successor.legacy_alias}


def test_eol_explicit_new_generation_refuses(db_session):
    connection = _connection(db_session)
    eol = _model(db_session, connection, "dead-model", lifecycle=LIFECYCLE_EOL)
    with pytest.raises(HTTPException) as excinfo:
        resolve_model(
            db_session,
            get_settings(),
            operation="image_edit",
            explicit_reference=eol.id,
            task_kind="PAGE_GENERATE",
        )
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["code"] == "MODEL_EOL"


def test_deprecated_repair_dispatch_passes_with_warn(db_session, caplog):
    """PAGE_REPAIR maintains an adopted candidate — WARN, not a refusal."""
    connection = _connection(db_session)
    deprecated = _model(
        db_session, connection, "repairable", lifecycle=LIFECYCLE_DEPRECATED
    )
    with caplog.at_level("WARNING", logger="mangaflow.model_router"):
        # The lifecycle gate must not raise; credential checks afterwards may
        # still fail (no key on the test connection), which proves the WARN
        # path rather than the refusal path was taken.
        try:
            resolve_model(
                db_session,
                get_settings(),
                operation="image_edit",
                explicit_reference=deprecated.id,
                task_kind="PAGE_REPAIR",
            )
        except HTTPException as error:
            assert error.status_code != 409 or (
                isinstance(error.detail, dict)
                and error.detail.get("code") != "MODEL_DEPRECATED"
            ) or "已退役" not in str(error.detail)
    assert any(
        "DEPRECATED" in record.message or "deprecated" in record.message.lower()
        for record in caplog.records
    )


def test_auto_routing_excludes_retired_rows(db_session):
    connection = _connection(db_session)
    _model(db_session, connection, "auto-retired", lifecycle=LIFECYCLE_EOL)
    active = _model(
        db_session,
        connection,
        "auto-active",
        model_type="TEXT",
        operations=["structured_text"],
    )
    # The retired row must not appear in AUTO candidates; the active one wins.
    resolved = resolve_model(
        db_session,
        get_settings(),
        operation="structured_text",
    )
    assert resolved.model.id == active.id


def test_resolve_without_db_kwarg_still_refuses(db_session):
    """The db=None degraded path keeps the refusal (no migration hint)."""
    connection = _connection(db_session)
    eol = _model(db_session, connection, "no-db-eol", lifecycle=LIFECYCLE_EOL)
    from app.services.model_router import _require_eligible, _resolved_row

    resolved = _resolved_row(db_session, eol)
    with pytest.raises(HTTPException) as excinfo:
        _require_eligible(resolved, "image_edit", explicit=True, task_kind="PAGE_GENERATE")
    assert excinfo.value.status_code == 409


# ---------------------------------------------------------------------------
# Schema + PATCH write path.
# ---------------------------------------------------------------------------


def test_lifecycle_check_constraint_rejects_bad_value(db_session):
    connection = _connection(db_session)
    model = _model(db_session, connection, "check-bad")
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        db_session.execute(
            update(AIModel)
            .where(AIModel.id == model.id)
            .values(lifecycle="RETIRED")
        )
        db_session.commit()
    db_session.rollback()


def test_patch_model_accepts_lifecycle_and_sunset(client, db_session):
    connection = _connection(db_session)
    model = _model(db_session, connection, "patch-lifecycle")
    response = client.patch(
        f"/api/v1/providers/models/{model.id}",
        json={
            "lifecycle": "DEPRECATED",
            "sunset_at": "2026-11-01T00:00:00Z",
            "version": model.version,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["lifecycle"] == "DEPRECATED"
    assert body["sunset_at"].startswith("2026-11-01")
    db_session.refresh(model)
    assert model.lifecycle == "DEPRECATED"


def test_patch_model_rejects_bad_lifecycle_value(client, db_session):
    connection = _connection(db_session)
    model = _model(db_session, connection, "patch-bad-lifecycle")
    response = client.patch(
        f"/api/v1/providers/models/{model.id}",
        json={"lifecycle": "SLEEPING", "version": model.version},
    )
    assert response.status_code == 422


def test_provider_model_update_rejects_naive_sunset():
    with pytest.raises(ValidationError):
        ProviderModelUpdate(
            lifecycle="DEPRECATED",
            sunset_at=datetime(2026, 11, 1),
            version=1,
        )


# ---------------------------------------------------------------------------
# Serialization + verifier semantics.
# ---------------------------------------------------------------------------


def test_models_endpoint_serializes_lifecycle(client, db_session):
    connection = _connection(db_session)
    deprecated = _model(
        db_session,
        connection,
        "catalog-deprecated",
        lifecycle=LIFECYCLE_DEPRECATED,
    )
    entry = ModelSunset(
        sunset_date=date(2026, 10, 1),
        successor_hint="successor-model",
    )
    with patch(
        "app.services.model_sunsets.MODEL_SUNSETS",
        {"catalog-deprecated": entry},
    ):
        response = client.get("/api/v1/models")
    assert response.status_code == 200
    row = next(
        item for item in response.json() if item["catalog_id"] == deprecated.id
    )
    assert row["lifecycle"] == "DEPRECATED"
    assert row["successor"] == "successor-model"
    assert row["sunset_at"] is not None


def test_verify_connection_skips_eol_model(client, db_session):
    """MODEL_SMOKE on an EOL row refuses before probing; health untouched."""
    connection = _connection(db_session)
    model = _model(db_session, connection, "eol-smoke", lifecycle=LIFECYCLE_EOL)
    response = client.post(
        f"/api/v1/providers/connections/{connection.id}/verify",
        json={
            "level": "MODEL_SMOKE",
            "catalog_model_id": model.id,
            "acknowledge_cost": True,
        },
    )
    assert response.status_code == 409
    db_session.refresh(connection)
    assert connection.health_state == "HEALTHY"


def test_discovery_sync_stamps_sunset_lifecycle(db_session):
    """A rediscovered sunset-table model becomes EOL/DEPRECATED, never ACTIVE."""
    connection = _connection(db_session)
    model = _model(
        db_session,
        connection,
        "rediscovered-eol",
        lifecycle=LIFECYCLE_EOL,
    )
    from app.services.provider_catalog import _upsert_discovered_models

    entry = ModelSunset(eol_date=date(2026, 10, 1))
    with patch(
        "app.services.model_sunsets.MODEL_SUNSETS", {"rediscovered-eol": entry}
    ):
        _upsert_discovered_models(
            db_session,
            connection,
            [{"id": "rediscovered-eol"}],
        )
        db_session.commit()
    db_session.refresh(model)
    # EOL stays EOL — rediscovery must not silently revive a shutdown row.
    assert model.lifecycle == LIFECYCLE_EOL
    assert model.enabled is True  # history-readable; enabled is not the gate


def test_sunset_listed_discovery_marks_deprecated(db_session):
    """A sunset-dated (not yet EOL) row stamps DEPRECATED on discovery sync."""
    connection = _connection(db_session)
    model = _model(db_session, connection, "sunsetting-row")
    from app.services.provider_catalog import _upsert_discovered_models

    entry = ModelSunset(
        sunset_date=date(2026, 10, 1), eol_date=date(2027, 1, 1)
    )
    with patch(
        "app.services.model_sunsets.MODEL_SUNSETS", {"sunsetting-row": entry}
    ):
        _upsert_discovered_models(
            db_session, connection, [{"id": "sunsetting-row"}]
        )
        db_session.commit()
    db_session.refresh(model)
    assert model.lifecycle == LIFECYCLE_DEPRECATED
