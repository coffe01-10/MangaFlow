from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from app.config import get_settings
from app.model_adapters.base import (
    ImageRequest,
    ModelResponse,
    MultimodalRequest,
    ProviderAdapterError,
    StructuredRequest,
)
from app.services.vertex_credentials import classify_vertex_failure


@dataclass(frozen=True)
class GoogleRuntime:
    api_key: str
    model_id: str
    display_name: str
    capabilities: dict[str, Any] = field(default_factory=dict)


def genai_http_options() -> dict[str, int]:
    """Per-request HTTP timeout (milliseconds) for every genai.Client built here.

    The LOCAL wall-clock cap is enforced between provider calls by the lease
    heartbeat, which cannot interrupt a thread blocked inside a single
    timeout-less HTTP request. Bounding the transport at the job budget keeps
    one generate_content from outliving the attempt cap.
    """

    return {"timeout": get_settings().job_timeout_seconds * 1000}


class _GoogleBase:
    def __init__(self, runtime: GoogleRuntime) -> None:
        self.runtime = runtime

    def _client(self):
        from google import genai

        return genai.Client(
            api_key=self.runtime.api_key, http_options=genai_http_options()
        )

    @staticmethod
    def _translate(error: Exception) -> ProviderAdapterError:
        failure = classify_vertex_failure(error)
        return ProviderAdapterError(
            failure.code, failure.message, retryable=failure.retryable
        )

    def _execute(self, operation):
        client = self._client()
        try:
            return operation(client)
        except ProviderAdapterError:
            raise
        except Exception as error:
            raise self._translate(error) from error
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()


class GoogleTextAdapter(_GoogleBase):
    def generate_structured(
        self, request: StructuredRequest, output_schema: type[BaseModel]
    ) -> BaseModel:
        from google.genai import types

        response = self._execute(
            lambda client: client.models.generate_content(
                model=self.runtime.model_id,
                contents=request.prompt,
                config=types.GenerateContentConfig(
                    system_instruction=request.system_instruction,
                    temperature=request.temperature,
                    max_output_tokens=request.metadata.get("max_output_tokens"),
                    response_mime_type="application/json",
                    response_schema=output_schema,
                ),
            )
        )
        try:
            text = response.text
        except Exception as error:
            raise ProviderAdapterError(
                "INVALID_OUTPUT", "Gemini API 返回结构无法解析", retryable=True
            ) from error
        if not text:
            raise ProviderAdapterError("INVALID_OUTPUT", "Gemini API 没有返回文本")
        try:
            return output_schema.model_validate(json.loads(text))
        except Exception as error:
            raise ProviderAdapterError(
                "INVALID_OUTPUT", "Gemini API 返回结构无法验证"
            ) from error

    def analyze_multimodal(
        self, request: MultimodalRequest, output_schema: type[BaseModel]
    ) -> BaseModel:
        from google.genai import types

        if len(request.images) != len(request.mime_types):
            raise ProviderAdapterError("INVALID_INPUT", "图片与 MIME 类型数量不一致")
        contents: list[Any] = [request.prompt]
        for data, mime_type in zip(request.images, request.mime_types, strict=True):
            contents.append(types.Part.from_bytes(data=data, mime_type=mime_type))
        response = self._execute(
            lambda client: client.models.generate_content(
                model=self.runtime.model_id,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=request.system_instruction,
                    temperature=request.temperature,
                    max_output_tokens=request.metadata.get("max_output_tokens"),
                    response_mime_type="application/json",
                    response_schema=output_schema,
                ),
            )
        )
        try:
            text = response.text
        except Exception as error:
            raise ProviderAdapterError(
                "INVALID_OUTPUT", "Gemini API 返回结构无法解析", retryable=True
            ) from error
        if not text:
            raise ProviderAdapterError("INVALID_OUTPUT", "Gemini API 没有返回分析结果")
        try:
            return output_schema.model_validate(json.loads(text))
        except Exception as error:
            raise ProviderAdapterError(
                "INVALID_OUTPUT", "Gemini API 返回结构无法验证"
            ) from error


class GoogleImageAdapter(_GoogleBase):
    def capabilities(self) -> dict[str, Any]:
        return dict(self.runtime.capabilities)

    def generate_page(self, request: ImageRequest) -> ModelResponse:
        return self._generate(request)

    def generate_asset(self, request: ImageRequest) -> ModelResponse:
        return self._generate(request)

    def edit_region(self, request: ImageRequest) -> ModelResponse:
        if not request.reference_images:
            raise ProviderAdapterError("INVALID_INPUT", "图片编辑至少需要一张参考图")
        return self._generate(request)

    def _generate(self, request: ImageRequest) -> ModelResponse:
        from google.genai import types

        resolutions = self.runtime.capabilities.get("resolutions") or ["1K"]
        max_references = int(self.runtime.capabilities.get("max_reference_images") or 0)
        if request.resolution not in resolutions:
            raise ProviderAdapterError(
                "UNSUPPORTED_CAPABILITY",
                f"{self.runtime.display_name} 不支持 {request.resolution} 输出",
            )
        if len(request.reference_images) > max_references:
            raise ProviderAdapterError("INVALID_INPUT", "参考图数量超过当前模型上限")
        if len(request.reference_images) != len(request.reference_mime_types):
            raise ProviderAdapterError("INVALID_INPUT", "参考图和 MIME 类型数量不一致")
        contents: list[Any] = [request.prompt]
        for data, mime_type in zip(
            request.reference_images, request.reference_mime_types, strict=True
        ):
            contents.append(types.Part.from_bytes(data=data, mime_type=mime_type))
        response = self._execute(
            lambda client: client.models.generate_content(
                model=self.runtime.model_id,
                contents=contents,
                config=types.GenerateContentConfig(
                    # One candidate: the product persists only the first
                    # image, so extra candidates would only inflate the bill.
                    candidate_count=1,
                    response_modalities=[types.Modality.TEXT, types.Modality.IMAGE],
                    image_config=types.ImageConfig(
                        aspect_ratio=request.aspect_ratio,
                        image_size=request.resolution,
                    ),
                ),
            )
        )
        try:
            images: list[bytes] = []
            texts: list[str] = []
            for candidate in response.candidates or []:
                for part in candidate.content.parts or []:
                    if part.inline_data and part.inline_data.data:
                        images.append(part.inline_data.data)
                    elif part.text:
                        texts.append(part.text)
            usage = (
                response.usage_metadata.model_dump(exclude_none=True)
                if response.usage_metadata
                else {}
            )
        except ProviderAdapterError:
            raise
        except Exception as error:
            raise ProviderAdapterError(
                "INVALID_OUTPUT", "Gemini API 图像响应结构无法解析", retryable=True
            ) from error
        if not images:
            raise ProviderAdapterError("INVALID_OUTPUT", "Gemini API 未返回图像")
        return ModelResponse(
            model_id=self.runtime.model_id,
            request_id=getattr(response, "response_id", None),
            usage=usage,
            text="\n".join(texts) or None,
            images=tuple(images),
        )
