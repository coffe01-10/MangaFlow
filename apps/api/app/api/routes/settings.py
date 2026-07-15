from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter

from fastapi import APIRouter, Body, Depends
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import ProviderHealth
from app.services.runtime_settings import read_runtime_settings, update_runtime_settings
from app.services.vertex_health import get_or_create_health, health_read, verify_vertex
from app.settings_schemas import (
    DiagnosticCheckRead,
    DiagnosticsRead,
    RuntimeSettingsRead,
    RuntimeSettingsUpdate,
    VertexHealthRead,
    VertexVerifyRequest,
)

router = APIRouter(prefix="/settings")


@router.get("/runtime", response_model=RuntimeSettingsRead)
def runtime_settings(db: Session = Depends(get_db)) -> RuntimeSettingsRead:
    return read_runtime_settings(db, get_settings())


@router.patch("/runtime", response_model=RuntimeSettingsRead)
def patch_runtime_settings(
    payload: RuntimeSettingsUpdate, db: Session = Depends(get_db)
) -> RuntimeSettingsRead:
    return update_runtime_settings(db, get_settings(), payload)


@router.get("/vertex/status", response_model=VertexHealthRead)
def vertex_status(db: Session = Depends(get_db)) -> VertexHealthRead:
    settings = get_settings()
    return health_read(get_or_create_health(db, settings), settings)


@router.post("/vertex/verify", response_model=VertexHealthRead)
def vertex_verify(
    payload: VertexVerifyRequest | None = Body(default=None),
    db: Session = Depends(get_db),
) -> VertexHealthRead:
    return verify_vertex(db, get_settings(), payload or VertexVerifyRequest())


def _check(check_id: str, label: str, operation) -> DiagnosticCheckRead:
    started = perf_counter()
    try:
        status, message = operation()
    except Exception:
        status, message = "FAILED", f"{label}检测失败"
    return DiagnosticCheckRead(
        id=check_id,
        label=label,
        status=status,
        message=message,
        latency_ms=round((perf_counter() - started) * 1000),
    )


@router.get("/diagnostics", response_model=DiagnosticsRead)
def diagnostics(db: Session = Depends(get_db)) -> DiagnosticsRead:
    settings = get_settings()
    health = db.scalar(select(ProviderHealth).where(ProviderHealth.provider == "vertex-ai"))

    def database_check():
        db.execute(text("SELECT 1"))
        return "OK", "数据库连接正常"

    def queue_check():
        if not settings.queue_enabled:
            return "OK", "当前使用本地同步执行"
        from redis import Redis

        connection = Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=0.4,
            socket_timeout=0.4,
        )
        try:
            connection.ping()
            return "OK", "Redis 队列连接正常"
        except Exception:
            return "WARNING", "Redis 暂不可用，开发环境任务会保留并等待重试"
        finally:
            connection.close()

    def oauth_check():
        if not settings.google_cloud_project or not settings.google_application_credentials:
            return "FAILED", "Vertex 服务账号尚未配置"
        if not settings.google_application_credentials.is_file():
            return "FAILED", "服务账号文件不存在"
        if health and health.health_state == "HEALTHY":
            return "OK", "最近一次 OAuth 验证成功"
        if health and health.health_state == "DEGRADED":
            return "WARNING", health.message
        return "NOT_CHECKED", "凭据文件存在，尚未执行显式联网验证"

    def text_check():
        access = health.text_model_access if health else "NOT_CHECKED"
        if access == "GRANTED":
            return "OK", "Gemini 3.5 Flash 最近一次验证成功"
        if access in {"DENIED", "UNAVAILABLE"}:
            return "WARNING", "文本模型当前不可用，请查看 Vertex 诊断"
        return "NOT_CHECKED", "尚未执行低 token 文本模型验证"

    checks = [
        DiagnosticCheckRead(
            id="api", label="MangaFlow API", status="OK", message="API 正常响应", latency_ms=0
        ),
        _check("database", "数据库", database_check),
        _check("queue", "Worker 与队列", queue_check),
        _check("oauth", "Google OAuth", oauth_check),
        _check("text-model", "文本模型", text_check),
    ]
    return DiagnosticsRead(checks=checks, checked_at=datetime.now(UTC))
