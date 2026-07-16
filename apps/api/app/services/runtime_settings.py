from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import AppSetting
from app.settings_schemas import RuntimeSettingsRead, RuntimeSettingsUpdate

RUNTIME_KEY = "runtime"
RUNTIME_DEFAULTS: dict[str, Any] = {
    "queue_mode": "AUTO",
    "default_concurrency": 4,
    "health_check_interval_seconds": 600,
    "ui_poll_interval_seconds": 3000,
    "workflow_autosave_ms": 800,
}


@dataclass(frozen=True)
class QueueExecutionState:
    """The queue mode requested by the user and the executor available now."""

    queue_mode: str
    actual_executor: str
    redis_state: str
    can_execute: bool


def _safe_overrides(db: Session) -> tuple[dict[str, Any], int]:
    row = db.get(AppSetting, RUNTIME_KEY)
    if not row:
        return {}, 1
    allowed = set(RuntimeSettingsUpdate.model_fields) - {"version"}
    return {key: value for key, value in row.value.items() if key in allowed}, row.version


def read_runtime_settings(db: Session, settings: Settings) -> RuntimeSettingsRead:
    overrides, version = _safe_overrides(db)
    values = {
        **RUNTIME_DEFAULTS,
        "job_timeout_seconds": settings.job_timeout_seconds,
        "max_auto_repairs": settings.max_auto_repairs,
        **overrides,
    }
    return RuntimeSettingsRead(
        **values,
        version=version,
        database_backend=settings.database_url.split(":", 1)[0],
        storage_root=str(settings.storage_root),
        upload_root=str(settings.upload_root),
        redis_configured=bool(settings.redis_url),
    )


def read_queue_mode(db: Session) -> str:
    overrides, _ = _safe_overrides(db)
    return str(overrides.get("queue_mode") or RUNTIME_DEFAULTS["queue_mode"])


def queue_execution_state(
    db: Session,
    settings: Settings,
    *,
    probe_redis: bool = True,
) -> QueueExecutionState:
    """Resolve AUTO/LOCAL/REDIS without making LOCAL depend on Redis.

    Redis probing is deliberately short-lived. Callers that only need the configured
    mode can disable it, while diagnostics and readiness use the live result.
    """

    queue_mode = read_queue_mode(db)
    if not settings.queue_enabled:
        return QueueExecutionState(
            queue_mode=queue_mode,
            actual_executor="NONE",
            redis_state="NOT_USED",
            can_execute=False,
        )
    if queue_mode == "LOCAL":
        return QueueExecutionState(
            queue_mode=queue_mode,
            actual_executor="LOCAL",
            redis_state="NOT_USED",
            can_execute=True,
        )
    if not probe_redis:
        return QueueExecutionState(
            queue_mode=queue_mode,
            actual_executor="PENDING",
            redis_state="UNKNOWN",
            can_execute=queue_mode == "AUTO" and settings.environment == "development",
        )

    connection = None
    try:
        from redis import Redis

        connection = Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=0.4,
            socket_timeout=0.4,
        )
        connection.ping()
        return QueueExecutionState(
            queue_mode=queue_mode,
            actual_executor="REDIS",
            redis_state="AVAILABLE",
            can_execute=True,
        )
    except Exception:
        use_local = queue_mode == "AUTO" and settings.environment == "development"
        return QueueExecutionState(
            queue_mode=queue_mode,
            actual_executor="LOCAL" if use_local else "NONE",
            redis_state="UNAVAILABLE",
            can_execute=use_local,
        )
    finally:
        if connection is not None:
            connection.close()


def apply_runtime_overrides(db: Session, settings: Settings) -> None:
    """Rehydrate the safe dynamic subset in API and worker processes."""
    overrides, _ = _safe_overrides(db)
    if not overrides:
        return
    if "job_timeout_seconds" in overrides:
        settings.job_timeout_seconds = overrides["job_timeout_seconds"]
    if "max_auto_repairs" in overrides:
        settings.max_auto_repairs = overrides["max_auto_repairs"]
    # queue_mode is resolved per enqueue operation. In particular, LOCAL means
    # "run with the local executor", not "disable the queue". Keep queue_enabled
    # unchanged as a legacy environment value so rehydration cannot silently turn
    # a valid local worker into a save-only mode.


def update_runtime_settings(
    db: Session, settings: Settings, payload: RuntimeSettingsUpdate
) -> RuntimeSettingsRead:
    row = db.get(AppSetting, RUNTIME_KEY)
    current_version = row.version if row else 1
    if payload.version != current_version:
        raise HTTPException(status_code=409, detail="运行设置已更新，请刷新后重试")

    current, _ = _safe_overrides(db)
    changes = payload.model_dump(exclude_unset=True, exclude={"version"})
    merged = {**current, **changes}
    if row:
        row.value = merged
        row.version += 1
    else:
        row = AppSetting(key=RUNTIME_KEY, value=merged, version=2)
        db.add(row)
    db.commit()

    # Existing workers read Settings per job, so safe mutable overrides take effect
    # without a restart. Paths, database URLs, Redis URLs and credentials are never dynamic.
    apply_runtime_overrides(db, settings)
    return read_runtime_settings(db, settings)
