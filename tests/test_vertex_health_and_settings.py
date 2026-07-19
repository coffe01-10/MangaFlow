from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Lock
from types import SimpleNamespace

import pytest

from app.config import Settings, get_settings
from app.models import AppSetting, ProviderHealth
from app.services.runtime_settings import (
    apply_runtime_overrides,
    update_runtime_settings,
)
from app.services.vertex_credentials import (
    VertexCredentialManager,
    classify_vertex_failure,
)
from app.services.vertex_health import verify_vertex
from app.settings_schemas import RuntimeSettingsUpdate, VertexVerifyRequest


class ProviderError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class AdapterError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@pytest.mark.parametrize(
    ("error", "code", "retryable"),
    [
        (ProviderError(401, "token expired"), "AUTHENTICATION", True),
        (ProviderError(403, "private detail must stay hidden"), "PERMISSION", False),
        (ProviderError(403, "Model Armor blocked by safety"), "CONTENT_POLICY", False),
        (ProviderError(404, "model missing"), "MODEL_NOT_FOUND", False),
        (ProviderError(429, "quota"), "RATE_LIMIT", True),
        (ProviderError(503, "upstream"), "UPSTREAM", True),
        (TimeoutError("slow"), "TIMEOUT", True),
        (AdapterError("INVALID_OUTPUT"), "INVALID_OUTPUT", False),
    ],
)
def test_vertex_failure_classification_is_safe(error, code, retryable):
    failure = classify_vertex_failure(error)
    assert failure.code == code
    assert failure.retryable is retryable
    assert "private detail" not in failure.message


def test_vertex_execute_retries_transient_errors_and_closes_every_client():
    manager = VertexCredentialManager(max_attempts=3, base_backoff_seconds=0)
    settings = Settings(google_cloud_project="test-project")
    clients: list[SimpleNamespace] = []
    attempts = 0

    def factory():
        client = SimpleNamespace(closed=False)
        client.close = lambda: setattr(client, "closed", True)
        clients.append(client)
        return client

    def operation(_client):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ProviderError(429, "rate limited")
        return "ok"

    assert manager.execute(settings, operation, client_factory=factory) == "ok"
    assert attempts == 3
    assert all(client.closed for client in clients)


def test_vertex_execute_does_not_retry_permission_errors():
    manager = VertexCredentialManager(max_attempts=3, base_backoff_seconds=0)
    attempts = 0

    def operation(_client):
        nonlocal attempts
        attempts += 1
        raise ProviderError(403, "denied")

    with pytest.raises(ProviderError):
        manager.execute(
            Settings(google_cloud_project="test-project"),
            operation,
            client_factory=lambda: SimpleNamespace(close=lambda: None),
        )
    assert attempts == 1


def test_credential_refresh_is_serialized(tmp_path, monkeypatch):
    credential_file = tmp_path / "service-account.json"
    credential_file.write_text("{}", encoding="utf-8")
    settings = Settings(
        google_cloud_project="test-project",
        google_application_credentials=credential_file,
    )
    credentials = SimpleNamespace(token=None, expiry=None)
    manager = VertexCredentialManager()
    monkeypatch.setattr(manager, "_new_credentials", lambda _path: credentials)
    refresh_count = 0
    refresh_lock = Lock()

    def refresh(value):
        nonlocal refresh_count
        with refresh_lock:
            refresh_count += 1
        value.token = "redacted-token"
        value.expiry = datetime.now(UTC) + timedelta(hours=1)

    monkeypatch.setattr(manager, "_refresh", refresh)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: manager.get_credentials(settings), range(8)))

    assert refresh_count == 1
    assert all(item is credentials for item in results)


def test_runtime_overrides_survive_rehydrate_and_reject_sensitive_fields(
    client, db_session
):
    settings = Settings(queue_enabled=True, job_timeout_seconds=900, max_auto_repairs=3)
    update_runtime_settings(
        db_session,
        settings,
        RuntimeSettingsUpdate(
            queue_mode="LOCAL",
            job_timeout_seconds=120,
            max_auto_repairs=2,
            version=1,
        ),
    )
    restarted = Settings(
        queue_enabled=True, job_timeout_seconds=900, max_auto_repairs=3
    )
    apply_runtime_overrides(db_session, restarted)
    # LOCAL is an executor mode, not a switch that disables execution.
    assert restarted.queue_enabled is True
    assert restarted.job_timeout_seconds == 120
    assert restarted.max_auto_repairs == 2
    assert set(db_session.get(AppSetting, "runtime").value) <= {
        "queue_mode",
        "job_timeout_seconds",
        "max_auto_repairs",
    }

    response = client.patch(
        "/api/v1/settings/runtime",
        json={"version": 2, "google_application_credentials": "must-not-enter-api"},
    )
    assert response.status_code == 422
    assert (
        "google_application_credentials"
        not in client.get("/api/v1/settings/runtime").text
    )


def test_diagnostics_reports_local_executor_without_probing_redis(client, db_session):
    db_session.add(
        AppSetting(
            key="runtime",
            value={"queue_mode": "LOCAL"},
            version=1,
        )
    )
    db_session.commit()

    response = client.get("/api/v1/settings/diagnostics")

    assert response.status_code == 200
    payload = response.json()
    assert payload["queue"] == {
        "current_mode": "LOCAL",
        "actual_executor": "LOCAL",
        "redis_state": "NOT_USED",
        "can_execute_new_jobs": True,
    }
    queue_check = next(item for item in payload["checks"] if item["id"] == "queue")
    assert queue_check["status"] == "OK"
    assert "本地后台执行器" in queue_check["message"]


def test_provider_health_persists_permission_failure_without_losing_configuration(
    db_session, tmp_path, monkeypatch
):
    credential_file = tmp_path / "service-account.json"
    credential_file.write_text("{}", encoding="utf-8")
    settings = Settings(
        google_cloud_project="test-project",
        google_application_credentials=credential_file,
    )
    manager = SimpleNamespace(
        execute=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ProviderError(403, "sensitive provider response")
        ),
        token_expiry=lambda _settings: None,
    )
    monkeypatch.setattr(
        "app.services.vertex_health.get_vertex_credential_manager", lambda: manager
    )

    result = verify_vertex(
        db_session, settings, VertexVerifyRequest(level="CREDENTIALS")
    )

    stored = db_session.query(ProviderHealth).filter_by(provider="vertex-ai").one()
    assert result.configured is True
    assert result.credential_file_present is True
    assert result.health_state == "DEGRADED"
    assert stored.error_code == "PERMISSION"
    assert stored.consecutive_failures == 1
    assert "sensitive provider response" not in stored.message


def test_vertex_status_get_never_returns_credential_path(client, tmp_path, monkeypatch):
    credential_file = tmp_path / "service-account.json"
    credential_file.write_text("{}", encoding="utf-8")
    settings = get_settings()
    monkeypatch.setattr(settings, "google_cloud_project", "test-project")
    monkeypatch.setattr(settings, "google_application_credentials", credential_file)

    response = client.get("/api/v1/settings/vertex/status")

    assert response.status_code == 200
    assert str(credential_file) not in response.text
    assert "private_key" not in response.text


def test_successful_credential_refresh_clears_only_transient_model_outages(
    db_session, tmp_path, monkeypatch
):
    credential_file = tmp_path / "service-account.json"
    credential_file.write_text("{}", encoding="utf-8")
    settings = Settings(
        google_cloud_project="test-project",
        google_application_credentials=credential_file,
    )
    health = ProviderHealth(
        provider="vertex-ai",
        configured=True,
        credential_file_present=True,
        health_state="DEGRADED",
        text_model_access="UNAVAILABLE",
        image_model_access={
            "image.nano_banana_2": "UNAVAILABLE",
            "image.nano_banana_pro": "DENIED",
        },
    )
    db_session.add(health)
    db_session.commit()
    manager = SimpleNamespace(
        execute=lambda *_args, **_kwargs: True,
        token_expiry=lambda _settings: datetime.now(UTC) + timedelta(hours=1),
    )
    monkeypatch.setattr(
        "app.services.vertex_health.get_vertex_credential_manager", lambda: manager
    )

    result = verify_vertex(
        db_session, settings, VertexVerifyRequest(level="CREDENTIALS")
    )

    assert result.health_state == "HEALTHY"
    assert result.text_model_access == "NOT_CHECKED"
    assert result.image_model_access["image.nano_banana_2"] == "NOT_CHECKED"
    assert result.image_model_access["image.nano_banana_pro"] == "DENIED"
