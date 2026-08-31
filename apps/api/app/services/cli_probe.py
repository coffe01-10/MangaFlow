"""Provider-neutral persistence for optional CLI channel probes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy.orm import Session

from app.models import ModelProbe, ProviderConnection


@dataclass(frozen=True)
class CLIProbeObservation:
    status: str
    metrics: dict = field(default_factory=dict)
    error_code: str | None = None
    message: str = ""
    latency_ms: int | None = None


class CLIProbeAdapter(Protocol):
    def presence(self) -> CLIProbeObservation: ...

    def version(self) -> CLIProbeObservation: ...

    def login(self) -> CLIProbeObservation: ...

    def capability(self) -> CLIProbeObservation: ...


def probe_cli_connection(
    db: Session,
    connection_id: str,
    adapter: CLIProbeAdapter,
    *,
    auto_commit: bool = False,
) -> ProviderConnection:
    """Run read-only probe steps, persist every step, and derive readiness."""

    connection = db.get(ProviderConnection, connection_id)
    if connection is None or not connection.protocol.startswith("CLI_"):
        raise ValueError("CLI connection not found")
    connection.health_state = "PROBING"
    observations: list[tuple[str, CLIProbeObservation]] = []
    blocked = False
    for probe_type, callback in (
        ("CLI_PRESENCE", adapter.presence),
        ("CLI_VERSION", adapter.version),
        ("CLI_LOGIN", adapter.login),
        ("CLI_CAPABILITY", adapter.capability),
    ):
        observation = (
            CLIProbeObservation(status="UNKNOWN", message="前置探测未通过")
            if blocked
            else callback()
        )
        if observation.status not in {"PASSED", "FAILED", "UNKNOWN"}:
            raise ValueError("invalid CLI probe status")
        observations.append((probe_type, observation))
        blocked = blocked or observation.status == "FAILED"
        db.add(
            ModelProbe(
                connection_id=connection.id,
                probe_type=probe_type,
                status=observation.status,
                latency_ms=observation.latency_ms,
                metrics=observation.metrics,
                error_code=observation.error_code,
                message=observation.message,
            )
        )

    failed = next(
        ((kind, item) for kind, item in observations if item.status == "FAILED"),
        None,
    )
    unknown = next(
        ((kind, item) for kind, item in observations if item.status == "UNKNOWN"),
        None,
    )
    if failed is None and unknown is None:
        connection.health_state = "AVAILABLE"
        connection.error_code = None
        connection.message = "CLI 通道就绪"
        version = observations[1][1].metrics.get("version")
        if isinstance(version, str):
            connection.nonsecret_config = {
                **(connection.nonsecret_config or {}),
                "cli_version": version[:120],
            }
        connection.last_success_at = datetime.now(UTC)
    else:
        terminal_observation = failed if failed is not None else unknown
        assert terminal_observation is not None
        kind, item = terminal_observation
        connection.health_state = {
            "CLI_PRESENCE": "UNAVAILABLE",
            "CLI_LOGIN": "UNAUTHENTICATED",
            "CLI_CAPABILITY": "UNSUPPORTED",
        }.get(kind, "UNKNOWN") if failed is not None else "UNKNOWN"
        connection.error_code = item.error_code or (
            "CLI_PROBE_INCONCLUSIVE" if failed is None else None
        )
        connection.message = item.message or "CLI 探测结果不完整"
    connection.last_checked_at = datetime.now(UTC)
    if auto_commit:
        db.commit()
        db.refresh(connection)
    return connection
