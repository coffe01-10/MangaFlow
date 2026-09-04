from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import PurePosixPath, PureWindowsPath
from time import perf_counter
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.http_bounds import read_bounded_http_body
from app.model_adapters.base import ProviderAdapterError
from app.model_adapters.compatible import provider_http_client
from app.models import (
    AIModel,
    ModelProbe,
    ProviderConnection,
    ProviderKey,
    ProviderProfile,
    RoutingPolicy,
)
from app.provider_schemas import (
    ConnectionCreate,
    ConnectionUpdate,
    ModelVisibilityBatchUpdate,
    ProviderCreate,
    ProviderKeyWrite,
    ProviderModelCreate,
    ProviderModelUpdate,
    ProviderUpdate,
    RoutingPolicyWrite,
)
from app.services.credential_crypto import (
    encrypt_secret,
    mark_key_failure,
    mark_key_success,
    secret_hint,
    select_provider_key,
)
from app.services.credential_source import (
    CLI_SESSION,
    CONNECTION_KEY,
    ENV_SERVICE_ACCOUNT,
    connection_credential_source,
    connection_protocol_capabilities,
    default_cli_executable_for_protocol,
    environment_credentials_ready,
)
from app.services.provider_presets import (
    ANTHROPIC_ENDPOINTS,
    OPENAI_ENDPOINTS,
    ensure_provider_presets,
    proxy_url_for_connection,
)

_BLOCKED_HEADERS = {"authorization", "x-api-key", "host", "content-length"}


def _enabled_key_is_usable(key: ProviderKey, now: datetime) -> bool:
    cooldown_until = key.cooldown_until
    if cooldown_until is not None and cooldown_until.tzinfo is None:
        cooldown_until = cooldown_until.replace(tzinfo=UTC)
    return bool(
        key.enabled and (cooldown_until is None or cooldown_until <= now)
    )


def connection_is_configured(
    settings: Settings,
    connection: ProviderConnection,
    keys: list[ProviderKey],
) -> bool:
    """Return the protocol-neutral credential readiness for a connection."""

    source = connection_credential_source(connection)
    if source == ENV_SERVICE_ACCOUNT:
        return environment_credentials_ready(settings, connection.protocol)
    if source == CLI_SESSION:
        return connection.health_state == "AVAILABLE"
    now = datetime.now(UTC)
    return any(_enabled_key_is_usable(key, now) for key in keys)


def _validate_base_url_syntax(value: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(status_code=422, detail="供应商 Base URL 必须是 HTTP(S) 地址")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise HTTPException(status_code=422, detail="供应商 Base URL 不能包含凭据或查询参数")
    if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise HTTPException(status_code=422, detail="供应商 Base URL 必须使用 HTTPS")
    return normalized


def _validate_endpoint_templates(templates: dict[str, str]) -> dict[str, str]:
    for path in templates.values():
        parsed = urlparse(str(path))
        if (
            not str(path).startswith("/")
            or parsed.scheme
            or parsed.netloc
            or parsed.query
            or parsed.fragment
            or ".." in parsed.path.split("/")
        ):
            raise HTTPException(status_code=422, detail="端点模板必须是无查询参数的站内绝对路径")
    return {str(key): str(value) for key, value in templates.items()}


def _validate_headers(headers: dict[str, str]) -> dict[str, str]:
    for key in headers:
        if key.lower() in _BLOCKED_HEADERS:
            raise HTTPException(status_code=422, detail=f"不能覆盖受保护请求头 {key}")
        if "\r" in key or "\n" in key or "\r" in str(headers[key]) or "\n" in str(headers[key]):
            raise HTTPException(status_code=422, detail="请求头不能包含换行符")
    return {str(key): str(value) for key, value in headers.items()}


def _validate_balance_config(config: dict[str, Any]) -> dict[str, Any]:
    value = dict(config)
    path = value.get("path")
    if path:
        parsed = urlparse(str(path))
        if (
            not str(path).startswith("/")
            or str(path).startswith("//")
            or parsed.scheme
            or parsed.netloc
            or parsed.fragment
            or ".." in parsed.path.split("/")
        ):
            raise HTTPException(
                status_code=422,
                detail="余额端点必须是同一供应商下的站内绝对路径",
            )
    return value


def _default_endpoints(protocol: str) -> dict[str, str]:
    return dict(ANTHROPIC_ENDPOINTS if protocol == "ANTHROPIC" else OPENAI_ENDPOINTS)


def list_provider_views(db: Session, settings: Settings) -> list[dict]:
    ensure_provider_presets(db, settings)
    profiles = list(db.scalars(select(ProviderProfile).order_by(ProviderProfile.name)))
    connections = list(
        db.scalars(select(ProviderConnection).order_by(ProviderConnection.created_at))
    )
    keys = list(db.scalars(select(ProviderKey).order_by(ProviderKey.created_at)))
    model_counts = dict(
        db.execute(
            select(AIModel.connection_id, func.count(AIModel.id)).group_by(AIModel.connection_id)
        ).all()
    )
    connection_map: dict[str, list[ProviderConnection]] = {}
    key_map: dict[str, list[ProviderKey]] = {}
    for connection in connections:
        connection_map.setdefault(connection.provider_id, []).append(connection)
    for key in keys:
        key_map.setdefault(key.connection_id, []).append(key)
    result = []
    for profile in profiles:
        items = []
        for connection in connection_map.get(profile.id, []):
            connection_keys = key_map.get(connection.id, [])
            credential_source = connection_credential_source(connection)
            protocol_capabilities = connection_protocol_capabilities(connection)
            items.append(
                {
                    "id": connection.id,
                    "provider_id": profile.id,
                    "name": connection.name,
                    "protocol": connection.protocol,
                    "base_url": connection.base_url,
                    "enabled": connection.enabled,
                    "configured": connection_is_configured(
                        settings, connection, connection_keys
                    ),
                    "credential_source": credential_source,
                    "credential_writable": (
                        credential_source == CONNECTION_KEY
                        and settings.provider_credentials_writable
                    ),
                    "supports_model_discovery": (
                        protocol_capabilities.supports_model_discovery
                    ),
                    "supports_balance": bool(
                        (connection.balance_config or {}).get("enabled")
                        and (connection.balance_config or {}).get("path")
                    ),
                    "supported_model_types": list(
                        protocol_capabilities.supported_model_types
                    ),
                    "use_responses_api": connection.use_responses_api,
                    "endpoint_templates": connection.endpoint_templates or {},
                    "extra_headers": connection.extra_headers or {},
                    "balance_config": connection.balance_config or {},
                    "nonsecret_config": connection.nonsecret_config or {},
                    "health_state": connection.health_state,
                    "last_checked_at": connection.last_checked_at,
                    "last_success_at": connection.last_success_at,
                    "latency_ms": connection.latency_ms,
                    "error_code": connection.error_code,
                    "message": connection.message,
                    "key_count": len(connection_keys),
                    "model_count": int(model_counts.get(connection.id, 0)),
                    "keys": connection_keys,
                    "version": connection.version,
                }
            )
        result.append(
            {
                "id": profile.id,
                "preset_key": profile.preset_key,
                "name": profile.name,
                "category": profile.category,
                "description": profile.description,
                "built_in": profile.built_in,
                "enabled": profile.enabled,
                "risk_label": profile.risk_label,
                "documentation_url": profile.documentation_url,
                "connections": items,
                "version": profile.version,
            }
        )
    return result


def create_custom_provider(
    db: Session, payload: ProviderCreate
) -> ProviderProfile:
    profile = ProviderProfile(
        name=payload.name,
        category="CUSTOM",
        description="用户自定义供应商",
        built_in=False,
        enabled=True,
        risk_label="CUSTOM",
    )
    db.add(profile)
    db.flush()
    db.add(
        ProviderConnection(
            provider_id=profile.id,
            name="默认连接",
            protocol=payload.protocol,
            base_url=_validate_base_url_syntax(payload.base_url),
            enabled=False,
            use_responses_api=payload.use_responses_api,
            endpoint_templates=_default_endpoints(payload.protocol),
            extra_headers={},
            balance_config={},
            nonsecret_config={"overridden_fields": ["base_url"]},
            health_state="UNCONFIGURED",
            message="等待录入 API Key",
        )
    )
    db.commit()
    db.refresh(profile)
    return profile


def update_provider(
    db: Session, provider_id: str, payload: ProviderUpdate
) -> ProviderProfile:
    profile = db.get(ProviderProfile, provider_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="供应商不存在")
    if profile.version != payload.version:
        raise HTTPException(status_code=409, detail="供应商设置已更新，请刷新后重试")
    changes = payload.model_dump(exclude_unset=True, exclude={"version"})
    changes = {key: value for key, value in changes.items() if value is not None}
    for key, value in changes.items():
        setattr(profile, key, value)
    profile.version += 1
    db.commit()
    db.refresh(profile)
    return profile


def add_connection(
    db: Session, provider_id: str, payload: ConnectionCreate
) -> ProviderConnection:
    if db.get(ProviderProfile, provider_id) is None:
        raise HTTPException(status_code=404, detail="供应商不存在")
    connection = ProviderConnection(
        provider_id=provider_id,
        name=payload.name,
        protocol=payload.protocol,
        base_url=_validate_base_url_syntax(payload.base_url),
        enabled=False,
        use_responses_api=payload.use_responses_api,
        endpoint_templates=_default_endpoints(payload.protocol),
        extra_headers={},
        balance_config={},
        nonsecret_config={"overridden_fields": ["base_url", "protocol"]},
        health_state="UNCONFIGURED",
        message="等待录入 API Key",
    )
    db.add(connection)
    db.commit()
    db.refresh(connection)
    return connection



def is_absolute_executable_path(value: str) -> bool:
    """Platform-independent absolute-path check for configured CLI paths.

    The product host is Windows, but this validation runs wherever the API
    process lives (Linux development, containers): ``Path().is_absolute()``
    alone accepts ``/usr/bin/agy`` and rejects ``C:\\tools\\agy.exe`` on
    POSIX, and the reverse on Windows. Both syntaxes are absolute; the CLI
    probe still fails closed when the file does not exist on the actual
    host, so accepting the wider syntax here adds no execution surface.
    """

    return PureWindowsPath(value).is_absolute() or PurePosixPath(value).is_absolute()


def update_connection(
    db: Session, connection_id: str, payload: ConnectionUpdate
) -> ProviderConnection:
    connection = db.get(ProviderConnection, connection_id)
    if connection is None:
        raise HTTPException(status_code=404, detail="供应商连接不存在")
    if connection.version != payload.version:
        raise HTTPException(status_code=409, detail="连接设置已更新，请刷新后重试")
    changes = payload.model_dump(exclude_unset=True, exclude={"version"})
    default_cli_executable = default_cli_executable_for_protocol(connection.protocol)
    previous_cli_executable = str(
        (connection.nonsecret_config or {}).get("cli_executable")
        or default_cli_executable
    )
    if changes.get("base_url"):
        changes["base_url"] = _validate_base_url_syntax(changes["base_url"])
    if changes.get("extra_headers") is not None:
        changes["extra_headers"] = _validate_headers(changes["extra_headers"])
    if changes.get("endpoint_templates") is not None:
        changes["endpoint_templates"] = _validate_endpoint_templates(
            changes["endpoint_templates"]
        )
    if changes.get("balance_config") is not None:
        changes["balance_config"] = _validate_balance_config(
            changes["balance_config"]
        )
    if (
        changes.get("nonsecret_config") is not None
        and connection_credential_source(connection) == CLI_SESSION
    ):
        config = dict(changes["nonsecret_config"])
        executable = config.get("cli_executable", default_cli_executable)
        if (
            not isinstance(executable, str)
            or not executable.strip()
            or len(executable) > 500
            or "\0" in executable
        ):
            raise HTTPException(status_code=422, detail="CLI 可执行文件配置无效")
        executable = executable.strip()
        if (
            executable.casefold() != default_cli_executable
            and not is_absolute_executable_path(executable)
        ):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"CLI 可执行文件必须是 {default_cli_executable} 或绝对路径"
                ),
            )
        config["cli_executable"] = executable
        changes["nonsecret_config"] = config
    cli_executable_changed = (
        connection_credential_source(connection) == CLI_SESSION
        and changes.get("nonsecret_config") is not None
        and str(
            changes["nonsecret_config"].get("cli_executable")
            or default_cli_executable
        )
        != previous_cli_executable
    )
    if "enabled" in changes:
        source_config = changes.get("nonsecret_config", connection.nonsecret_config)
        nonsecret_config = dict(source_config or {})
        nonsecret_config.pop("auto_enable_pending", None)
        if "nonsecret_config" in changes:
            changes["nonsecret_config"] = nonsecret_config
        else:
            connection.nonsecret_config = nonsecret_config
    for key, value in changes.items():
        setattr(connection, key, value)
    if cli_executable_changed:
        connection.health_state = "UNKNOWN"
        connection.error_code = None
        connection.message = "CLI 路径已更改，等待重新探测"
    connection.version += 1
    db.commit()
    db.refresh(connection)
    return connection


def write_provider_key(
    db: Session,
    settings: Settings,
    connection_id: str,
    payload: ProviderKeyWrite,
) -> ProviderKey:
    connection = db.get(ProviderConnection, connection_id)
    if connection is None:
        raise HTTPException(status_code=404, detail="供应商连接不存在")
    key = db.scalar(
        select(ProviderKey).where(
            ProviderKey.connection_id == connection_id,
            ProviderKey.label == payload.label,
        )
    )
    encrypted = encrypt_secret(settings, payload.api_key)
    if key is None:
        key = ProviderKey(
            connection_id=connection_id,
            label=payload.label,
            encrypted_secret=encrypted,
            key_hint=secret_hint(payload.api_key),
            health_state="UNKNOWN",
        )
        db.add(key)
    else:
        key.encrypted_secret = encrypted
        key.key_hint = secret_hint(payload.api_key)
        key.enabled = True
        key.health_state = "UNKNOWN"
        key.cooldown_until = None
        key.last_error_code = None
        key.version += 1
    connection.message = "凭据已保存，等待连接测试"
    if not connection.enabled:
        connection.enabled = True
    db.commit()
    db.refresh(key)
    return key


def delete_provider_key(db: Session, connection_id: str, key_id: str) -> None:
    key = db.get(ProviderKey, key_id)
    if key is None or key.connection_id != connection_id:
        raise HTTPException(status_code=404, detail="API Key 不存在")
    db.delete(key)
    db.commit()


def create_model(
    db: Session, connection_id: str, payload: ProviderModelCreate
) -> AIModel:
    connection = db.get(ProviderConnection, connection_id)
    if connection is None:
        raise HTTPException(status_code=404, detail="供应商连接不存在")
    _validate_protocol_capabilities(
        connection,
        payload.model_type,
        payload.input_modalities,
        payload.output_modalities,
        payload.operations,
    )
    duplicate = db.scalar(
        select(AIModel).where(
            AIModel.connection_id == connection_id,
            AIModel.provider_model_id == payload.provider_model_id,
        )
    )
    if duplicate:
        raise HTTPException(status_code=409, detail="该连接已存在同名模型")
    values = payload.model_dump()
    display_name = values.pop("display_name") or payload.provider_model_id
    model = AIModel(
        connection_id=connection_id,
        display_name=display_name,
        source="MANUAL",
        confidence="MANUAL",
        **values,
    )
    db.add(model)
    db.commit()
    db.refresh(model)
    return model


def list_models_for_connection(db: Session, connection_id: str) -> list[AIModel]:
    connection = db.get(ProviderConnection, connection_id)
    if connection is None:
        raise HTTPException(status_code=404, detail="供应商连接不存在")
    return list(
        db.scalars(
            select(AIModel)
            .where(AIModel.connection_id == connection_id)
            .order_by(AIModel.model_type, AIModel.priority.desc(), AIModel.display_name)
        )
    )


def update_model(db: Session, model_id: str, payload: ProviderModelUpdate) -> AIModel:
    model = db.get(AIModel, model_id)
    if model is None:
        raise HTTPException(status_code=404, detail="模型不存在")
    if model.version != payload.version:
        raise HTTPException(status_code=409, detail="模型设置已更新，请刷新后重试")
    changes = payload.model_dump(exclude_unset=True, exclude={"version"})
    display_requested = "display_enabled" in changes
    display_enabled = changes.pop("display_enabled", None)
    if display_requested and display_enabled is None:
        raise HTTPException(status_code=422, detail="模型展示偏好不能为 null")
    if display_requested and not changes:
        result = db.execute(
            update(AIModel)
            .where(AIModel.id == model.id, AIModel.version == payload.version)
            .values(
                display_enabled=bool(display_enabled),
                version=AIModel.version + 1,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            db.rollback()
            raise HTTPException(status_code=409, detail="模型设置已更新，请刷新后重试")
        db.commit()
        db.expire(model)
        db.refresh(model)
        return model
    connection = db.get(ProviderConnection, model.connection_id)
    if connection is None:
        raise HTTPException(status_code=409, detail="模型所属连接已不存在")
    _validate_protocol_capabilities(
        connection,
        str(changes.get("model_type") or model.model_type),
        list(changes.get("input_modalities") or model.input_modalities or []),
        list(changes.get("output_modalities") or model.output_modalities or []),
        list(changes.get("operations") or model.operations or []),
    )
    for key, value in changes.items():
        setattr(model, key, value)
    if display_requested:
        model.display_enabled = bool(display_enabled)
    model.source = "MANUAL"
    model.confidence = "MANUAL"
    model.version += 1
    db.commit()
    db.refresh(model)
    return model


def set_model_visibility_bulk(
    db: Session, payload: ModelVisibilityBatchUpdate
) -> dict[str, list[dict[str, object]]]:
    """Persist independent model display preferences with partial success."""

    updated: list[dict[str, object]] = []
    failed: list[dict[str, object]] = []
    for item in payload.items:
        model = db.get(AIModel, item.model_id)
        if model is None:
            failed.append(
                {
                    "model_id": item.model_id,
                    "error_code": "MODEL_NOT_FOUND",
                    "message": "模型不存在或已被删除",
                }
            )
            continue
        connection = db.get(ProviderConnection, model.connection_id)
        if connection is None:
            failed.append(
                {
                    "model_id": item.model_id,
                    "error_code": "CONNECTION_MISSING",
                    "message": "模型所属连接已不存在",
                }
            )
            continue
        if model.display_enabled == payload.display_enabled:
            updated.append({"model_id": model.id, "version": model.version})
            continue
        if model.version != item.expected_version:
            failed.append(
                {
                    "model_id": item.model_id,
                    "error_code": "VERSION_CONFLICT",
                    "message": "模型设置已更新，请刷新后重试",
                    "current_version": model.version,
                }
            )
            continue
        result = db.execute(
            update(AIModel)
            .where(AIModel.id == model.id, AIModel.version == item.expected_version)
            .values(
                display_enabled=payload.display_enabled,
                version=AIModel.version + 1,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            db.rollback()
            db.expire(model)
            db.refresh(model)
            if model.display_enabled == payload.display_enabled:
                updated.append({"model_id": model.id, "version": model.version})
            else:
                failed.append(
                    {
                        "model_id": item.model_id,
                        "error_code": "VERSION_CONFLICT",
                        "message": "模型设置已更新，请刷新后重试",
                        "current_version": model.version,
                    }
                )
            continue
        db.commit()
        db.expire(model)
        db.refresh(model)
        updated.append({"model_id": model.id, "version": model.version})

    # The final item may have been a read-only failure or idempotent success.
    # Close that transaction without changing independently committed successes.
    if db.in_transaction():
        db.rollback()
    return {"updated": updated, "failed": failed}


def _validate_protocol_capabilities(
    connection: ProviderConnection,
    model_type: str,
    input_modalities: list[str],
    output_modalities: list[str],
    operations: list[str],
) -> None:
    if connection.protocol == "ANTHROPIC" and (
        model_type == "IMAGE" or any(operation.startswith("image_") for operation in operations)
    ):
        raise HTTPException(
            status_code=422,
            detail="Anthropic 协议连接当前只支持文字与视觉理解模型",
        )
    allowed_operations = {
        "TEXT": {"structured_text", "multimodal_analysis"},
        "IMAGE": {"image_generate", "image_edit"},
    }
    unknown = set(operations) - allowed_operations[model_type]
    if not operations or unknown:
        raise HTTPException(status_code=422, detail="模型操作与模型类型不匹配")
    if model_type == "TEXT" and "TEXT" not in output_modalities:
        raise HTTPException(status_code=422, detail="文字模型必须声明 TEXT 输出")
    if model_type == "IMAGE" and "IMAGE" not in output_modalities:
        raise HTTPException(status_code=422, detail="图片模型必须声明 IMAGE 输出")
    if "TEXT" not in input_modalities:
        raise HTTPException(status_code=422, detail="当前支持的模型操作必须接受 TEXT 输入")
    if (
        "multimodal_analysis" in operations or "image_edit" in operations
    ) and "IMAGE" not in input_modalities:
        raise HTTPException(status_code=422, detail="视觉分析或图片编辑必须接受 IMAGE 输入")


def _request_headers(connection: ProviderConnection, api_key: str) -> dict[str, str]:
    headers = _validate_headers(dict(connection.extra_headers or {}))
    if connection.protocol == "ANTHROPIC":
        headers["x-api-key"] = api_key
        headers.setdefault("anthropic-version", "2023-06-01")
    else:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _connection_http_client(
    db: Session,
    settings: Settings,
    connection: ProviderConnection,
    url: str,
    timeout: httpx.Timeout,
) -> httpx.Client:
    profile = db.get(ProviderProfile, connection.provider_id)
    parsed = urlparse(url)
    allow_loopback = settings.environment.lower() == "development" and parsed.hostname in {
        "localhost",
        "127.0.0.1",
        "::1",
    }
    return provider_http_client(
        url,
        timeout=timeout,
        allow_private=settings.allow_private_provider_networks,
        allow_http_loopback=allow_loopback,
        proxy_url=(
            proxy_url_for_connection(profile, connection, settings) if profile else None
        ),
    )


def _collect_google_model_entries(models, settings: Settings) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    approx_bytes = 2
    for item in models:
        ident = str(getattr(item, "name", "") or "").removeprefix("models/")
        label = str(getattr(item, "display_name", "") or "")
        if not ident:
            continue
        if len(ident) > 256 or len(label) > 256:
            raise ValueError("供应商模型列表字段超过允许的大小")
        entry = {"id": ident, "display_name": label}
        encoded = json.dumps(entry, ensure_ascii=False)
        approx_bytes += len(encoded.encode("utf-8")) + 1
        if approx_bytes > settings.max_provider_metadata_bytes:
            raise ValueError("供应商模型列表超过允许的大小")
        if len(entries) >= settings.max_discovered_models:
            raise ValueError("供应商模型列表超过允许的条目数")
        entries.append(entry)
    return entries


def _fetch_model_entries(
    db: Session,
    settings: Settings,
    connection: ProviderConnection,
    secret: str,
    *,
    client: httpx.Client | None = None,
) -> tuple[list[dict], int]:
    """Read and validate a model listing without mutating the catalog."""

    started = perf_counter()
    owned_client = False
    http = client
    try:
        if connection.protocol == "GOOGLE_NATIVE":
            from google import genai

            google_client = genai.Client(api_key=secret)
            try:
                entries = _collect_google_model_entries(
                    google_client.models.list(), settings
                )
            finally:
                close = getattr(google_client, "close", None)
                if callable(close):
                    close()
        else:
            path = (connection.endpoint_templates or {}).get("models", "/models")
            target_url = urljoin(
                f"{connection.base_url.rstrip('/')}/", path.lstrip("/")
            )
            if http is None:
                http = _connection_http_client(
                    db,
                    settings,
                    connection,
                    target_url,
                    httpx.Timeout(30.0, connect=10.0),
                )
                owned_client = True
            request = http.build_request(
                "GET",
                target_url,
                headers=_request_headers(connection, secret),
            )
            response = http.send(request, stream=True, follow_redirects=False)
            try:
                if response.status_code >= 400:
                    raise _http_error(response.status_code)
                if 300 <= response.status_code < 400:
                    raise ProviderAdapterError(
                        "UPSTREAM", "模型列表端点返回了未允许的重定向"
                    )
                raw = read_bounded_http_body(
                    response, settings.max_provider_metadata_bytes
                )
            finally:
                response.close()
            try:
                body = json.loads(raw)
            except json.JSONDecodeError as error:
                raise ValueError("供应商模型列表响应格式无效") from error
            if isinstance(body, list):
                entries = body
            elif isinstance(body, dict):
                entries = body.get("data") or []
            else:
                raise ValueError("供应商模型列表响应格式无效")
            if not isinstance(entries, list):
                raise ValueError("供应商模型列表 data 字段必须是数组")
            if len(entries) > settings.max_discovered_models:
                raise ValueError("供应商模型列表超过允许的条目数")
            for entry in entries:
                if not isinstance(entry, dict):
                    raise ValueError("供应商模型列表条目必须是对象")
                ident = str(entry.get("id") or entry.get("name") or "")
                label = str(entry.get("display_name") or "")
                if len(ident) > 256 or len(label) > 256:
                    raise ValueError("供应商模型列表字段超过允许的大小")
        return entries, round((perf_counter() - started) * 1000)
    finally:
        if owned_client and http is not None:
            http.close()


def _record_connection_failure(
    db: Session,
    connection: ProviderConnection,
    selected,
    error: Exception,
    started: float,
) -> None:
    code = str(getattr(error, "code", "UPSTREAM"))
    connection.health_state = "DEGRADED"
    connection.last_checked_at = datetime.now(UTC)
    connection.latency_ms = round((perf_counter() - started) * 1000)
    connection.error_code = code
    connection.message = _safe_error_message(code)
    mark_key_failure(db, selected.row, code)


def probe_connection_credentials(
    db: Session,
    settings: Settings,
    connection: ProviderConnection,
    *,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Verify a key-backed connection without generating model output.

    Discovery-capable protocols validate the key against their model-list
    endpoint but deliberately do not write the returned entries. Protocols
    without a safe discovery endpoint only prove that the stored credential is
    readable; their connection remains degraded until a model smoke test.
    """

    selected = select_provider_key(db, settings, connection.id)
    capabilities = connection_protocol_capabilities(connection)
    started = perf_counter()
    if not capabilities.supports_model_discovery:
        connection.health_state = "DEGRADED"
        connection.last_checked_at = datetime.now(UTC)
        connection.latency_ms = round((perf_counter() - started) * 1000)
        connection.error_code = None
        connection.message = "凭据可读取；该协议需通过模型冒烟验证远端权限"
        db.commit()
        return {"remote_verified": False, "discovered_models": None}
    try:
        entries, latency_ms = _fetch_model_entries(
            db, settings, connection, selected.secret, client=client
        )
        connection.health_state = "HEALTHY"
        connection.last_checked_at = datetime.now(UTC)
        connection.last_success_at = connection.last_checked_at
        connection.latency_ms = latency_ms
        connection.error_code = None
        connection.message = "凭据与模型目录连接验证成功"
        mark_key_success(db, selected.row)
        return {"remote_verified": True, "discovered_models": len(entries)}
    except (ProviderAdapterError, httpx.HTTPError, ValueError) as error:
        _record_connection_failure(db, connection, selected, error, started)
        if isinstance(error, ProviderAdapterError):
            raise HTTPException(status_code=502, detail=error.user_message) from error
        raise HTTPException(status_code=502, detail="无法验证供应商凭据") from error


def discover_models(
    db: Session,
    settings: Settings,
    connection_id: str,
    *,
    client: httpx.Client | None = None,
) -> list[AIModel]:
    connection = db.get(ProviderConnection, connection_id)
    if connection is None:
        raise HTTPException(status_code=404, detail="供应商连接不存在")
    if not connection_protocol_capabilities(connection).supports_model_discovery:
        raise HTTPException(
            status_code=422,
            detail="该连接不支持模型发现，请使用预设目录或手动添加模型",
        )
    selected = select_provider_key(db, settings, connection.id)
    started = perf_counter()
    try:
        entries, latency_ms = _fetch_model_entries(
            db, settings, connection, selected.secret, client=client
        )
        models = _upsert_discovered_models(db, connection, entries)
        connection.health_state = "HEALTHY"
        connection.last_checked_at = datetime.now(UTC)
        connection.last_success_at = connection.last_checked_at
        connection.latency_ms = latency_ms
        connection.error_code = None
        connection.message = f"已发现 {len(models)} 个模型"
        mark_key_success(db, selected.row)
        return models
    except (ProviderAdapterError, httpx.HTTPError, ValueError) as error:
        _record_connection_failure(db, connection, selected, error, started)
        if isinstance(error, ProviderAdapterError):
            raise HTTPException(status_code=502, detail=error.user_message) from error
        raise HTTPException(status_code=502, detail="无法读取供应商模型列表") from error


def _upsert_discovered_models(
    db: Session, connection: ProviderConnection, entries: list[dict]
) -> list[AIModel]:
    existing = {
        row.provider_model_id: row
        for row in db.scalars(select(AIModel).where(AIModel.connection_id == connection.id))
    }
    result: list[AIModel] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        provider_model_id = str(entry.get("id") or entry.get("name") or "").removeprefix(
            "models/"
        )
        if not provider_model_id:
            continue
        metadata = _infer_model(entry, provider_model_id, connection)
        model = existing.get(provider_model_id)
        if model is None:
            model = AIModel(
                connection_id=connection.id,
                provider_model_id=provider_model_id,
                display_name=str(
                    entry.get("display_name") or entry.get("name") or provider_model_id
                ),
                source="DISCOVERED",
                **metadata,
            )
            db.add(model)
        elif model.source != "MANUAL":
            current_capabilities = dict(model.capabilities or {})
            verified_operations = current_capabilities.pop("verified_operations", None)
            preserve_verification = model.confidence in {"VERIFIED", "PARTIAL"} and all(
                getattr(model, key) == metadata[key]
                for key in (
                    "model_type",
                    "input_modalities",
                    "output_modalities",
                    "operations",
                    "api_surfaces",
                )
            ) and current_capabilities == metadata["capabilities"]
            if preserve_verification:
                metadata["confidence"] = model.confidence
                if verified_operations is not None:
                    metadata["capabilities"] = {
                        **metadata["capabilities"],
                        "verified_operations": verified_operations,
                    }
            elif model.confidence in {"VERIFIED", "PARTIAL"}:
                model.last_verified_at = None
            model.display_name = str(
                entry.get("display_name") or entry.get("name") or provider_model_id
            )
            for key, value in metadata.items():
                setattr(model, key, value)
        result.append(model)
    db.flush()
    return result


def _infer_model(
    entry: dict, provider_model_id: str, connection: ProviderConnection
) -> dict[str, Any]:
    lowered = provider_model_id.lower()
    architecture = entry.get("architecture") or {}
    inputs = [str(item).upper() for item in architecture.get("input_modalities") or []]
    outputs = [str(item).upper() for item in architecture.get("output_modalities") or []]
    declared = bool(inputs or outputs or entry.get("capabilities"))
    image_output_name = any(
        token in lowered
        for token in ("image", "flux", "cogview", "seedream", "dall-e", "ideogram")
    )
    if not outputs:
        outputs = ["IMAGE"] if image_output_name else ["TEXT"]
    if not inputs:
        vision_name = any(
            token in lowered
            for token in ("vision", "-vl", "4o", "gemini", "claude", "image")
        )
        inputs = ["TEXT", "IMAGE"] if vision_name else ["TEXT"]
    model_type = "IMAGE" if "IMAGE" in outputs else "TEXT"
    if model_type == "IMAGE":
        if connection.protocol == "ANTHROPIC":
            operations = []
            surfaces = []
        else:
            operations = ["image_generate"]
            if any(token in lowered for token in ("edit", "image", "flux")):
                operations.append("image_edit")
            surfaces = ["IMAGES"] if connection.protocol == "OPENAI" else []
    else:
        operations = ["structured_text"]
        if "IMAGE" in inputs:
            operations.append("multimodal_analysis")
        surfaces = ["RESPONSES" if connection.use_responses_api else "CHAT"]
    capabilities = {
        "structured_output_mode": "JSON_MODE",
        "supported_parameters": entry.get("supported_parameters") or [],
        "context_length": entry.get("context_length"),
    }
    if model_type == "IMAGE":
        capabilities.update(
            {"resolutions": ["1K"], "max_reference_images": 1, "size_map": {"1K": "1024x1536"}}
        )
    return {
        "model_type": model_type,
        "input_modalities": inputs,
        "output_modalities": outputs,
        "operations": operations,
        "api_surfaces": surfaces,
        "capabilities": capabilities,
        "pricing": entry.get("pricing") or {},
        "confidence": "DECLARED" if declared else "INFERRED",
        "enabled": not (connection.protocol == "ANTHROPIC" and model_type == "IMAGE"),
        "priority": 50,
    }


def _http_error(status: int) -> ProviderAdapterError:
    if status == 401:
        return ProviderAdapterError("AUTHENTICATION", "供应商 API Key 无效")
    if status == 403:
        return ProviderAdapterError("PERMISSION", "供应商拒绝访问")
    if status == 429:
        return ProviderAdapterError("RATE_LIMIT", "供应商请求已达限制", retryable=True)
    if status >= 500:
        return ProviderAdapterError("UPSTREAM", "供应商服务暂时不可用", retryable=True)
    return ProviderAdapterError("INVALID_INPUT", "供应商拒绝了当前请求")


def _safe_error_message(code: str) -> str:
    return {
        "AUTHENTICATION": "API Key 无效",
        "PERMISSION": "供应商拒绝访问",
        "RATE_LIMIT": "供应商请求已达限制",
        "MODEL_NOT_FOUND": "模型或端点不存在",
    }.get(code, "供应商暂时无法连接")


def _json_path(value: Any, path: str) -> Any:
    current = value
    for segment in path.split("."):
        if isinstance(current, list) and segment.isdigit():
            current = current[int(segment)]
        elif isinstance(current, dict):
            current = current.get(segment)
        else:
            return None
    return current


def read_balance(
    db: Session,
    settings: Settings,
    connection_id: str,
    *,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    connection = db.get(ProviderConnection, connection_id)
    if connection is None:
        raise HTTPException(status_code=404, detail="供应商连接不存在")
    config = _validate_balance_config(connection.balance_config or {})
    if not config.get("enabled") or not config.get("path"):
        return {"configured": False, "value": None, "message": "该供应商未配置余额接口"}
    selected = select_provider_key(db, settings, connection.id)
    target_url = urljoin(
        f"{connection.base_url.rstrip('/')}/", str(config["path"]).lstrip("/")
    )
    owned_client = False
    http = client
    try:
        if http is None:
            http = _connection_http_client(
                db,
                settings,
                connection,
                target_url,
                httpx.Timeout(15.0, connect=5.0),
            )
            owned_client = True
        request = http.build_request(
            "GET",
            target_url,
            headers=_request_headers(connection, selected.secret),
        )
        response = http.send(request, stream=True, follow_redirects=False)
        try:
            if response.status_code >= 400:
                raise _http_error(response.status_code)
            if 300 <= response.status_code < 400:
                raise ProviderAdapterError("UPSTREAM", "余额端点返回了未允许的重定向")
            raw = read_bounded_http_body(
                response, settings.max_provider_metadata_bytes
            )
        finally:
            response.close()
        try:
            body = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ProviderAdapterError("UPSTREAM", "余额查询响应格式无效") from error
        mark_key_success(db, selected.row)
        return {
            "configured": True,
            "value": _json_path(body, str(config.get("result_path") or "")),
            "usage": _json_path(body, str(config.get("usage_path") or ""))
            if config.get("usage_path")
            else None,
            "currency": config.get("currency"),
            "message": "余额查询成功",
        }
    except ProviderAdapterError as error:
        mark_key_failure(
            db,
            selected.row,
            error.code,
            retry_after_seconds=error.retry_after_seconds,
            # Balance failures are diagnostic: they must not disable a
            # generation key that may be perfectly valid for paid calls.
            degrade_only=True,
        )
        raise HTTPException(status_code=502, detail=error.user_message) from error
    except Exception as error:
        if isinstance(error, HTTPException):
            raise
        raise HTTPException(status_code=502, detail="余额查询失败") from error
    finally:
        if owned_client and http is not None:
            http.close()


def create_probe(
    db: Session,
    *,
    connection_id: str,
    model_id: str | None,
    probe_type: str,
    status: str,
    latency_ms: int | None,
    metrics: dict | None = None,
    error_code: str | None = None,
    message: str = "",
) -> ModelProbe:
    probe = ModelProbe(
        connection_id=connection_id,
        model_id=model_id,
        probe_type=probe_type,
        status=status,
        latency_ms=latency_ms,
        metrics=metrics or {},
        error_code=error_code,
        message=message,
    )
    db.add(probe)
    db.commit()
    db.refresh(probe)
    return probe


def upsert_routing_policy(
    db: Session, payload: RoutingPolicyWrite
) -> RoutingPolicy:
    policy = db.scalar(
        select(RoutingPolicy).where(
            RoutingPolicy.project_id == payload.project_id,
            RoutingPolicy.task_kind == payload.task_kind,
        )
    )
    if policy is None:
        policy = RoutingPolicy(**payload.model_dump(exclude={"version"}))
        db.add(policy)
    else:
        if payload.version is not None and policy.version != payload.version:
            raise HTTPException(status_code=409, detail="路由策略已更新，请刷新后重试")
        for key, value in payload.model_dump(exclude={"version"}).items():
            setattr(policy, key, value)
        policy.version += 1
    db.commit()
    db.refresh(policy)
    return policy
