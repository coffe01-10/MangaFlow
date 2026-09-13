from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from app.model_adapters.base import (
    ImageRequest,
    ModelResponse,
    MultimodalRequest,
    ProviderAdapterError,
    StructuredRequest,
    attach_provider_usage,
    blocked_finish_reason,
    response_usage,
    strip_json_fences,
)
from app.services.model_capabilities import capability_reference_limit
from app.services.vertex_credentials import classify_vertex_failure

_GOOGLE_HTTP_TIMEOUT_MS = 90_000


def _validate_structured_text(
    text: str, output_schema: type[BaseModel], *, failure_message: str
) -> BaseModel:
    try:
        payload = json.loads(strip_json_fences(text))
    except ValueError as error:
        raise ProviderAdapterError(
            "INVALID_OUTPUT", "Gemini API 返回内容不是有效 JSON", retryable=True
        ) from error
    try:
        return output_schema.model_validate(payload)
    except Exception as error:
        raise ProviderAdapterError("INVALID_OUTPUT", failure_message) from error


@dataclass(frozen=True)
class GoogleRuntime:
    api_key: str
    model_id: str
    display_name: str
    capabilities: dict[str, Any] = field(default_factory=dict)


class _GoogleBase:
    def __init__(self, runtime: GoogleRuntime) -> None:
        self.runtime = runtime

    def _client(self):
        from google import genai
        from google.genai import types

        # Bound connect/read like the HTTP-API path (90s); the SDK default
        # lets a hung upstream pin a worker slot for minutes. Retry attempts
        # are pinned to 1 so the dormant SDK retry can never silently multiply
        # paid dispatches inside the manager/worker's own bounded retry loop
        # (issue #209).
        return genai.Client(
            api_key=self.runtime.api_key,
            http_options=types.HttpOptions(
                timeout=_GOOGLE_HTTP_TIMEOUT_MS,
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )

    @staticmethod
    def _translate(error: Exception) -> ProviderAdapterError:
        failure = classify_vertex_failure(error)
        return ProviderAdapterError(
            failure.code, failure.message, retryable=failure.retryable
        )

    _blocked_reason = staticmethod(blocked_finish_reason)

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
                    # #644: mirror vertex.generate_structured — only set
                    # thinking_config when the caller declared a budget, so a
                    # cost-saving thinking_budget=0 is not silently dropped on
                    # Gemini 2.5's default dynamic thinking.
                    thinking_config=(
                        types.ThinkingConfig(
                            thinking_budget=request.metadata["thinking_budget"]
                        )
                        if "thinking_budget" in request.metadata
                        else None
                    ),
                    response_mime_type="application/json",
                    response_schema=output_schema,
                ),
            )
        )
        if self._blocked_reason(response):
            raise ProviderAdapterError(
                "CONTENT_POLICY",
                "请求被 Gemini API 内容安全策略拦截，系统已缩小生成片段；请重试",
            )
        try:
            text = response.text
        except Exception as error:
            raise ProviderAdapterError(
                "INVALID_OUTPUT", "Gemini API 返回结构无法解析", retryable=True
            ) from error
        if not text:
            raise ProviderAdapterError("INVALID_OUTPUT", "Gemini API 没有返回文本")
        result = _validate_structured_text(
            text, output_schema, failure_message="Gemini API 返回结构无法验证"
        )
        attach_provider_usage(result, response_usage(response))
        return result

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
                    # #644: mirror vertex.generate_structured — only set
                    # thinking_config when the caller declared a budget, so a
                    # cost-saving thinking_budget=0 is not silently dropped on
                    # Gemini 2.5's default dynamic thinking.
                    thinking_config=(
                        types.ThinkingConfig(
                            thinking_budget=request.metadata["thinking_budget"]
                        )
                        if "thinking_budget" in request.metadata
                        else None
                    ),
                    response_mime_type="application/json",
                    response_schema=output_schema,
                ),
            )
        )
        if self._blocked_reason(response):
            raise ProviderAdapterError(
                "CONTENT_POLICY",
                "请求被 Gemini API 内容安全策略拦截，系统已缩小生成片段；请重试",
            )
        try:
            text = response.text
        except Exception as error:
            raise ProviderAdapterError(
                "INVALID_OUTPUT", "Gemini API 返回结构无法解析", retryable=True
            ) from error
        if not text:
            raise ProviderAdapterError("INVALID_OUTPUT", "Gemini API 没有返回分析结果")
        result = _validate_structured_text(
            text, output_schema, failure_message="Gemini API 返回结构无法验证"
        )
        attach_provider_usage(result, response_usage(response))
        return result


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
        max_references = capability_reference_limit(self.runtime.capabilities) or 0
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
                # A blocked candidate has no content at all; read it defensively
                # so the blocked shape classifies below instead of raising
                # AttributeError into a retryable INVALID_OUTPUT (issue #206).
                parts = getattr(getattr(candidate, "content", None), "parts", None) or []
                for part in parts:
                    if part.inline_data and part.inline_data.data:
                        images.append(part.inline_data.data)
                    elif part.text:
                        texts.append(part.text)
            usage = response_usage(response) or {}
        except ProviderAdapterError:
            raise
        except Exception as error:
            raise ProviderAdapterError(
                "INVALID_OUTPUT", "Gemini API 图像响应结构无法解析", retryable=True
            ) from error
        if not images:
            if self._blocked_reason(response):
                raise ProviderAdapterError(
                    "CONTENT_POLICY",
                    "请求被 Gemini API 内容安全策略拦截，本次生成被拒绝",
                )
            raise ProviderAdapterError("INVALID_OUTPUT", "Gemini API 未返回图像")
        return ModelResponse(
            model_id=self.runtime.model_id,
            request_id=getattr(response, "response_id", None),
            usage=usage,
            text="\n".join(texts) or None,
            images=tuple(images),
        )
