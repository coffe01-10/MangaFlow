from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import Settings
from app.model_adapters.base import ImageRequest, StructuredRequest
from app.model_adapters.vertex import VertexImageAdapter, VertexTextAdapter
from app.models import ProviderHealth
from app.services.model_registry import build_registry
from app.services.vertex_credentials import (
    classify_vertex_failure,
    get_vertex_credential_manager,
)
from app.settings_schemas import VertexHealthRead, VertexVerifyRequest

PROVIDER = "vertex-ai"


class _TextSmokeResult(BaseModel):
    ok: bool


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


def verify_vertex(
    db: Session, settings: Settings, payload: VertexVerifyRequest
) -> VertexHealthRead:
    health = get_or_create_health(db, settings)
    if not health.configured or not health.credential_file_present:
        return health_read(health, settings)
    if payload.level == "IMAGE_MODEL" and payload.image_model_alias is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=422, detail="图片验证必须明确选择 Nano Banana 2 或 Pro")

    health.health_state = "CHECKING"
    health.last_checked_at = datetime.now(UTC)
    health.message = "正在执行显式联网验证"
    db.commit()
    started = perf_counter()
    manager = get_vertex_credential_manager()
    try:
        if payload.level == "CREDENTIALS":
            # Creating the client refreshes OAuth when required but never calls a model.
            manager.execute(settings, lambda _client: True)
            # A successful OAuth refresh proves the previous network outage is over, but
            # it does not prove model access. Clear only transient results; permanent
            # permission denials remain visible until that model is explicitly verified.
            if health.text_model_access == "UNAVAILABLE":
                health.text_model_access = "NOT_CHECKED"
            access = dict(health.image_model_access or {})
            health.image_model_access = {
                alias: ("NOT_CHECKED" if state == "UNAVAILABLE" else state)
                for alias, state in access.items()
            }
            message = "Vertex AI 服务账号验证成功"
        elif payload.level == "TEXT_MODEL":
            adapter = VertexTextAdapter(settings, build_registry(settings)["text.fast"])
            adapter.generate_structured(
                StructuredRequest(
                    prompt='只返回 {"ok": true}',
                    temperature=0,
                    metadata={"max_output_tokens": 64, "thinking_budget": 0},
                ),
                _TextSmokeResult,
            )
            health.text_model_access = "GRANTED"
            message = "Gemini 3.5 Flash 低 token 验证成功"
        else:
            assert payload.image_model_alias is not None
            adapter = VertexImageAdapter(
                settings, build_registry(settings)[payload.image_model_alias]
            )
            adapter.generate_asset(
                ImageRequest(
                    prompt="一枚简单的黑色圆点，白色背景，无文字",
                    resolution="1K",
                    aspect_ratio="1:1",
                )
            )
            access = dict(health.image_model_access or {})
            access[payload.image_model_alias] = "GRANTED"
            health.image_model_access = access
            message = "所选图片模型 1K 验证成功"

        now = datetime.now(UTC)
        health.health_state = "HEALTHY"
        health.last_success_at = now
        health.consecutive_failures = 0
        health.error_code = None
        health.message = message
        health.token_expires_at = manager.token_expiry(settings)
    except Exception as error:
        failure = classify_vertex_failure(error)
        health.consecutive_failures += 1
        health.error_code = failure.code
        health.message = failure.message
        # A provider/network failure does not erase a valid local configuration.
        health.health_state = (
            "DEGRADED"
            if health.last_success_at is not None
            or failure.code in {"PERMISSION", "RATE_LIMIT", "TIMEOUT", "UPSTREAM"}
            else "OFFLINE"
        )
        if payload.level == "TEXT_MODEL":
            health.text_model_access = (
                "DENIED" if failure.code in {"PERMISSION", "MODEL_NOT_FOUND"} else "UNAVAILABLE"
            )
        elif payload.level == "IMAGE_MODEL" and payload.image_model_alias:
            access = dict(health.image_model_access or {})
            access[payload.image_model_alias] = (
                "DENIED" if failure.code in {"PERMISSION", "MODEL_NOT_FOUND"} else "UNAVAILABLE"
            )
            health.image_model_access = access
    finally:
        health.last_checked_at = datetime.now(UTC)
        health.latency_ms = round((perf_counter() - started) * 1000)
        db.commit()
        db.refresh(health)
    return health_read(health, settings)
