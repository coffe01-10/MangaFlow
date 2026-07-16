import json
from typing import Any

from pydantic import BaseModel

from app.config import Settings
from app.model_adapters.base import (
    ImageRequest,
    ModelResponse,
    MultimodalRequest,
    StructuredRequest,
)
from app.services.model_registry import ModelCapability
from app.services.vertex_credentials import (
    classify_vertex_failure,
    get_vertex_credential_manager,
)


class VertexAdapterError(RuntimeError):
    def __init__(self, code: str, user_message: str) -> None:
        super().__init__(user_message)
        self.code = code
        self.user_message = user_message


class _VertexBase:
    def __init__(self, settings: Settings, capability: ModelCapability) -> None:
        if not settings.vertex_configured:
            raise VertexAdapterError("AUTHENTICATION", "Vertex AI 服务账号尚未正确配置")
        self.settings = settings
        self.capability = capability
        self.credential_manager = get_vertex_credential_manager()

    def _client(self):
        return self.credential_manager.create_client(self.settings)

    def _execute(self, operation):
        return self.credential_manager.execute(
            self.settings,
            operation,
            client_factory=self._client,
        )

    @staticmethod
    def _translate_error(error: Exception) -> VertexAdapterError:
        failure = classify_vertex_failure(error)
        return VertexAdapterError(failure.code, failure.message)


class VertexTextAdapter(_VertexBase):
    def generate_structured(
        self, request: StructuredRequest, output_schema: type[BaseModel]
    ) -> BaseModel:
        from google.genai import types

        try:
            response = self._execute(
                lambda client: client.models.generate_content(
                    model=self.capability.model_id,
                    contents=request.prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=request.system_instruction,
                        temperature=request.temperature,
                        max_output_tokens=request.metadata.get("max_output_tokens"),
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
            if not response.text:
                raise VertexAdapterError("INVALID_OUTPUT", "模型没有返回可验证的结构化结果")
            return output_schema.model_validate(json.loads(response.text))
        except VertexAdapterError:
            raise
        except Exception as error:
            raise self._translate_error(error) from error

    def analyze_multimodal(
        self, request: MultimodalRequest, output_schema: type[BaseModel]
    ) -> BaseModel:
        from google.genai import types

        if len(request.images) != len(request.mime_types):
            raise VertexAdapterError("INVALID_INPUT", "图片与 MIME 类型数量不一致")
        contents: list[Any] = [request.prompt]
        for data, mime_type in zip(request.images, request.mime_types, strict=True):
            contents.append(types.Part.from_bytes(data=data, mime_type=mime_type))
        try:
            response = self._execute(
                lambda client: client.models.generate_content(
                    model=self.capability.model_id,
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
            if not response.text:
                raise VertexAdapterError("INVALID_OUTPUT", "模型没有返回检查结果")
            return output_schema.model_validate(json.loads(response.text))
        except VertexAdapterError:
            raise
        except Exception as error:
            raise self._translate_error(error) from error


class VertexImageAdapter(_VertexBase):
    def capabilities(self) -> dict[str, Any]:
        return self.capability.to_dict()

    def generate_page(self, request: ImageRequest) -> ModelResponse:
        return self._generate(request)

    def generate_asset(self, request: ImageRequest) -> ModelResponse:
        return self._generate(request)

    def edit_region(self, request: ImageRequest) -> ModelResponse:
        if not request.reference_images:
            raise VertexAdapterError("INVALID_INPUT", "局部编辑至少需要一张参考图")
        return self._generate(request)

    def _generate(self, request: ImageRequest) -> ModelResponse:
        from google.genai import types

        if request.resolution not in self.capability.resolutions:
            raise VertexAdapterError(
                "UNSUPPORTED_CAPABILITY",
                f"{self.capability.display_name} 不支持 {request.resolution} 输出",
            )
        if len(request.reference_images) > self.capability.max_reference_images:
            raise VertexAdapterError("INVALID_INPUT", "参考图数量超过当前模型上限")
        if len(request.reference_images) != len(request.reference_mime_types):
            raise VertexAdapterError("INVALID_INPUT", "参考图和 MIME 类型数量不一致")

        contents: list[Any] = [request.prompt]
        for data, mime_type in zip(
            request.reference_images, request.reference_mime_types, strict=True
        ):
            contents.append(types.Part.from_bytes(data=data, mime_type=mime_type))

        try:
            response = self._execute(
                lambda client: client.models.generate_content(
                    model=self.capability.model_id,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        response_modalities=[types.Modality.TEXT, types.Modality.IMAGE],
                        image_config=types.ImageConfig(
                            aspect_ratio=request.aspect_ratio,
                            image_size=request.resolution,
                        ),
                    ),
                )
            )
            images: list[bytes] = []
            texts: list[str] = []
            for candidate in response.candidates or []:
                for part in candidate.content.parts or []:
                    if part.inline_data and part.inline_data.data:
                        images.append(part.inline_data.data)
                    elif part.text:
                        texts.append(part.text)
            if not images:
                raise VertexAdapterError("INVALID_OUTPUT", "模型未返回图像")
            usage = (
                response.usage_metadata.model_dump(exclude_none=True)
                if response.usage_metadata
                else {}
            )
            return ModelResponse(
                model_id=self.capability.model_id,
                request_id=getattr(response, "response_id", None),
                usage=usage,
                text="\n".join(texts) or None,
                images=tuple(images),
            )
        except VertexAdapterError:
            raise
        except Exception as error:
            raise self._translate_error(error) from error
