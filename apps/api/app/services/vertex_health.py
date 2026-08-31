from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import AIModel, ProviderConnection, ProviderHealth, ProviderProfile
from app.provider_schemas import ConnectionVerifyRequest
from app.services.vertex_credentials import get_vertex_credential_manager
from app.settings_schemas import VertexHealthRead, VertexVerifyRequest

__all__ = ["get_vertex_credential_manager"]

PROVIDER = "vertex-ai"


def _configured(settings: Settings) -> bool:
    return bool(settings.google_cloud_project and settings.google_application_credentials)


def _file_present(settings: Settings) -> bool:
    return bool(
        settings.google_application_credentials
        and settings.google_application_credentials.is_file()
    )


def get_or_create_health(db: Session, settings: Settings) -> ProviderHealth:
    health = db.query(ProviderHealth).filter_by(provider=PROVIDER).one_or_none()
    configured = _configured(settings)
    present = _file_present(settings)
    if not health:
        health = ProviderHealth(
            provider=PROVIDER,
            configured=configured,
            credential_file_present=present,
            health_state="DEGRADED" if configured and present else "UNCONFIGURED",
            message=(
                "服务端凭据已配置，等待首次验证"
                if configured and present
                else "请在服务端配置 Vertex AI 服务账号"
            ),
        )
        db.add(health)
        db.commit()
        db.refresh(health)
        return health

    changed = health.configured != configured or health.credential_file_present != present
    health.configured = configured
    health.credential_file_present = present
    if not configured or not present:
        health.health_state = "UNCONFIGURED"
        health.error_code = "CREDENTIAL_FILE_MISSING" if configured else "NOT_CONFIGURED"
        health.message = "凭据文件不存在" if configured else "请在服务端配置 Vertex AI 服务账号"
        changed = True
    if changed:
        db.commit()
        db.refresh(health)
    return health


def health_read(health: ProviderHealth, settings: Settings) -> VertexHealthRead:
    return VertexHealthRead(
        configured=health.configured,
        health_state=health.health_state,
        credential_file_present=health.credential_file_present,
        project=settings.google_cloud_project,
        location=settings.google_cloud_location,
        text_model=settings.vertex_text_model,
        image_models=[
            settings.vertex_image_model_nano_banana_2,
            settings.vertex_image_model_nano_banana_pro,
        ],
        last_checked_at=health.last_checked_at,
        last_success_at=health.last_success_at,
        token_expires_at=health.token_expires_at,
        consecutive_failures=health.consecutive_failures,
        latency_ms=health.latency_ms,
        error_code=health.error_code,
        message=health.message,
        text_model_access=health.text_model_access,
        image_model_access=health.image_model_access or {},
    )


def sync_connection_to_legacy_health(
    db: Session,
    settings: Settings,
    connection: ProviderConnection,
) -> ProviderHealth:
    """Mirror unified connection health into the one-release legacy shape."""

    health = get_or_create_health(db, settings)
    is_new_result = health.last_checked_at != connection.last_checked_at
    health.health_state = connection.health_state
    health.last_checked_at = connection.last_checked_at
    health.last_success_at = connection.last_success_at
    health.latency_ms = connection.latency_ms
    health.error_code = connection.error_code
    health.message = connection.message
    if connection.health_state == "HEALTHY":
        health.consecutive_failures = 0
    elif connection.error_code and is_new_result:
        health.consecutive_failures += 1
    db.flush()
    return health


def verify_vertex(
    db: Session, settings: Settings, payload: VertexVerifyRequest
) -> VertexHealthRead:
    from app.services.connection_verifier import verify_connection
    from app.services.provider_presets import ensure_provider_presets

    ensure_provider_presets(db, settings, auto_commit=True)
    profile = db.scalar(
        select(ProviderProfile).where(ProviderProfile.preset_key == PROVIDER)
    )
    connection = (
        db.scalar(
            select(ProviderConnection).where(
                ProviderConnection.provider_id == profile.id
            )
        )
        if profile
        else None
    )
    if connection is None:
        raise HTTPException(status_code=404, detail="Vertex 兼容连接不存在")

    catalog_model_id = None
    acknowledge_cost = False
    if payload.level != "CREDENTIALS":
        alias = (
            "text.fast"
            if payload.level == "TEXT_MODEL"
            else payload.image_model_alias
        )
        if alias is None:
            raise HTTPException(status_code=422, detail="图片验证必须明确选择模型")
        model = db.scalar(select(AIModel).where(AIModel.legacy_alias == alias))
        if model is None:
            raise HTTPException(status_code=404, detail="兼容验证模型不存在")
        catalog_model_id = model.id
        acknowledge_cost = model.model_type == "IMAGE"

    verify_connection(
        db,
        settings,
        connection.id,
        ConnectionVerifyRequest(
            level="CREDENTIALS" if payload.level == "CREDENTIALS" else "MODEL_SMOKE",
            catalog_model_id=catalog_model_id,
            acknowledge_cost=acknowledge_cost,
        ),
    )
    health = sync_connection_to_legacy_health(db, settings, connection)
    if payload.level == "CREDENTIALS" and connection.health_state == "HEALTHY":
        if health.text_model_access == "UNAVAILABLE":
            health.text_model_access = "NOT_CHECKED"
        access = dict(health.image_model_access or {})
        health.image_model_access = {
            alias: ("NOT_CHECKED" if state == "UNAVAILABLE" else state)
            for alias, state in access.items()
        }
        health.token_expires_at = get_vertex_credential_manager().token_expiry(
            settings
        )
    elif payload.level == "TEXT_MODEL":
        health.text_model_access = (
            "GRANTED" if connection.health_state == "HEALTHY" else "UNAVAILABLE"
        )
    elif payload.level == "IMAGE_MODEL" and payload.image_model_alias:
        access = dict(health.image_model_access or {})
        access[payload.image_model_alias] = (
            "GRANTED" if connection.health_state == "HEALTHY" else "UNAVAILABLE"
        )
        health.image_model_access = access
    db.commit()
    db.refresh(health)
    return health_read(health, settings)
