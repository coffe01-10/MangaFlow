from __future__ import annotations

import random
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TypeVar

from app.config import Settings
from app.services.provider_errors import ProviderFailure

T = TypeVar("T")

# Compatibility re-export of the shared provider-neutral failure type. Fields
# and runtime behavior are identical to the previous Vertex-local dataclass;
# keeping the historical name avoids breaking importers while the code set
# lives in one place (docs/v02-provider-neutrality-audit.md C5).
VertexFailure = ProviderFailure


def _is_transport_or_sdk_error(error: Exception) -> bool:
    """Distinguish transient transport/SDK failures from local defects.

    The catch-all below used to mark every unknown exception retryable, so an
    adapter bug or off-contract response shape was re-billed up to
    ``max_attempts`` times inside one audit row. Unknown failures keep
    transient semantics only when they demonstrably come from the transport
    stack or the google SDK namespace; everything else is treated as terminal
    (issue #209).
    """

    if isinstance(error, (ConnectionError, TimeoutError, OSError)):
        return True
    root = (type(error).__module__ or "").split(".", 1)[0]
    return root in {
        "google",
        "grpc",
        "httpx",
        "httpcore",
        "requests",
        "urllib3",
        "aiohttp",
    }


def _raw_mentions(raw: str, code: str) -> bool:
    """Word-boundary numeric match on the error text.

    A bare substring check let "received: 4019" or "port 40443" trip the 401/
    404 branches; the worst case was a quota message containing such a number
    being classified AUTHENTICATION, which permanently disables the key.
    """

    return re.search(rf"(?<![0-9]){code}(?![0-9])", raw) is not None


def classify_vertex_failure(error: Exception) -> VertexFailure:
    """Map provider and transport failures to safe, user-facing categories."""
    raw = str(error).lower()
    status = getattr(error, "status_code", None) or getattr(error, "code", None)
    if callable(status):
        try:
            status = status()
        except TypeError:
            status = None
    status_text = str(status).lower()

    if status == "INVALID_OUTPUT":
        return VertexFailure("INVALID_OUTPUT", "模型已响应，但返回格式无法验证", False)
    if status in {"INVALID_INPUT", "UNSUPPORTED_CAPABILITY"}:
        return VertexFailure("CONFIGURATION", "模型调用参数与当前能力不匹配", False)

    if any(
        token in raw
        for token in (
            "model armor",
            "safety policy",
            "prohibited content",
            "blocked by safety",
            "responsible ai",
        )
    ):
        return VertexFailure(
            "CONTENT_POLICY",
            "请求被 Vertex 内容安全策略拦截，系统已缩小生成片段；请重试",
            False,
        )
    # Gemini Developer API rejects a bad key with HTTP 400 and "API key not
    # valid" — no 401, none of the tokens below. Without this branch it fell
    # through to the transport fallback as retryable UPSTREAM, so every job
    # burned its full retry budget on a permanently bad key and the key was
    # never disabled (replacement/disable paths key off AUTHENTICATION).
    if "api key not valid" in raw or "api_key_invalid" in raw:
        return VertexFailure(
            "AUTHENTICATION", "API 密钥无效，请更换有效的密钥", False, authentication=True
        )
    if status in (401,) or _raw_mentions(raw, "401") or "unauth" in raw or "invalid_grant" in raw:
        return VertexFailure(
            "AUTHENTICATION", "Vertex AI 凭据无效或令牌已过期", True, authentication=True
        )
    if status in (403,) or _raw_mentions(raw, "403") or "permission" in raw or "forbidden" in raw:
        return VertexFailure("PERMISSION", "服务账号没有调用该 Vertex 模型的权限", False)
    if status in (404,) or _raw_mentions(raw, "404") or "not found" in raw or "not_found" in raw:
        return VertexFailure("MODEL_NOT_FOUND", "配置的 Vertex 模型或区域不可用", False)
    if (
        status in (429,)
        or _raw_mentions(raw, "429")
        or "rate limit" in raw
        or "resource_exhausted" in raw
    ):
        return VertexFailure("RATE_LIMIT", "Vertex AI 请求过于频繁，请稍后重试", True)
    if isinstance(error, TimeoutError) or "timeout" in raw or "deadline" in raw:
        return VertexFailure("TIMEOUT", "Vertex AI 请求超时", True)
    if (
        isinstance(status, int)
        and 500 <= status < 600
        or status_text.startswith("5")
        or any(token in raw for token in ("connection", "dns", "temporarily unavailable"))
    ):
        return VertexFailure("UPSTREAM", "Vertex AI 网络或上游服务暂时不可用", True)
    if any(token in raw for token in ("credential", "service account", "private key")):
        return VertexFailure("CONFIGURATION", "Vertex AI 服务账号配置无效", False)
    if _is_transport_or_sdk_error(error):
        return VertexFailure("UPSTREAM", "Vertex AI 暂时无法完成请求", True)
    return VertexFailure(
        "UPSTREAM", "Vertex AI 请求出现未分类错误，已停止重试", False
    )


@dataclass
class _CredentialEntry:
    credentials: Any
    lock: threading.RLock


class VertexCredentialManager:
    """Process-local credential cache with serialized refresh and bounded retries."""

    def __init__(
        self,
        *,
        refresh_margin: timedelta = timedelta(minutes=5),
        max_attempts: int = 3,
        base_backoff_seconds: float = 0.2,
    ) -> None:
        self.refresh_margin = refresh_margin
        self.max_attempts = max_attempts
        self.base_backoff_seconds = base_backoff_seconds
        self._entries: dict[tuple[str, int, str, str], _CredentialEntry] = {}
        self._entries_lock = threading.RLock()
        # Number of provider dispatches the most recent ``execute`` call made
        # (issue #209): the manager retries the whole ``operation`` inside ONE
        # audit row, so the count is the only visibility into hidden paid
        # dispatches. Reset at the start of every ``execute`` and incremented
        # before each operation invocation; read after ``execute`` returns or
        # raises.
        self.last_dispatch_count = 0

    @staticmethod
    def _config_key(settings: Settings) -> tuple[str, int, str, str]:
        path = settings.google_application_credentials
        if not path:
            raise RuntimeError("Vertex AI 服务账号尚未配置")
        resolved = Path(path).resolve()
        if not resolved.is_file():
            raise RuntimeError("Vertex AI 凭据文件不存在")
        return (
            str(resolved),
            resolved.stat().st_mtime_ns,
            settings.google_cloud_project or "",
            settings.google_cloud_location,
        )

    def _new_credentials(self, path: str) -> Any:
        from google.oauth2 import service_account

        return service_account.Credentials.from_service_account_file(
            path,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )

    def _entry(self, settings: Settings) -> tuple[tuple[str, int, str, str], _CredentialEntry]:
        key = self._config_key(settings)
        with self._entries_lock:
            entry = self._entries.get(key)
            if entry is None:
                entry = _CredentialEntry(self._new_credentials(key[0]), threading.RLock())
                # A modified credentials file invalidates older entries for that path.
                self._entries = {
                    existing_key: existing
                    for existing_key, existing in self._entries.items()
                    if existing_key[0] != key[0]
                }
                self._entries[key] = entry
            return key, entry

    @staticmethod
    def _aware(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    def _needs_refresh(self, credentials: Any) -> bool:
        expiry = self._aware(getattr(credentials, "expiry", None))
        token = getattr(credentials, "token", None)
        return not token or not expiry or expiry <= datetime.now(UTC) + self.refresh_margin

    @staticmethod
    def _refresh(credentials: Any) -> None:
        from google.auth.transport.requests import Request

        request = Request()
        try:
            credentials.refresh(request)
        finally:
            session = getattr(request, "session", None)
            close = getattr(session, "close", None)
            if callable(close):
                close()

    def get_credentials(self, settings: Settings, *, force_refresh: bool = False) -> Any:
        _, entry = self._entry(settings)
        with entry.lock:
            if force_refresh or self._needs_refresh(entry.credentials):
                self._refresh(entry.credentials)
            return entry.credentials

    def token_expiry(self, settings: Settings) -> datetime | None:
        try:
            _, entry = self._entry(settings)
        except Exception:
            return None
        return self._aware(getattr(entry.credentials, "expiry", None))

    def invalidate(self, settings: Settings) -> None:
        try:
            key = self._config_key(settings)
        except Exception:
            return
        with self._entries_lock:
            self._entries.pop(key, None)

    def create_client(self, settings: Settings) -> Any:
        from google import genai
        from google.genai import types

        credentials = self.get_credentials(settings)
        # Timeout bound (90s) plus pinned retry attempts: the SDK default retry
        # policy could silently multiply paid dispatches inside the manager's
        # own bounded retry loop and one audit row (issue #209).
        return genai.Client(
            vertexai=True,
            project=settings.google_cloud_project,
            location=settings.google_cloud_location,
            credentials=credentials,
            http_options=types.HttpOptions(
                timeout=90_000,
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )

    def execute(
        self,
        settings: Settings,
        operation: Callable[[Any], T],
        *,
        client_factory: Callable[[], Any] | None = None,
    ) -> T:
        auth_retried = False
        factory = client_factory or (lambda: self.create_client(settings))
        self.last_dispatch_count = 0
        for attempt in range(self.max_attempts):
            client = None
            try:
                client = factory()
                self.last_dispatch_count += 1
                return operation(client)
            except Exception as error:
                failure = classify_vertex_failure(error)
                if failure.authentication and not auth_retried:
                    auth_retried = True
                    self.invalidate(settings)
                    continue
                if not failure.retryable or attempt >= self.max_attempts - 1:
                    raise
                delay = self.base_backoff_seconds * (2**attempt)
                time.sleep(delay + random.uniform(0, delay / 3 if delay else 0))
            finally:
                close = getattr(client, "close", None)
                if callable(close):
                    close()
        raise RuntimeError("Vertex AI retry loop exhausted")


_manager = VertexCredentialManager()


def get_vertex_credential_manager() -> VertexCredentialManager:
    return _manager
