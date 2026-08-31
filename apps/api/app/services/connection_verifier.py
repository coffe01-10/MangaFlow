from __future__ import annotations

import io
from datetime import UTC, datetime
from statistics import median
from time import perf_counter

from fastapi import HTTPException
from PIL import Image
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.model_adapters.base import (
    ImageRequest,
    MultimodalRequest,
    ProviderAdapterError,
    StructuredRequest,
)
from app.models import AIModel, ModelProbe, ProviderConnection, ProviderKey
from app.provider_schemas import ConnectionVerifyRequest
from app.services.credential_crypto import mark_key_failure, mark_key_success
from app.services.credential_source import (
    CONNECTION_KEY,
    connection_credential_source,
    connection_protocol_capabilities,
)
from app.services.model_router import bind_adapter
from app.services.provider_catalog import (
    connection_is_configured,
    create_probe,
    probe_connection_credentials,
)
from app.services.vertex_credentials import (
    classify_vertex_failure,
)


class _SmokeResult(BaseModel):
    ok: bool


def _connection_keys(db: Session, connection_id: str) -> list[ProviderKey]:
    return list(
        db.scalars(
            select(ProviderKey).where(ProviderKey.connection_id == connection_id)
        )
    )


def connection_health_view(
    db: Session, settings: Settings, connection: ProviderConnection
) -> dict:
    capabilities = connection_protocol_capabilities(connection)
    source = connection_credential_source(connection)
    return {
        "connection_id": connection.id,
        "configured": connection_is_configured(
            settings, connection, _connection_keys(db, connection.id)
        ),
        "credential_source": source,
        "supports_model_discovery": capabilities.supports_model_discovery,
        "supports_balance": bool(
            (connection.balance_config or {}).get("enabled")
            and (connection.balance_config or {}).get("path")
        ),
        "supported_model_types": list(capabilities.supported_model_types),
        "health_state": connection.health_state,
        "last_checked_at": connection.last_checked_at,
        "last_success_at": connection.last_success_at,
        "latency_ms": connection.latency_ms,
        "error_code": connection.error_code,
        "message": connection.message,
    }


def get_connection_health(
    db: Session, settings: Settings, connection_id: str
) -> dict:
    connection = db.get(ProviderConnection, connection_id)
    if connection is None:
        raise HTTPException(status_code=404, detail="供应商连接不存在")
    return connection_health_view(db, settings, connection)


def _sync_legacy_health(
    db: Session, settings: Settings, connection: ProviderConnection
) -> None:
    if connection_credential_source(connection) != "ENV_SERVICE_ACCOUNT":
        return
    # Compatibility is intentionally isolated here; the connection row remains
    # the unified health source used by new endpoints.
    from app.services.vertex_health import sync_connection_to_legacy_health

    sync_connection_to_legacy_health(db, settings, connection)


def _failed_probe(
    db: Session,
    connection: ProviderConnection,
    *,
    model_id: str | None,
    probe_type: str,
    error_code: str,
    message: str,
    latency_ms: int | None,
) -> ModelProbe:
    return create_probe(
        db,
        connection_id=connection.id,
        model_id=model_id,
        probe_type=probe_type,
        status="FAILED",
        latency_ms=latency_ms,
        error_code=error_code,
        message=message,
    )


def _verify_credentials(
    db: Session, settings: Settings, connection: ProviderConnection
) -> ModelProbe:
    started = perf_counter()
    source = connection_credential_source(connection)
    if source == CONNECTION_KEY:
        try:
            metrics = probe_connection_credentials(db, settings, connection)
        except HTTPException as error:
            db.refresh(connection)
            if connection.health_state == "CHECKING":
                connection.health_state = (
                    "UNCONFIGURED" if error.status_code == 409 else "DEGRADED"
                )
                connection.last_checked_at = datetime.now(UTC)
                connection.error_code = (
                    "NO_USABLE_KEY" if error.status_code == 409 else "CONNECTION_FAILED"
                )
                connection.message = str(error.detail)
                connection.latency_ms = round((perf_counter() - started) * 1000)
                db.commit()
            return _failed_probe(
                db,
                connection,
                model_id=None,
                probe_type="CREDENTIALS",
                error_code=connection.error_code or "CONNECTION_FAILED",
                message=str(error.detail),
                latency_ms=round((perf_counter() - started) * 1000),
            )
        return create_probe(
            db,
            connection_id=connection.id,
            model_id=None,
            probe_type="CREDENTIALS",
            status="PASSED",
            latency_ms=connection.latency_ms,
            metrics=metrics,
            message=connection.message,
        )

    if not settings.vertex_configured:
        connection.health_state = "UNCONFIGURED"
        connection.last_checked_at = datetime.now(UTC)
        connection.error_code = "NOT_CONFIGURED"
        connection.message = "服务端环境凭据尚未配置完整"
        connection.latency_ms = round((perf_counter() - started) * 1000)
        db.commit()
        _sync_legacy_health(db, settings, connection)
        return _failed_probe(
            db,
            connection,
            model_id=None,
            probe_type="CREDENTIALS",
            error_code="NOT_CONFIGURED",
            message=connection.message,
            latency_ms=connection.latency_ms,
        )

    # Import through the compatibility module so existing installations and
    # tests that replace the legacy credential-manager seam keep working.
    from app.services.vertex_health import get_vertex_credential_manager

    manager = get_vertex_credential_manager()
    try:
        # OAuth refresh/client creation is the environment-account credential
        # smoke.  It never sends a prompt or invokes a text/image model.
        manager.execute(settings, lambda _client: True)
        now = datetime.now(UTC)
        connection.health_state = "HEALTHY"
        connection.last_checked_at = now
        connection.last_success_at = now
        connection.latency_ms = round((perf_counter() - started) * 1000)
        connection.error_code = None
        connection.message = "服务端环境凭据验证成功"
        db.commit()
        _sync_legacy_health(db, settings, connection)
        return create_probe(
            db,
            connection_id=connection.id,
            model_id=None,
            probe_type="CREDENTIALS",
            status="PASSED",
            latency_ms=connection.latency_ms,
            metrics={"remote_verified": True},
            message=connection.message,
        )
    except Exception as error:
        failure = classify_vertex_failure(error)
        connection.health_state = (
            "DEGRADED"
            if connection.last_success_at is not None
            or failure.code in {"PERMISSION", "RATE_LIMIT", "TIMEOUT", "UPSTREAM"}
            else "OFFLINE"
        )
        connection.last_checked_at = datetime.now(UTC)
        connection.latency_ms = round((perf_counter() - started) * 1000)
        connection.error_code = failure.code
        connection.message = failure.message
        db.commit()
        _sync_legacy_health(db, settings, connection)
        return _failed_probe(
            db,
            connection,
            model_id=None,
            probe_type="CREDENTIALS",
            error_code=failure.code,
            message=failure.message,
            latency_ms=connection.latency_ms,
        )


def _image_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def _smoke_operation(model: AIModel, requested: str | None) -> str:
    operations = set(model.operations or [])
    operation = requested
    if operation is None:
        preferences = (
            ("structured_text", "multimodal_analysis")
            if model.model_type == "TEXT"
            else ("image_generate", "image_edit")
        )
        operation = next((item for item in preferences if item in operations), None)
    if operation is None or operation not in operations:
        raise HTTPException(status_code=422, detail="所选模型不支持请求的冒烟操作")
    return operation


def _verify_model_smoke(
    db: Session,
    settings: Settings,
    connection: ProviderConnection,
    model: AIModel,
    payload: ConnectionVerifyRequest,
) -> ModelProbe:
    if model.model_type == "IMAGE" and not payload.acknowledge_cost:
        raise HTTPException(
            status_code=422,
            detail="图片模型冒烟测试可能计费，必须明确确认",
        )

    operation = _smoke_operation(model, payload.operation)
    tested_operations: set[str] = set()
    latencies: list[int] = []
    binding = None
    current_operation = operation
    try:
        for _ in range(payload.runs):
            binding = bind_adapter(
                db,
                settings,
                operation=current_operation,
                explicit_reference=model.id,
            )
            started = perf_counter()
            if current_operation == "structured_text":
                binding.adapter.generate_structured(
                    StructuredRequest(
                        prompt='只返回 {"ok": true}',
                        temperature=0,
                        metadata={"max_output_tokens": 64},
                    ),
                    _SmokeResult,
                )
            elif current_operation == "multimodal_analysis":
                image = _image_bytes()
                binding.adapter.analyze_multimodal(
                    MultimodalRequest(
                        prompt='检查图片并只返回 {"ok": true}',
                        images=(image,),
                        mime_types=("image/png",),
                        temperature=0,
                    ),
                    _SmokeResult,
                )
            elif current_operation == "image_generate":
                binding.adapter.generate_asset(
                    ImageRequest(
                        prompt="一个简单黑色圆点，白色背景，无文字",
                        resolution="1K",
                        aspect_ratio="1:1",
                    )
                )
            else:
                image = _image_bytes()
                binding.adapter.edit_region(
                    ImageRequest(
                        prompt="保持白色背景，在中心添加一个黑色圆点，无文字",
                        resolution="1K",
                        aspect_ratio="1:1",
                        reference_images=(image,),
                        reference_mime_types=("image/png",),
                    )
                )
            tested_operations.add(current_operation)
            latencies.append(round((perf_counter() - started) * 1000))
            if binding and binding.selected_key:
                mark_key_success(db, binding.selected_key.row)

        now = datetime.now(UTC)
        model.last_verified_at = now
        connection.last_success_at = now
        connection.last_checked_at = now
        model.median_latency_ms = round(median(latencies))
        model.success_rate = (
            1.0
            if model.success_rate is None
            else round(model.success_rate * 0.8 + 0.2, 4)
        )
        capabilities = dict(model.capabilities or {})
        verified_operations = set(capabilities.get("verified_operations") or [])
        verified_operations.update(tested_operations)
        capabilities["verified_operations"] = sorted(verified_operations)
        model.capabilities = capabilities
        model.confidence = (
            "VERIFIED"
            if set(model.operations or []).issubset(verified_operations)
            else "PARTIAL"
        )
        connection.health_state = "HEALTHY"
        connection.latency_ms = model.median_latency_ms
        connection.error_code = None
        connection.message = "模型能力测试通过"
        db.commit()
        _sync_legacy_health(db, settings, connection)
        return create_probe(
            db,
            connection_id=connection.id,
            model_id=model.id,
            probe_type="MODEL_SMOKE",
            status="PASSED",
            latency_ms=model.median_latency_ms,
            metrics={
                "min_ms": min(latencies),
                "median_ms": median(latencies),
                "max_ms": max(latencies),
                "verified_operations": sorted(tested_operations),
            },
            message="模型测试通过",
        )
    except ProviderAdapterError as error:
        if binding and binding.selected_key:
            mark_key_failure(
                db,
                binding.selected_key.row,
                error.code,
                retry_after_seconds=error.retry_after_seconds,
            )
        model.success_rate = (
            0.0 if model.success_rate is None else round(model.success_rate * 0.8, 4)
        )
        connection.health_state = "DEGRADED"
        connection.last_checked_at = datetime.now(UTC)
        connection.error_code = error.code
        connection.message = error.user_message
        db.commit()
        _sync_legacy_health(db, settings, connection)
        return _failed_probe(
            db,
            connection,
            model_id=model.id,
            probe_type="MODEL_SMOKE",
            error_code=error.code,
            message=error.user_message,
            latency_ms=None,
        )


def verify_connection(
    db: Session,
    settings: Settings,
    connection_id: str,
    payload: ConnectionVerifyRequest,
) -> tuple[dict, ModelProbe]:
    connection = db.get(ProviderConnection, connection_id)
    if connection is None:
        raise HTTPException(status_code=404, detail="供应商连接不存在")

    model = None
    if payload.level == "MODEL_SMOKE":
        model = db.get(AIModel, payload.catalog_model_id)
        if model is None or model.connection_id != connection.id:
            raise HTTPException(status_code=404, detail="测试模型不存在")
        if model.model_type == "IMAGE" and not payload.acknowledge_cost:
            raise HTTPException(
                status_code=422,
                detail="图片模型冒烟测试可能计费，必须明确确认",
            )

    previous_state = (
        connection.health_state,
        connection.error_code,
        connection.message,
    )
    connection.health_state = "CHECKING"
    connection.last_checked_at = datetime.now(UTC)
    connection.error_code = None
    connection.message = "正在执行连接验证"
    db.commit()

    try:
        if payload.level == "CREDENTIALS":
            probe = _verify_credentials(db, settings, connection)
        else:
            assert model is not None
            probe = _verify_model_smoke(db, settings, connection, model, payload)
    except HTTPException:
        connection.health_state, connection.error_code, connection.message = (
            previous_state
        )
        db.commit()
        raise
    except Exception:
        connection.health_state = "DEGRADED"
        connection.last_checked_at = datetime.now(UTC)
        connection.error_code = "UPSTREAM"
        connection.message = "连接验证异常中止"
        db.commit()
        raise

    db.refresh(connection)
    return connection_health_view(db, settings, connection), probe
