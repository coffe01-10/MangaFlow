from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter

from fastapi import APIRouter, Body, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.services.provider_catalog import list_provider_views
from app.services.runtime_settings import (
    queue_execution_state,
    read_runtime_settings,
    update_runtime_settings,
)
from app.services.vertex_health import get_or_create_health, health_read, verify_vertex
from app.settings_schemas import (
    DiagnosticCheckRead,
    DiagnosticsRead,
    QueueDiagnosticRead,
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
    queue_state = queue_execution_state(db, settings)

    def database_check():
        db.execute(text("SELECT 1"))
        return "OK", "数据库连接正常"

    def queue_check():
        if queue_state.actual_executor == "LOCAL":
            if queue_state.queue_mode == "LOCAL":
                return "OK", "LOCAL 模式；本地后台执行器可以执行新任务"
            return "WARNING", "AUTO 模式；Redis 暂不可用，已切换本地后台执行器"
        if queue_state.actual_executor == "REDIS":
            return "OK", f"{queue_state.queue_mode} 模式；Redis 队列可以执行新任务"
        if queue_state.redis_state == "NOT_USED":
            return "WARNING", "后台执行器被环境级维护开关停用，新任务将保留等待"
        return "WARNING", f"{queue_state.queue_mode} 模式；Redis 暂不可用，新任务将保留等待"

    checks = [
        DiagnosticCheckRead(
            id="api", label="MangaFlow API", status="OK", message="API 正常响应", latency_ms=0
        ),
        _check("database", "数据库", database_check),
        _check("queue", "Worker 与队列", queue_check),
    ]
    for profile in list_provider_views(db, settings):
        for connection in profile["connections"]:
            if not (
                connection["enabled"]
                or connection["configured"]
                or connection["health_state"] != "UNCONFIGURED"
            ):
                continue
            state = connection["health_state"]
            if not connection["configured"]:
                status = "FAILED"
                message = "凭据尚未配置"
            elif state == "HEALTHY":
                status = "OK"
                message = connection["message"] or "最近一次连接验证成功"
            elif state in {"DEGRADED", "OFFLINE"}:
                status = "WARNING"
                message = connection["message"] or "连接需要重新验证"
            else:
                status = "NOT_CHECKED"
                message = connection["message"] or "尚未执行连接验证"
            checks.append(
                DiagnosticCheckRead(
                    id=f"provider-{connection['id']}",
                    label=f"{profile['name']} · {connection['name']}",
                    status=status,
                    message=message,
                    latency_ms=connection["latency_ms"],
                )
            )
    return DiagnosticsRead(
        checks=checks,
        checked_at=datetime.now(UTC),
        queue=QueueDiagnosticRead(
            current_mode=queue_state.queue_mode,
            actual_executor=queue_state.actual_executor,
            redis_state=queue_state.redis_state,
            can_execute_new_jobs=queue_state.can_execute,
        ),
    )
