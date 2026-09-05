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


class ProviderAdapterError(RuntimeError):
    def __init__(
        self,
        code: str,
        user_message: str,
        *,
        retryable: bool = False,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(user_message)
        self.code = code
        self.user_message = user_message
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


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
