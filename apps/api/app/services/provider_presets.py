from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import AIModel, ProviderConnection, ProviderHealth, ProviderProfile
from app.services.credential_source import (
    CLI_SESSION,
    ENV_SERVICE_ACCOUNT,
    credential_source_for_protocol,
    default_cli_executable_for_protocol,
)
from app.services.model_capabilities import whole_image_reference_edit_capabilities

OPENAI_ENDPOINTS = {
    "models": "/models",
    "chat": "/chat/completions",
    "responses": "/responses",
    "images_generate": "/images/generations",
    "images_edit": "/images/edits",
}
ANTHROPIC_ENDPOINTS = {"models": "/models", "messages": "/messages"}


@dataclass(frozen=True)
class ProviderPreset:
    key: str
    name: str
    protocol: str
    base_url: str
    category: str = "OFFICIAL"
    risk_label: str = "OFFICIAL"
    documentation_url: str | None = None
    use_responses_api: bool = False
    endpoint_templates: dict[str, str] = field(default_factory=dict)
    extra_headers: dict[str, str] = field(default_factory=dict)
    balance_config: dict = field(default_factory=dict)


def _openai(
    key: str,
    name: str,
    base_url: str,
    *,
    category: str = "OFFICIAL",
    risk_label: str = "OFFICIAL",
    documentation_url: str | None = None,
    use_responses_api: bool = False,
    balance_config: dict | None = None,
) -> ProviderPreset:
    return ProviderPreset(
        key=key,
        name=name,
        protocol="OPENAI",
        base_url=base_url,
        category=category,
        risk_label=risk_label,
        documentation_url=documentation_url,
        use_responses_api=use_responses_api,
        endpoint_templates=OPENAI_ENDPOINTS,
        balance_config=balance_config or {},
    )


PRESETS: tuple[ProviderPreset, ...] = (
    ProviderPreset(
        key="vertex-ai",
        name="Vertex AI",
        protocol="VERTEX_NATIVE",
        base_url="vertex://google-cloud",
        documentation_url="https://cloud.google.com/vertex-ai/generative-ai/docs",
    ),
    ProviderPreset(
        key="gemini-api",
        name="Gemini API",
        protocol="GOOGLE_NATIVE",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        documentation_url="https://ai.google.dev/gemini-api/docs",
        endpoint_templates={"models": "/models"},
    ),
    _openai(
        "openai",
        "OpenAI",
        "https://api.openai.com/v1",
        documentation_url="https://platform.openai.com/docs",
        use_responses_api=True,
    ),
    ProviderPreset(
        key="codex-cli",
        name="Codex CLI",
        protocol="CLI_CODEX",
        base_url="cli://codex",
        documentation_url="https://developers.openai.com/codex/cli",
    ),
    ProviderPreset(
        key="antigravity-cli",
        name="Antigravity CLI",
        protocol="CLI_ANTIGRAVITY",
        base_url="cli://antigravity",
        documentation_url="https://antigravity.google/docs/cli/headless/",
    ),
    ProviderPreset(
        key="grok-build-cli",
        name="Grok Build CLI",
        protocol="CLI_GROK_BUILD",
        base_url="cli://grok-build",
        documentation_url="https://docs.x.ai/build/overview",
    ),
    ProviderPreset(
        key="anthropic",
        name="Anthropic",
        protocol="ANTHROPIC",
        base_url="https://api.anthropic.com/v1",
        documentation_url="https://platform.claude.com/docs",
        endpoint_templates=ANTHROPIC_ENDPOINTS,
        extra_headers={"anthropic-version": "2023-06-01"},
    ),
    _openai(
        "deepseek",
        "DeepSeek",
        "https://api.deepseek.com/v1",
        documentation_url="https://api-docs.deepseek.com/zh-cn/",
        balance_config={
            "enabled": True,
            "path": "/user/balance",
            "result_path": "balance_infos.0.total_balance",
        },
    ),
    _openai(
        "openrouter",
        "OpenRouter",
        "https://openrouter.ai/api/v1",
        category="GATEWAY",
        risk_label="THIRD_PARTY",
        documentation_url="https://openrouter.ai/docs",
        balance_config={
            "enabled": True,
            "path": "/credits",
            "result_path": "data.total_credits",
            "usage_path": "data.total_usage",
        },
    ),
    _openai(
        "opencode-zen",
        "OpenCode Zen",
        "https://opencode.ai/zen/v1",
        category="GATEWAY",
        risk_label="THIRD_PARTY",
        documentation_url="https://opencode.ai/docs/zen/",
    ),
    _openai(
        "siliconflow",
        "硅基流动",
        "https://api.siliconflow.cn/v1",
        category="GATEWAY",
        risk_label="THIRD_PARTY",
        documentation_url="https://docs.siliconflow.cn/",
        balance_config={
            "enabled": True,
            "path": "/user/info",
            "result_path": "data.totalBalance",
        },
    ),
    _openai(
        "vercel-ai-gateway",
        "Vercel AI Gateway",
        "https://ai-gateway.vercel.sh/v1",
        category="GATEWAY",
        risk_label="THIRD_PARTY",
        documentation_url="https://vercel.com/docs/ai-gateway",
        balance_config={"enabled": True, "path": "/credits", "result_path": "balance"},
    ),
    _openai(
        "dashscope",
        "阿里云百炼",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        documentation_url="https://help.aliyun.com/zh/model-studio/",
    ),
    _openai(
        "volcengine-ark",
        "火山方舟",
        "https://ark.cn-beijing.volces.com/api/v3",
        documentation_url="https://www.volcengine.com/docs/82379",
    ),
    _openai(
        "moonshot",
        "月之暗面",
        "https://api.moonshot.cn/v1",
        documentation_url="https://platform.moonshot.cn/docs",
        balance_config={
            "enabled": True,
            "path": "/users/me/balance",
            "result_path": "data.available_balance",
        },
    ),
    _openai(
        "zhipu",
        "智谱 GLM",
        "https://open.bigmodel.cn/api/paas/v4",
        documentation_url="https://docs.bigmodel.cn/",
    ),
    _openai(
        "stepfun",
        "阶跃星辰",
        "https://api.stepfun.com/v1",
        documentation_url="https://platform.stepfun.com/docs",
    ),
    _openai(
        "tencent-hunyuan",
        "腾讯混元",
        "https://api.hunyuan.cloud.tencent.com/v1",
        documentation_url="https://cloud.tencent.com/document/product/1729",
    ),
    _openai(
        "xai",
        "xAI",
        "https://api.x.ai/v1",
        documentation_url="https://docs.x.ai/",
        use_responses_api=True,
    ),
    ProviderPreset(
        key="minimax-anthropic",
        name="MiniMax",
        protocol="ANTHROPIC",
        base_url="https://api.minimaxi.com/anthropic/v1",
        documentation_url="https://platform.minimaxi.com/document",
        endpoint_templates=ANTHROPIC_ENDPOINTS,
        extra_headers={"anthropic-version": "2023-06-01"},
    ),
    _openai(
        "mimo",
        "MIMO",
        "https://api.xiaomimimo.com/v1",
        documentation_url="https://platform.xiaomimimo.com/",
    ),
    _openai(
        "302-ai",
        "302.AI",
        "https://api.302.ai/v1",
        category="GATEWAY",
        risk_label="THIRD_PARTY",
        documentation_url="https://302.ai/",
    ),
    _openai(
        "aihubmix",
        "AiHubMix",
        "https://aihubmix.com/v1",
        category="GATEWAY",
        risk_label="THIRD_PARTY",
        documentation_url="https://aihubmix.com/",
    ),
    _openai(
        "tokenpony",
        "小马算力",
        "https://api.tokenpony.cn/v1",
        category="GATEWAY",
        risk_label="THIRD_PARTY",
        documentation_url="https://tokenpony.cn/",
    ),
    _openai(
        "suixiang",
        "随想 AI",
        "https://sui-xiang.com/v1",
        category="GATEWAY",
        risk_label="THIRD_PARTY",
        documentation_url="https://sui-xiang.com/",
    ),
    _openai(
        "ackai",
        "AckAI",
        "https://ackai.fun/v1",
        category="GATEWAY",
        risk_label="THIRD_PARTY",
        documentation_url="https://ackai.fun/",
    ),
)


def preset_dicts() -> list[dict]:
    return [
        {
            "key": item.key,
            "name": item.name,
            "protocol": item.protocol,
            "base_url": item.base_url,
            "category": item.category,
            "risk_label": item.risk_label,
            "documentation_url": item.documentation_url,
            "use_responses_api": item.use_responses_api,
            "endpoint_templates": dict(item.endpoint_templates),
            "balance_config": dict(item.balance_config),
        }
        for item in PRESETS
    ]


def proxy_url_for_connection(
    profile: ProviderProfile,
    connection: ProviderConnection,
    settings: Settings,
) -> str | None:
    """Only route unchanged built-in API origins through the operator proxy."""

    if not settings.mangaflow_proxy_url or not profile.built_in or not profile.preset_key:
        return None
    preset = next((item for item in PRESETS if item.key == profile.preset_key), None)
    if (
        preset is None
        or preset.protocol != connection.protocol
        or preset.base_url.rstrip("/") != connection.base_url.rstrip("/")
    ):
        return None
    return settings.mangaflow_proxy_url


def ensure_provider_presets(
    db: Session, settings: Settings, *, auto_commit: bool = True
) -> None:
    provider_catalog_empty = db.scalar(select(ProviderProfile.id).limit(1)) is None
    existing = {
        row.preset_key: row
        for row in db.scalars(
            select(ProviderProfile).where(ProviderProfile.preset_key.is_not(None))
        )
    }
    created_vertex_profile = False
    for preset in PRESETS:
        profile = existing.get(preset.key)
        if profile is None:
            profile = ProviderProfile(
                preset_key=preset.key,
                name=preset.name,
                category=preset.category,
                description="内置供应商预设",
                built_in=True,
                enabled=True,
                risk_label=preset.risk_label,
                documentation_url=preset.documentation_url,
            )
            db.add(profile)
            db.flush()
            if preset.key == "vertex-ai":
                created_vertex_profile = True
            credential_source = credential_source_for_protocol(preset.protocol)
            environment_credentials_ready = (
                credential_source == ENV_SERVICE_ACCOUNT
                and settings.vertex_configured
            )
            nonsecret_config = {"preset_version": 1, "overridden_fields": []}
            if credential_source == ENV_SERVICE_ACCOUNT and not environment_credentials_ready:
                nonsecret_config["auto_enable_pending"] = True
            if credential_source == CLI_SESSION:
                nonsecret_config["cli_executable"] = default_cli_executable_for_protocol(
                    preset.protocol
                )
            connection = ProviderConnection(
                provider_id=profile.id,
                name="默认连接",
                protocol=preset.protocol,
                base_url=preset.base_url,
                enabled=environment_credentials_ready,
                use_responses_api=preset.use_responses_api,
                endpoint_templates=dict(preset.endpoint_templates),
                extra_headers=dict(preset.extra_headers),
                balance_config=dict(preset.balance_config),
                nonsecret_config=nonsecret_config,
                health_state=(
                    "DEGRADED"
                    if environment_credentials_ready
                    else "UNKNOWN"
                    if credential_source == CLI_SESSION
                    else "UNCONFIGURED"
                ),
                message=(
                    "等待 CLI 探测"
                    if credential_source == CLI_SESSION
                    else "等待配置与验证"
                ),
            )
            db.add(connection)
            db.flush()
        else:
            profile.name = preset.name
            profile.category = preset.category
            profile.risk_label = preset.risk_label
            profile.documentation_url = preset.documentation_url

    db.flush()
    sync_vertex_connection_health(db, settings)
    if created_vertex_profile and provider_catalog_empty:
        _ensure_vertex_models(db, settings)
    _ensure_codex_cli_model(db)
    _ensure_antigravity_cli_model(db)
    _ensure_grok_build_cli_model(db)
    db.flush()
    if auto_commit:
        db.commit()


def sync_vertex_connection_health(
    db: Session,
    settings: Settings,
    legacy_health: ProviderHealth | None = None,
) -> None:
    profile = db.scalar(
        select(ProviderProfile).where(ProviderProfile.preset_key == "vertex-ai")
    )
    if profile is None:
        return
    connection = db.scalar(
        select(ProviderConnection).where(ProviderConnection.provider_id == profile.id)
    )
    if connection is None:
        return
    nonsecret_config = dict(connection.nonsecret_config or {})
    if nonsecret_config.get("auto_enable_pending") and settings.vertex_configured:
        connection.enabled = True
        nonsecret_config.pop("auto_enable_pending", None)
        connection.nonsecret_config = nonsecret_config
    if not settings.vertex_configured:
        connection.health_state = "UNCONFIGURED"
        connection.message = "等待配置 Vertex 服务账号"
        return
    health = legacy_health or db.scalar(
        select(ProviderHealth).where(ProviderHealth.provider == "vertex-ai")
    )
    if health is None:
        connection.health_state = "DEGRADED"
        connection.message = "Vertex 凭据已配置，等待能力验证"
        return
    connection.health_state = health.health_state
    connection.last_checked_at = health.last_checked_at
    connection.last_success_at = health.last_success_at
    connection.latency_ms = health.latency_ms
    connection.error_code = health.error_code
    connection.message = health.message


def _ensure_vertex_models(db: Session, settings: Settings) -> None:
    profile = db.scalar(
        select(ProviderProfile).where(ProviderProfile.preset_key == "vertex-ai")
    )
    if profile is None:
        return
    connection = db.scalar(
        select(ProviderConnection).where(ProviderConnection.provider_id == profile.id)
    )
    if connection is None:
        return
    definitions = (
        {
            "legacy_alias": "text.fast",
            "provider_model_id": settings.vertex_text_model,
            "display_name": "Gemini 3.5 Flash",
            "model_type": "TEXT",
            "input_modalities": ["TEXT", "IMAGE"],
            "output_modalities": ["TEXT"],
            "operations": ["structured_text", "multimodal_analysis"],
            "api_surfaces": ["GOOGLE_GENERATE_CONTENT"],
            "capabilities": {
                "structured_output_mode": "STRICT_SCHEMA",
                "supported_parameters": ["thinking_budget"],
            },
        },
        {
            "legacy_alias": "image.nano_banana_2",
            "provider_model_id": settings.vertex_image_model_nano_banana_2,
            "display_name": "Nano Banana 2",
            "model_type": "IMAGE",
            "input_modalities": ["TEXT", "IMAGE"],
            "output_modalities": ["IMAGE"],
            "operations": ["image_generate", "image_edit"],
            "api_surfaces": ["GOOGLE_GENERATE_CONTENT"],
            "capabilities": {
                "resolutions": ["1K", "2K", "4K"],
                "preview_resolutions": ["4K"],
                "max_reference_images": 14,
                # V02-44B matrix §1.2/§6: the adapter surface has no mask
                # parameter, so the preset declares whole-image-reference-only
                # instead of pretending native inpaint exists.
                **whole_image_reference_edit_capabilities(),
            },
        },
        {
            "legacy_alias": "image.nano_banana_pro",
            "provider_model_id": settings.vertex_image_model_nano_banana_pro,
            "display_name": "Nano Banana Pro",
            "model_type": "IMAGE",
            "input_modalities": ["TEXT", "IMAGE"],
            "output_modalities": ["IMAGE"],
            "operations": ["image_generate", "image_edit"],
            "api_surfaces": ["GOOGLE_GENERATE_CONTENT"],
            "capabilities": {
                "resolutions": ["1K", "2K", "4K"],
                "preview_resolutions": ["4K"],
                "max_reference_images": 14,
                **whole_image_reference_edit_capabilities(),
            },
        },
    )
    for definition in definitions:
        model = db.scalar(
            select(AIModel).where(AIModel.legacy_alias == definition["legacy_alias"])
        )
        # Existing rows are user-owned. In particular, startup seeding must
        # never overwrite enabled, display_enabled, priority, or verification.
        if model is None:
            model = AIModel(
                connection_id=connection.id,
                source="PRESET",
                confidence="DECLARED",
                enabled=True,
                priority=90,
                **definition,
            )
            db.add(model)


def _ensure_codex_cli_model(db: Session) -> None:
    profile = db.scalar(
        select(ProviderProfile).where(ProviderProfile.preset_key == "codex-cli")
    )
    if profile is None:
        return
    connection = db.scalar(
        select(ProviderConnection).where(ProviderConnection.provider_id == profile.id)
    )
    if connection is None:
        return
    existing = db.scalar(
        select(AIModel).where(
            AIModel.connection_id == connection.id,
            AIModel.provider_model_id == "codex-imagegen",
        )
    )
    if existing is not None:
        return
    db.add(
        AIModel(
            connection_id=connection.id,
            provider_model_id="codex-imagegen",
            display_name="Codex CLI ImageGen",
            model_type="IMAGE",
            input_modalities=["TEXT", "IMAGE"],
            output_modalities=["IMAGE"],
            operations=["image_generate", "image_edit"],
            api_surfaces=["CODEX_EXEC_IMAGEGEN"],
            capabilities={
                "resolutions": ["1K"],
                "max_reference_images": 5,
                "cost_source": "CLI_EXTERNAL",
                # V02-44B: CLI image edit is a whole-image reference call with
                # no mask request surface.
                **whole_image_reference_edit_capabilities(),
            },
            pricing={"mode": "UNKNOWN"},
            source="PRESET",
            confidence="DECLARED",
            enabled=True,
            priority=50,
        )
    )


def _ensure_antigravity_cli_model(db: Session) -> None:
    profile = db.scalar(
        select(ProviderProfile).where(ProviderProfile.preset_key == "antigravity-cli")
    )
    if profile is None:
        return
    connection = db.scalar(
        select(ProviderConnection).where(ProviderConnection.provider_id == profile.id)
    )
    if connection is None:
        return
    existing = db.scalar(
        select(AIModel).where(
            AIModel.connection_id == connection.id,
            AIModel.provider_model_id == "antigravity-imagegen",
        )
    )
    if existing is not None:
        return
    db.add(
        AIModel(
            connection_id=connection.id,
            provider_model_id="antigravity-imagegen",
            display_name="Antigravity CLI ImageGen",
            model_type="IMAGE",
            input_modalities=["TEXT", "IMAGE"],
            output_modalities=["IMAGE"],
            operations=["image_generate", "image_edit"],
            api_surfaces=["ANTIGRAVITY_HEADLESS_GENERATE_IMAGE"],
            capabilities={
                "resolutions": ["1K"],
                "max_reference_images": 1,
                "cost_source": "CLI_EXTERNAL",
                # V02-44B: single-reference whole-image edit, no mask surface.
                **whole_image_reference_edit_capabilities(),
            },
            pricing={"mode": "UNKNOWN"},
            source="PRESET",
            confidence="DECLARED",
            enabled=True,
            priority=50,
        )
    )


def _ensure_grok_build_cli_model(db: Session) -> None:
    profile = db.scalar(
        select(ProviderProfile).where(ProviderProfile.preset_key == "grok-build-cli")
    )
    if profile is None:
        return
    connection = db.scalar(
        select(ProviderConnection).where(ProviderConnection.provider_id == profile.id)
    )
    if connection is None:
        return
    existing = db.scalar(
        select(AIModel).where(
            AIModel.connection_id == connection.id,
            AIModel.provider_model_id == "grok-build-imagine",
        )
    )
    if existing is not None:
        return
    db.add(
        AIModel(
            connection_id=connection.id,
            provider_model_id="grok-build-imagine",
            display_name="Grok Build Imagine",
            model_type="IMAGE",
            input_modalities=["TEXT", "IMAGE"],
            output_modalities=["IMAGE"],
            operations=["image_generate", "image_edit"],
            api_surfaces=["GROK_BUILD_MEDIA_TOOLS"],
            capabilities={
                "resolutions": ["1K"],
                "max_reference_images": 5,
                "cost_source": "CLI_EXTERNAL",
                # V02-44B: media-tool image edit is whole-image reference, no
                # mask request surface.
                **whole_image_reference_edit_capabilities(),
            },
            pricing={"mode": "UNKNOWN"},
            source="PRESET",
            confidence="DECLARED",
            enabled=True,
            priority=50,
        )
    )
