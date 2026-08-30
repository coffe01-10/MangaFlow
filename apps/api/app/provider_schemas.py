from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class ProviderPresetRead(BaseModel):
    key: str
    name: str
    protocol: str
    base_url: str
    category: str
    risk_label: str
    documentation_url: str | None
    use_responses_api: bool
    endpoint_templates: dict[str, str]
    balance_config: dict[str, Any]


class ProviderKeyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    label: str
    key_hint: str
    enabled: bool
    health_state: str
    cooldown_until: datetime | None
    last_used_at: datetime | None
    last_error_code: str | None
    created_at: datetime
    updated_at: datetime


class ProviderConnectionRead(BaseModel):
    id: str
    provider_id: str
    name: str
    protocol: str
    base_url: str
    enabled: bool
    configured: bool
    credential_writable: bool
    use_responses_api: bool
    endpoint_templates: dict[str, str]
    extra_headers: dict[str, str]
    balance_config: dict[str, Any]
    nonsecret_config: dict[str, Any]
    health_state: str
    last_checked_at: datetime | None
    last_success_at: datetime | None
    latency_ms: int | None
    error_code: str | None
    message: str
    key_count: int
    model_count: int
    keys: list[ProviderKeyRead]
    version: int


class ProviderProfileRead(BaseModel):
    id: str
    preset_key: str | None
    name: str
    category: str
    description: str
    built_in: bool
    enabled: bool
    risk_label: str
    documentation_url: str | None
    connections: list[ProviderConnectionRead]
    version: int


class ProviderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    protocol: Literal["OPENAI", "ANTHROPIC"]
    base_url: str = Field(min_length=8, max_length=500)
    use_responses_api: bool = False


class ProviderUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    enabled: bool | None = None
    version: int = Field(ge=1)


class ConnectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="备用连接", min_length=1, max_length=120)
    protocol: Literal["OPENAI", "ANTHROPIC"]
    base_url: str = Field(min_length=8, max_length=500)
    use_responses_api: bool = False


class ConnectionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    base_url: str | None = Field(default=None, min_length=8, max_length=500)
    enabled: bool | None = None
    use_responses_api: bool | None = None
    endpoint_templates: dict[str, str] | None = None
    extra_headers: dict[str, str] | None = None
    balance_config: dict[str, Any] | None = None
    nonsecret_config: dict[str, Any] | None = None
    version: int = Field(ge=1)


class ProviderKeyWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(default="default", min_length=1, max_length=80)
    api_key: str = Field(min_length=1, max_length=8192)


class ProviderModelRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    connection_id: str
    provider_model_id: str
    display_name: str
    legacy_alias: str | None
    model_type: str
    input_modalities: list[str]
    output_modalities: list[str]
    operations: list[str]
    api_surfaces: list[str]
    capabilities: dict[str, Any]
    pricing: dict[str, Any]
    source: str
    confidence: str
    enabled: bool
    priority: int
    success_rate: float | None
    median_latency_ms: int | None
    last_verified_at: datetime | None
    created_at: datetime
    updated_at: datetime
    version: int


class ProviderModelCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_model_id: str = Field(min_length=1, max_length=200)
    display_name: str | None = Field(default=None, max_length=200)
    model_type: Literal["TEXT", "IMAGE"] = "TEXT"
    input_modalities: list[Literal["TEXT", "IMAGE"]] = Field(default_factory=lambda: ["TEXT"])
    output_modalities: list[Literal["TEXT", "IMAGE"]] = Field(default_factory=lambda: ["TEXT"])
    operations: list[str] = Field(default_factory=list)
    api_surfaces: list[str] = Field(default_factory=list)
    capabilities: dict[str, Any] = Field(default_factory=dict)


class ProviderModelUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    model_type: Literal["TEXT", "IMAGE"] | None = None
    input_modalities: list[Literal["TEXT", "IMAGE"]] | None = None
    output_modalities: list[Literal["TEXT", "IMAGE"]] | None = None
    operations: list[str] | None = None
    api_surfaces: list[str] | None = None
    capabilities: dict[str, Any] | None = None
    pricing: dict[str, Any] | None = None
    enabled: bool | None = None
    priority: int | None = Field(default=None, ge=0, le=100)
    version: int = Field(ge=1)


class _ModelPricingVersionFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=120)
    model_id: str = Field(min_length=1, max_length=128)
    pricing_version: str = Field(min_length=1, max_length=64)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    effective_from: datetime
    effective_to: datetime | None = None
    input_tokens_per_million: Decimal | None = Field(
        default=None, ge=0, max_digits=20, decimal_places=8
    )
    output_tokens_per_million: Decimal | None = Field(
        default=None, ge=0, max_digits=20, decimal_places=8
    )
    output_image_each: Decimal | None = Field(
        default=None, ge=0, max_digits=20, decimal_places=8
    )
    request_each: Decimal | None = Field(
        default=None, ge=0, max_digits=20, decimal_places=8
    )


class ModelPricingVersionCreate(_ModelPricingVersionFields):
    @model_validator(mode="after")
    def validate_window_and_rates(self):
        if self.effective_from.utcoffset() is None or (
            self.effective_to is not None and self.effective_to.utcoffset() is None
        ):
            raise ValueError("价格生效时间必须包含明确时区")
        self.effective_from = self.effective_from.astimezone(UTC)
        if self.effective_to is not None:
            self.effective_to = self.effective_to.astimezone(UTC)
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("价格结束时间必须晚于开始时间")
        if all(
            rate is None
            for rate in (
                self.input_tokens_per_million,
                self.output_tokens_per_million,
                self.output_image_each,
                self.request_each,
            )
        ):
            raise ValueError("至少配置一种明确计价单位")
        return self


class ModelPricingVersionRead(_ModelPricingVersionFields):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime

    @field_validator("effective_from", "effective_to", "created_at", mode="before")
    @classmethod
    def restore_utc_for_naive_database_values(cls, value):
        if isinstance(value, datetime) and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


class ConnectionTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    test_type: Literal["CREDENTIALS", "TEXT", "VISION", "IMAGE", "BENCHMARK"] = (
        "CREDENTIALS"
    )
    model_id: str | None = None
    acknowledge_cost: bool = False
    runs: int = Field(default=1, ge=1, le=3)

    @model_validator(mode="after")
    def validate_test(self):
        if self.test_type in {"TEXT", "VISION", "IMAGE", "BENCHMARK"} and not self.model_id:
            raise ValueError("模型测试必须选择模型")
        if self.test_type in {"IMAGE", "BENCHMARK"} and not self.acknowledge_cost:
            raise ValueError("图片或基准测试可能计费，必须明确确认")
        return self


class ModelProbeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    connection_id: str
    model_id: str | None
    probe_type: str
    status: str
    latency_ms: int | None
    metrics: dict[str, Any]
    error_code: str | None
    message: str
    created_at: datetime


class BalanceRead(BaseModel):
    configured: bool
    value: str | float | None
    usage: str | float | None = None
    currency: str | None = None
    message: str


class RoutingPolicyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str | None
    task_kind: str
    mode: str
    required_operations: list[str]
    weights: dict[str, int]
    fallback_config: dict[str, Any]
    enabled: bool
    version: int


class RoutingPolicyWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str | None = None
    task_kind: str = Field(min_length=1, max_length=48)
    mode: Literal["AUTO", "EXPLICIT"] = "AUTO"
    required_operations: list[str] = Field(default_factory=list)
    weights: dict[str, int] = Field(
        default_factory=lambda: {
            "reliability": 45,
            "priority": 25,
            "latency": 20,
            "cost": 10,
        }
    )
    fallback_config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    version: int | None = None

    @model_validator(mode="after")
    def validate_weights(self):
        expected = {"reliability", "priority", "latency", "cost"}
        if set(self.weights) != expected or sum(self.weights.values()) != 100:
            raise ValueError("路由权重必须包含四项且合计为 100")
        if any(value < 0 or value > 100 for value in self.weights.values()):
            raise ValueError("路由权重必须位于 0 到 100")
        return self
