from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel


def strip_json_fences(text: str) -> str:
    """Strip a single Markdown code fence a model may wrap around JSON."""
    stripped = text.strip()
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        if first_newline != -1 and stripped.endswith("```"):
            return stripped[first_newline + 1 : -3].strip()
    return stripped


# finish_reason values the SDK reports on otherwise-successful empty responses
# when safety systems blocked the content (docs/v02-cli-executor-contract.md §7).
BLOCKED_FINISH_REASONS = frozenset(
    {
        "SAFETY",
        "RECITATION",
        "BLOCKLIST",
        "PROHIBITED_CONTENT",
        "SPII",
    }
)


def blocked_finish_reason(response) -> str | None:
    """Read safety refusals the google-genai SDK surfaces as empty 200s.

    Shared by the Vertex and Gemini-native adapters: a blocked response has no
    ``text``/``content`` parts, so without this check it degrades to a generic
    INVALID_OUTPUT (and in the image path an AttributeError) instead of the
    CONTENT_POLICY classification the split-retry flows depend on.
    """

    feedback = getattr(response, "prompt_feedback", None)
    block = getattr(feedback, "block_reason", None) if feedback else None
    if block:
        return str(getattr(block, "name", block))
    for candidate in getattr(response, "candidates", None) or []:
        reason = getattr(candidate, "finish_reason", None)
        if reason is None:
            continue
        name = str(getattr(reason, "name", reason))
        if name in BLOCKED_FINISH_REASONS:
            return name
    return None


def response_usage(response) -> dict[str, Any] | None:
    """Best-effort google-genai usage_metadata extraction for text results."""

    metadata = getattr(response, "usage_metadata", None)
    if metadata is None:
        return None
    try:
        dumped = metadata.model_dump(exclude_none=True)
    except Exception:
        return None
    return dumped if isinstance(dumped, dict) and dumped else None


def attach_provider_usage(result: BaseModel, usage: dict[str, Any] | None) -> None:
    """Transport provider usage on a structured text result (issue #204).

    ``TextModelAdapter.generate_structured``/``analyze_multimodal`` return a
    bare pydantic model — the public return shape callers depend on — so the
    adapter-private ``provider_usage`` attribute is the only sanctioned
    channel: adapters call this helper right before returning, and the worker
    layer reads it best-effort via ``getattr(result, "provider_usage", None)``
    (falling back to ``usage`` for ``ModelResponse`` image results). Pydantic
    v2 instances tolerate ``object.__setattr__``: the value lands in the
    instance dict, is ignored by equality/serialization, and stays a clean
    ``None`` via ``getattr`` when the adapter never attached one. Empty or
    non-dict payloads are dropped so "no usage reported" remains NULL.
    """

    if not isinstance(usage, dict) or not usage:
        return
    object.__setattr__(result, "provider_usage", dict(usage))


class ProviderAdapterError(RuntimeError):
    def __init__(
        self,
        code: str,
        user_message: str,
        *,
        retryable: bool = False,
        retry_after_seconds: int | None = None,
        usage: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(user_message)
        self.code = code
        self.user_message = user_message
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds
        # Usage already billed by a provider POST that later failed downstream
        # (e.g. an image URL download timeout): the FAILED audit finalize reads
        # it so the spent tokens/images are not silently discarded (issue #207).
        self.usage = usage


@dataclass(frozen=True)
class StructuredRequest:
    prompt: str
    system_instruction: str | None = None
    temperature: float = 0.2
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MultimodalRequest:
    prompt: str
    images: tuple[bytes, ...]
    mime_types: tuple[str, ...]
    system_instruction: str | None = None
    temperature: float = 0.1
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ImageRequest:
    prompt: str
    resolution: str = "1K"
    aspect_ratio: str = "3:4"
    reference_images: tuple[bytes, ...] = ()
    reference_mime_types: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelResponse:
    model_id: str
    request_id: str | None
    usage: dict[str, Any]
    text: str | None = None
    images: tuple[bytes, ...] = ()


class TextModelAdapter(Protocol):
    """Structured text adapter contract.

    Implementations return the validated pydantic model directly. To surface
    provider billing facts without changing that public return shape, text
    adapters attach the provider usage dict through
    ``attach_provider_usage(result, usage)``; consumers read it best-effort
    with ``getattr(result, "provider_usage", None)`` and must treat a missing
    attribute as "no usage reported".
    """

    def generate_structured(
        self, request: StructuredRequest, output_schema: type[BaseModel]
    ) -> BaseModel: ...

    def analyze_multimodal(
        self, request: MultimodalRequest, output_schema: type[BaseModel]
    ) -> BaseModel: ...


class ImageModelAdapter(Protocol):
    def generate_page(self, request: ImageRequest) -> ModelResponse: ...
    def generate_asset(self, request: ImageRequest) -> ModelResponse: ...
    def edit_region(self, request: ImageRequest) -> ModelResponse: ...
    def capabilities(self) -> dict[str, Any]: ...
