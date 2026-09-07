from datetime import UTC, datetime

from sqlalchemy import update

from app.config import get_settings
from app.models import AIModel, ProviderConnection, ProviderProfile
from app.services.model_router import resolve_model


def _connection(db_session, *, protocol: str = "OPENAI") -> ProviderConnection:
    profile = ProviderProfile(
        name=f"展示偏好供应商-{protocol}",
        category="CUSTOM",
        enabled=True,
    )
    db_session.add(profile)
    db_session.flush()
    connection = ProviderConnection(
        provider_id=profile.id,
        name="展示偏好连接",
        protocol=protocol,
        base_url=(
            "vertex://visibility-test"
            if protocol == "VERTEX_NATIVE"
            else "https://visibility.example.com/v1"
        ),
        enabled=True,
        health_state="HEALTHY",
    )
    db_session.add(connection)
    db_session.flush()
    return connection


def _model(
    db_session,
    connection: ProviderConnection,
    provider_model_id: str,
    *,
    display_enabled: bool = True,
    version: int = 1,
) -> AIModel:
    model = AIModel(
        connection_id=connection.id,
        provider_model_id=provider_model_id,
        display_name=provider_model_id,
        model_type="TEXT",
        input_modalities=["TEXT"],
        output_modalities=["TEXT"],
        operations=["structured_text"],
        source="DISCOVERED",
        confidence="VERIFIED",
        enabled=True,
        display_enabled=display_enabled,
        priority=100,
        version=version,
        last_verified_at=datetime.now(UTC),
    )
    db_session.add(model)
    db_session.commit()
    return model


def test_model_catalog_exposes_accepts_explicit_mask(client, db_session):
    """V02-43B: the /models catalog carries the V02-42B mask capability bit."""
    connection = _connection(db_session)
    masked = AIModel(
        connection_id=connection.id,
        provider_model_id="mask-capable",
        display_name="mask-capable",
        model_type="IMAGE",
        input_modalities=["TEXT", "IMAGE"],
        output_modalities=["IMAGE"],
        operations=["image_gen", "image_edit"],
        capabilities={"accepts_explicit_mask": True, "resolutions": ["1K"]},
        source="DISCOVERED",
        confidence="VERIFIED",
        enabled=True,
        display_enabled=True,
        priority=100,
        last_verified_at=datetime.now(UTC),
    )
    plain = _model(db_session, connection, "no-mask-bit")
    db_session.add(masked)
    db_session.commit()

    catalog = client.get("/api/v1/models")
    assert catalog.status_code == 200
    rows = {item["catalog_id"]: item for item in catalog.json()}
    assert rows[masked.id]["accepts_explicit_mask"] is True
    assert rows[plain.id]["accepts_explicit_mask"] is False


def test_single_visibility_patch_preserves_capability_metadata(client, db_session):
    connection = _connection(db_session)
    model = _model(db_session, connection, "single-visibility")

    hidden = client.patch(
        f"/api/v1/providers/models/{model.id}",
        json={"display_enabled": False, "version": model.version},
    )

    assert hidden.status_code == 200
    hidden_body = hidden.json()
    assert hidden_body["display_enabled"] is False
    assert hidden_body["version"] == 2
    assert hidden_body["source"] == "DISCOVERED"
    assert hidden_body["confidence"] == "VERIFIED"

    mixed = client.patch(
        f"/api/v1/providers/models/{model.id}",
        json={
            "display_enabled": True,
            "display_name": "手工名称",
            "version": hidden_body["version"],
        },
    )

    assert mixed.status_code == 200
    mixed_body = mixed.json()
    assert mixed_body["display_enabled"] is True
    assert mixed_body["display_name"] == "手工名称"
    assert mixed_body["source"] == "MANUAL"
    assert mixed_body["confidence"] == "MANUAL"


def test_single_visibility_patch_cannot_overwrite_concurrent_version(client, db_session):
    connection = _connection(db_session)
    model = _model(db_session, connection, "concurrent-visibility")

    db_session.execute(
        update(AIModel)
        .where(AIModel.id == model.id)
        .values(display_name="并发修改", version=2)
        .execution_options(synchronize_session=False)
    )
    db_session.commit()

    response = client.patch(
        f"/api/v1/providers/models/{model.id}",
        json={"display_enabled": False, "version": 1},
    )

    assert response.status_code == 409
    db_session.expire_all()
    current = db_session.get(AIModel, model.id)
    assert current.display_name == "并发修改"
    assert current.display_enabled is True
    assert current.version == 2


def test_connection_model_management_list_includes_visibility(client, db_session):
    connection = _connection(db_session)
    hidden = _model(
        db_session,
        connection,
        "managed-hidden",
        display_enabled=False,
    )

    response = client.get(
        f"/api/v1/providers/connections/{connection.id}/models"
    )

    assert response.status_code == 200
    row = next(item for item in response.json() if item["id"] == hidden.id)
    assert row["display_enabled"] is False
    assert row["enabled"] is True
    assert row["source"] == "DISCOVERED"
    assert row["confidence"] == "VERIFIED"
    assert row["last_verified_at"] is not None
    missing = client.get(
        "/api/v1/providers/connections/missing-connection/models"
    )
    assert missing.status_code == 404


def test_visibility_batch_partially_succeeds_and_is_idempotent(client, db_session):
    connection = _connection(db_session)
    changed = _model(db_session, connection, "batch-change")
    already_hidden = _model(
        db_session,
        connection,
        "batch-idempotent",
        display_enabled=False,
        version=3,
    )
    stale = _model(db_session, connection, "batch-stale", version=2)
    orphan = _model(db_session, connection, "batch-orphan")

    raw_connection = db_session.connection()
    raw_connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
    raw_connection.exec_driver_sql(
        "UPDATE ai_models SET connection_id = ? WHERE id = ?",
        ("missing-connection", orphan.id),
    )
    db_session.commit()
    raw_connection = db_session.connection()
    raw_connection.exec_driver_sql("PRAGMA foreign_keys=ON")
    db_session.commit()
    db_session.expire_all()

    response = client.patch(
        "/api/v1/providers/models/visibility",
        json={
            "items": [
                {"model_id": changed.id, "expected_version": 1},
                {"model_id": already_hidden.id, "expected_version": 1},
                {"model_id": stale.id, "expected_version": 1},
                {"model_id": "missing-model", "expected_version": 1},
                {"model_id": orphan.id, "expected_version": 1},
            ],
            "display_enabled": False,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert {item["model_id"]: item["version"] for item in body["updated"]} == {
        changed.id: 2,
    }
    failures = {item["model_id"]: item for item in body["failed"]}
    assert failures[stale.id]["error_code"] == "VERSION_CONFLICT"
    assert failures[stale.id]["current_version"] == 2
    # The idempotent branch still honors the optimistic token: already_hidden
    # matches the target value but its expected_version is stale (1 vs 3).
    assert failures[already_hidden.id]["error_code"] == "VERSION_CONFLICT"
    assert failures[already_hidden.id]["current_version"] == 3
    assert failures["missing-model"]["error_code"] == "MODEL_NOT_FOUND"
    assert failures[orphan.id]["error_code"] == "CONNECTION_MISSING"

    # A matching token makes the same no-op write an idempotent success
    # without bumping the version.
    retried_hidden = client.patch(
        "/api/v1/providers/models/visibility",
        json={
            "items": [{"model_id": already_hidden.id, "expected_version": 3}],
            "display_enabled": False,
        },
    )
    assert retried_hidden.status_code == 200
    assert retried_hidden.json() == {
        "updated": [{"model_id": already_hidden.id, "version": 3}],
        "failed": [],
    }

    db_session.expire_all()
    assert db_session.get(AIModel, changed.id).display_enabled is False
    assert db_session.get(AIModel, changed.id).version == 2
    assert db_session.get(AIModel, stale.id).display_enabled is True

    # A stale-token retry of an already-applied item is a version conflict;
    # retrying with the current token is the idempotent success.
    stale_retry = client.patch(
        "/api/v1/providers/models/visibility",
        json={
            "items": [{"model_id": changed.id, "expected_version": 1}],
            "display_enabled": False,
        },
    )
    assert stale_retry.status_code == 200
    assert stale_retry.json() == {
        "updated": [],
        "failed": [
            {
                "model_id": changed.id,
                "error_code": "VERSION_CONFLICT",
                "message": "模型设置已更新，请刷新后重试",
                "current_version": 2,
            }
        ],
    }

    retried = client.patch(
        "/api/v1/providers/models/visibility",
        json={
            "items": [{"model_id": changed.id, "expected_version": 2}],
            "display_enabled": False,
        },
    )
    assert retried.status_code == 200
    assert retried.json() == {
        "updated": [{"model_id": changed.id, "version": 2}],
        "failed": [],
    }


def test_visibility_batch_rejects_invalid_or_expanded_payloads(client):
    assert client.patch(
        "/api/v1/providers/models/visibility",
        json={"items": [], "display_enabled": False},
    ).status_code == 422
    assert client.patch(
        "/api/v1/providers/models/visibility",
        json={
            "items": [
                {"model_id": f"model-{index}", "expected_version": 1}
                for index in range(101)
            ],
            "display_enabled": False,
        },
    ).status_code == 422
    assert client.patch(
        "/api/v1/providers/models/visibility",
        json={
            "items": [{"model_id": "model", "expected_version": 1}],
            "display_enabled": False,
            "enabled": False,
        },
    ).status_code == 422


def test_hidden_model_remains_available_and_routable(client, db_session):
    connection = _connection(db_session, protocol="VERTEX_NATIVE")
    model = _model(
        db_session,
        connection,
        "hidden-but-routable",
        display_enabled=False,
    )

    catalog = client.get("/api/v1/models")
    assert catalog.status_code == 200
    row = next(item for item in catalog.json() if item["catalog_id"] == model.id)
    assert row["enabled"] is True
    assert row["display_enabled"] is False

    explicit = resolve_model(
        db_session,
        get_settings(),
        operation="structured_text",
        explicit_reference=model.id,
    )
    automatic = resolve_model(
        db_session,
        get_settings(),
        operation="structured_text",
        explicit_reference="auto",
        task_kind="VISIBILITY_TEST",
    )
    assert explicit.model.id == model.id
    assert automatic.model.id == model.id


def test_update_model_claims_version_atomically(client, db_session, monkeypatch):
    """A writer winning between the pre-check and the commit must 409, not
    silently drop the concurrent edit (check-then-write lost update)."""

    import sqlalchemy

    from app.services import provider_catalog

    connection = _connection(db_session)
    model = _model(db_session, connection, "atomic-claim")

    def concurrent_bump(_connection, *_args, **_kwargs):
        db_session.execute(
            sqlalchemy.update(AIModel)
            .where(AIModel.id == model.id)
            .values(version=AIModel.version + 1)
        )
        db_session.commit()

    monkeypatch.setattr(
        provider_catalog, "_validate_protocol_capabilities", concurrent_bump
    )

    response = client.patch(
        f"/api/v1/providers/models/{model.id}",
        json={"version": 1, "priority": 77},
    )

    assert response.status_code == 409
    db_session.expire_all()
    row = db_session.get(AIModel, model.id)
    assert row.version == 2  # only the concurrent writer's bump survived
    assert row.priority != 77
