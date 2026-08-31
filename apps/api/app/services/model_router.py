from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import SessionLocal
from app.model_adapters.antigravity_cli import (
    AntigravityCLIImageAdapter,
    AntigravityCLIRuntime,
)
from app.model_adapters.codex_cli import CodexCLIImageAdapter, CodexCLIRuntime
from app.model_adapters.compatible import (
    AnthropicCompatibleAdapter,
    CompatibleRuntime,
    OpenAICompatibleAdapter,
)
from app.model_adapters.google import (
    GoogleImageAdapter,
    GoogleRuntime,
    GoogleTextAdapter,
)
from app.model_adapters.grok_build_cli import (
    GrokBuildCLIImageAdapter,
    GrokBuildCLIRuntime,
)
from app.model_adapters.vertex import VertexImageAdapter, VertexTextAdapter
from app.models import AIModel, ProviderConnection, ProviderKey, ProviderProfile, RoutingPolicy
from app.services.credential_crypto import SelectedProviderKey, select_provider_key
from app.services.credential_source import (
    CLI_SESSION,
    ENV_SERVICE_ACCOUNT,
    connection_credential_source,
    environment_credentials_ready,
)
from app.services.model_registry import ModelCapability, build_registry
from app.services.provider_presets import ensure_provider_presets, proxy_url_for_connection

ALIAS_ALIASES = {
    "image.fast": "image.nano_banana_2",
    "image.quality": "image.nano_banana_pro",
}


@dataclass(frozen=True)
class ResolvedModel:
    model: AIModel
    connection: ProviderConnection
    provider: ProviderProfile
    route_score: float | None = None
    route_reason: str = "EXPLICIT"


@dataclass(frozen=True)
class AdapterBinding:
    resolved: ResolvedModel
    adapter: Any
    selected_key: SelectedProviderKey | None


def get_catalog_model(db: Session, reference: str) -> AIModel | None:
    model = db.get(AIModel, reference)
    if model is not None:
        return model
    normalized_reference = ALIAS_ALIASES.get(reference, reference)
    model = db.get(AIModel, normalized_reference)
    if model is not None:
        return model
    return db.scalar(
        select(AIModel).where(AIModel.legacy_alias == normalized_reference)
    )


def model_supports_resolution(model: AIModel, resolution: str) -> bool:
    supported = (model.capabilities or {}).get("resolutions") or []
    return not supported or resolution in supported


def model_operation_verified(model: AIModel, operation: str) -> bool:
    if model.confidence != "VERIFIED":
        return False
    verified_operations = (model.capabilities or {}).get("verified_operations")
    return verified_operations is None or operation in verified_operations


def resolve_model(
    db: Session,
    settings: Settings,
    *,
    operation: str,
    explicit_reference: str | None = None,
    project_id: str | None = None,
    task_kind: str | None = None,
) -> ResolvedModel:
    ensure_provider_presets(db, settings, auto_commit=False)
    if operation.startswith("image_") and (
        not explicit_reference or explicit_reference.casefold() == "auto"
    ):
        raise HTTPException(
            status_code=422,
            detail="图片任务必须显式选择模型，以保持项目画风一致",
        )
    if explicit_reference and explicit_reference.lower() != "auto":
        model = get_catalog_model(db, explicit_reference)
        if model is None:
            raise HTTPException(status_code=422, detail="未识别的模型")
        resolved = _resolved_row(db, model)
        _require_eligible(resolved, operation, explicit=True)
        _require_available_credentials(db, settings, resolved, explicit=True)
        return resolved

    policy = _routing_policy(db, project_id, task_kind or operation)
    if policy and policy.mode == "EXPLICIT":
        raise HTTPException(status_code=409, detail="当前任务策略要求显式选择模型")
    candidates: list[ResolvedModel] = []
    for model in db.scalars(select(AIModel).where(AIModel.enabled.is_(True))):
        resolved = _resolved_row(db, model)
        try:
            _require_eligible(resolved, operation, explicit=False)
        except HTTPException:
            continue
        if policy and not set(policy.required_operations or []).issubset(
            set(model.operations or [])
        ):
            continue
        try:
            _require_available_credentials(db, settings, resolved, explicit=False)
        except HTTPException:
            continue
        score = _route_score(model, policy)
        candidates.append(
            ResolvedModel(
                model=model,
                connection=resolved.connection,
                provider=resolved.provider,
                route_score=score,
                route_reason="AUTO",
            )
        )
    if not candidates:
        raise HTTPException(status_code=409, detail="没有已验证且满足任务能力的模型")
    return max(candidates, key=lambda item: item.route_score or 0)


def bind_adapter(
    db: Session,
    settings: Settings,
    *,
    operation: str,
    explicit_reference: str | None = None,
    project_id: str | None = None,
    task_kind: str | None = None,
) -> AdapterBinding:
    resolved = resolve_model(
        db,
        settings,
        operation=operation,
        explicit_reference=explicit_reference,
        project_id=project_id,
        task_kind=task_kind,
    )
    model = resolved.model
    connection = resolved.connection
    settings_key: SelectedProviderKey | None = None
    if connection.protocol == "VERTEX_NATIVE":
        capability = _catalog_capability(
            model,
            resolved.provider.name,
            build_registry(settings).get(model.legacy_alias or ""),
        )
        adapter = (
            VertexImageAdapter(settings, capability)
            if model.model_type == "IMAGE"
            else VertexTextAdapter(settings, capability)
        )
    elif connection.protocol == "CLI_CODEX":
        config = connection.nonsecret_config or {}
        executable = str(config.get("cli_executable") or "codex")
        runtime = CodexCLIRuntime(
            settings=settings,
            connection_id=connection.id,
            catalog_model_id=model.id,
            provider_model_id=model.provider_model_id,
            executable=executable,
            capabilities=model.capabilities or {},
            session_factory=SessionLocal,
        )
        adapter = CodexCLIImageAdapter(runtime)
    elif connection.protocol == "CLI_ANTIGRAVITY":
        config = connection.nonsecret_config or {}
        executable = str(config.get("cli_executable") or "agy")
        runtime = AntigravityCLIRuntime(
            settings=settings,
            connection_id=connection.id,
            catalog_model_id=model.id,
            provider_model_id=model.provider_model_id,
            executable=executable,
            capabilities=model.capabilities or {},
            session_factory=SessionLocal,
        )
        adapter = AntigravityCLIImageAdapter(runtime)
    elif connection.protocol == "CLI_GROK_BUILD":
        config = connection.nonsecret_config or {}
        executable = str(config.get("cli_executable") or "grok")
        runtime = GrokBuildCLIRuntime(
            settings=settings,
            connection_id=connection.id,
            catalog_model_id=model.id,
            provider_model_id=model.provider_model_id,
            executable=executable,
            capabilities=model.capabilities or {},
            session_factory=SessionLocal,
        )
        adapter = GrokBuildCLIImageAdapter(runtime)
    else:
        settings_key = select_provider_key(db, settings, connection.id)
        if connection.protocol == "GOOGLE_NATIVE":
            runtime = GoogleRuntime(
                api_key=settings_key.secret,
                model_id=model.provider_model_id,
                display_name=model.display_name,
                capabilities=model.capabilities or {},
            )
            adapter = (
                GoogleImageAdapter(runtime)
                if model.model_type == "IMAGE"
                else GoogleTextAdapter(runtime)
            )
        else:
            runtime = CompatibleRuntime(
                provider_name=resolved.provider.name,
                protocol=connection.protocol,
                base_url=connection.base_url,
                api_key=settings_key.secret,
                model_id=model.provider_model_id,
                endpoint_templates=connection.endpoint_templates or {},
                extra_headers={
                    **(connection.extra_headers or {}),
                    **((model.capabilities or {}).get("extra_headers") or {}),
                },
                use_responses_api=connection.use_responses_api,
                capabilities=model.capabilities or {},
                allow_private_networks=settings.allow_private_provider_networks,
                max_response_bytes=settings.max_upload_bytes,
                allow_http_loopback=(
                    settings.environment.lower() == "development"
                    and urlparse(connection.base_url).hostname
                    in {"localhost", "127.0.0.1", "::1"}
                ),
                proxy_url=proxy_url_for_connection(
                    resolved.provider, connection, settings
                ),
            )
            adapter = (
                AnthropicCompatibleAdapter(runtime)
                if connection.protocol == "ANTHROPIC"
                else OpenAICompatibleAdapter(runtime)
            )
    return AdapterBinding(resolved=resolved, adapter=adapter, selected_key=settings_key)


def _resolved_row(db: Session, model: AIModel) -> ResolvedModel:
    connection = db.get(ProviderConnection, model.connection_id)
    if connection is None:
        raise HTTPException(status_code=409, detail="模型所属连接已不存在")
    provider = db.get(ProviderProfile, connection.provider_id)
    if provider is None:
        raise HTTPException(status_code=409, detail="模型所属供应商已不存在")
    return ResolvedModel(model=model, connection=connection, provider=provider)


def _require_eligible(
    resolved: ResolvedModel, operation: str, *, explicit: bool
) -> None:
    if (
        not resolved.model.enabled
        or not resolved.connection.enabled
        or not resolved.provider.enabled
    ):
        raise HTTPException(status_code=409, detail="模型或供应商当前已停用")
    if operation not in (resolved.model.operations or []):
        raise HTTPException(status_code=422, detail="所选模型不支持当前任务")
    if resolved.connection.protocol == "ANTHROPIC" and operation.startswith("image_"):
        raise HTTPException(status_code=422, detail="Anthropic 协议连接不支持图片生成任务")
    if not explicit and not model_operation_verified(resolved.model, operation):
        raise HTTPException(status_code=409, detail="未经能力测试的模型不能参与自动路由")
    if not explicit and resolved.connection.health_state != "HEALTHY":
        raise HTTPException(status_code=409, detail="连接尚未通过健康验证")


def _require_available_credentials(
    db: Session,
    settings: Settings,
    resolved: ResolvedModel,
    *,
    explicit: bool,
) -> None:
    credential_source = connection_credential_source(resolved.connection)
    if credential_source == CLI_SESSION:
        if resolved.connection.health_state != "AVAILABLE":
            raise HTTPException(
                status_code=409,
                detail="CLI 通道尚未通过安装、登录与能力探测",
            )
        return
    if credential_source == ENV_SERVICE_ACCOUNT:
        if not environment_credentials_ready(settings, resolved.connection.protocol):
            raise HTTPException(
                status_code=409,
                detail="供应商连接的环境凭据尚未配置完整",
            )
        return
    if not settings.provider_credentials_writable:
        raise HTTPException(
            status_code=503 if explicit else 409,
            detail="服务端供应商凭据主密钥未配置或无效",
        )
    has_key = db.scalar(
        select(ProviderKey.id).where(
            ProviderKey.connection_id == resolved.connection.id,
            ProviderKey.enabled.is_(True),
            or_(
                ProviderKey.cooldown_until.is_(None),
                ProviderKey.cooldown_until <= datetime.now(UTC),
            ),
        )
    )
    if has_key is None:
        raise HTTPException(status_code=409, detail="供应商连接没有未冷却的可用 API Key")


def _routing_policy(
    db: Session, project_id: str | None, task_kind: str
) -> RoutingPolicy | None:
    if project_id:
        policy = db.scalar(
            select(RoutingPolicy).where(
                RoutingPolicy.project_id == project_id,
                RoutingPolicy.task_kind == task_kind,
                RoutingPolicy.enabled.is_(True),
            )
        )
        if policy:
            return policy
    return db.scalar(
        select(RoutingPolicy).where(
            RoutingPolicy.project_id.is_(None),
            RoutingPolicy.task_kind == task_kind,
            RoutingPolicy.enabled.is_(True),
        )
    )


def _route_score(model: AIModel, policy: RoutingPolicy | None) -> float:
    weights = (
        policy.weights
        if policy
        else {"reliability": 45, "priority": 25, "latency": 20, "cost": 10}
    )
    reliability = (model.success_rate * 100) if model.success_rate is not None else 50
    priority = max(0, min(100, model.priority))
    latency = 50 if model.median_latency_ms is None else max(
        0, 100 - model.median_latency_ms / 50
    )
    cost_tier = str((model.pricing or {}).get("relative_cost", "MEDIUM")).upper()
    cost = {"LOW": 90, "MEDIUM": 60, "HIGH": 25}.get(cost_tier, 50)
    return (
        reliability * weights["reliability"]
        + priority * weights["priority"]
        + latency * weights["latency"]
        + cost * weights["cost"]
    ) / 100


def _catalog_capability(
    model: AIModel,
    provider_name: str,
    fallback: ModelCapability | None = None,
) -> ModelCapability:
    capabilities = model.capabilities or {}
    fallback_resolutions = fallback.resolutions if fallback else ()
    fallback_preview_resolutions = fallback.preview_resolutions if fallback else ()
    fallback_max_references = fallback.max_reference_images if fallback else 0
    fallback_regions = fallback.regions if fallback else ("global",)
    fallback_parameters = fallback.supported_parameters if fallback else ()
    return ModelCapability(
        provider=provider_name,
        model_id=model.provider_model_id,
        logical_alias=model.legacy_alias or model.id,
        display_name=model.display_name,
        operations=tuple(model.operations or []),
        resolutions=tuple(capabilities.get("resolutions", fallback_resolutions) or ()),
        preview_resolutions=tuple(
            capabilities.get("preview_resolutions", fallback_preview_resolutions) or ()
        ),
        max_reference_images=int(
            capabilities.get("max_reference_images", fallback_max_references) or 0
        ),
        regions=tuple(capabilities.get("regions", fallback_regions) or ()),
        supported_parameters=tuple(
            capabilities.get("supported_parameters", fallback_parameters) or ()
        ),
    )
