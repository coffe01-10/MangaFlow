from __future__ import annotations

from io import BytesIO
from typing import Any

from PIL import Image
from pydantic import BaseModel

from app.model_adapters.base import (
    ImageModelAdapter,
    ImageRequest,
    ModelResponse,
    MultimodalRequest,
    StructuredRequest,
    TextModelAdapter,
)


def _generate_fake_png_bytes(
    width: int = 64, height: int = 64, color: tuple[int, int, int] = (240, 240, 240)
) -> bytes:
    buf = BytesIO()
    img = Image.new("RGB", (width, height), color)
    img.save(buf, format="PNG")
    return buf.getvalue()


class FakeAcceptanceImageAdapter(ImageModelAdapter):
    """Deterministic fake image adapter for acceptance testing with 0 external API calls."""

    def __init__(self, model_id: str = "fake-acceptance-nano-banana-2") -> None:
        self.model_id = model_id
        self.call_count = 0

    def generate_page(self, request: ImageRequest) -> ModelResponse:
        self.call_count += 1
        return ModelResponse(
            model_id=self.model_id,
            request_id=f"fake-req-{self.call_count}",
            usage={"input_tokens": 10, "output_images": 1},
            images=(_generate_fake_png_bytes(),),
        )

    def generate_asset(self, request: ImageRequest) -> ModelResponse:
        self.call_count += 1
        return ModelResponse(
            model_id=self.model_id,
            request_id=f"fake-asset-req-{self.call_count}",
            usage={"input_tokens": 10, "output_images": 1},
            images=(_generate_fake_png_bytes(),),
        )

    def edit_region(self, request: ImageRequest) -> ModelResponse:
        self.call_count += 1
        return ModelResponse(
            model_id=self.model_id,
            request_id=f"fake-edit-req-{self.call_count}",
            usage={"input_tokens": 10, "output_images": 1},
            images=(_generate_fake_png_bytes(),),
        )

    def capabilities(self) -> dict[str, Any]:
        return {
            "resolutions": ["1K", "2K", "4K"],
            "aspect_ratios": ["3:4", "16:9", "1:1"],
        }


class FakeAcceptanceTextAdapter(TextModelAdapter):
    """Deterministic local fake text adapter for acceptance testing."""

    def __init__(self, model_id: str = "fake-acceptance-gemini-flash") -> None:
        self.model_id = model_id
        self.call_count = 0

    def generate_structured(
        self, request: StructuredRequest, output_schema: type[BaseModel]
    ) -> BaseModel:
        self.call_count += 1
        # Construct empty / mock instance conforming to schema
        if hasattr(output_schema, "mock_default"):
            return output_schema.mock_default()  # type: ignore
        return output_schema.model_validate({})

    def analyze_multimodal(
        self, request: MultimodalRequest, output_schema: type[BaseModel]
    ) -> BaseModel:
        self.call_count += 1
        return output_schema.model_validate({})