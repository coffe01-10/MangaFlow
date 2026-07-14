from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel


@dataclass(frozen=True)
class StructuredRequest:
    prompt: str
    system_instruction: str | None = None
    temperature: float = 0.2
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


class ImageModelAdapter(Protocol):
    def generate_page(self, request: ImageRequest) -> ModelResponse: ...
    def generate_asset(self, request: ImageRequest) -> ModelResponse: ...
    def edit_region(self, request: ImageRequest) -> ModelResponse: ...
    def capabilities(self) -> dict[str, Any]: ...
