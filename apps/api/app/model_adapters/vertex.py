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

    def _client(self):
        from google import genai
        from google.oauth2 import service_account

        credentials = service_account.Credentials.from_service_account_file(
            str(self.settings.google_application_credentials),
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )

        return genai.Client(
            vertexai=True,
            project=self.settings.google_cloud_project,
            location=self.settings.google_cloud_location,
            credentials=credentials,
        )

    @staticmethod
    def _translate_error(error: Exception) -> VertexAdapterError:
        message = str(error).lower()
        if "credential" in message or "unauth" in message:
            return VertexAdapterError("AUTHENTICATION", "Vertex AI 凭据无效或已过期")
        if "permission" in message or "403" in message:
            return VertexAdapterError("PERMISSION", "服务账号没有调用该 Vertex 模型的权限")
        if "quota" in message or "resource_exhausted" in message:
            return VertexAdapterError("QUOTA", "Vertex AI 配额不足")
        if "429" in message or "rate" in message:
            return VertexAdapterError("RATE_LIMIT", "Vertex AI 请求过于频繁，请稍后重试")
        if "timeout" in message or "deadline" in message:
            return VertexAdapterError("TIMEOUT", "Vertex AI 请求超时")
        return VertexAdapterError("UPSTREAM", "Vertex AI 暂时无法完成请求")


class VertexTextAdapter(_VertexBase):
    def generate_structured(
        self, request: StructuredRequest, output_schema: type[BaseModel]
    ) -> BaseModel:
        from google.genai import types

        client = None
        try:
            client = self._client()
            response = client.models.generate_content(
                model=self.capability.model_id,
                contents=request.prompt,
                config=types.GenerateContentConfig(
                    system_instruction=request.system_instruction,
                    temperature=request.temperature,
                    max_output_tokens=request.metadata.get("max_output_tokens"),
                    response_mime_type="application/json",
                    response_schema=output_schema,
                ),
            )
            if not response.text:
                raise VertexAdapterError("INVALID_OUTPUT", "模型没有返回可验证的结构化结果")
            return output_schema.model_validate(json.loads(response.text))
        except VertexAdapterError:
            raise
        except Exception as error:
            raise self._translate_error(error) from error
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()

    def analyze_multimodal(
        self, request: MultimodalRequest, output_schema: type[BaseModel]
    ) -> BaseModel:
        from google.genai import types

        if len(request.images) != len(request.mime_types):
            raise VertexAdapterError("INVALID_INPUT", "图片与 MIME 类型数量不一致")
        contents: list[Any] = [request.prompt]
        for data, mime_type in zip(request.images, request.mime_types, strict=True):
            contents.append(types.Part.from_bytes(data=data, mime_type=mime_type))
        client = None
        try:
            client = self._client()
            response = client.models.generate_content(
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
            if not response.text:
                raise VertexAdapterError("INVALID_OUTPUT", "模型没有返回检查结果")
            return output_schema.model_validate(json.loads(response.text))
        except VertexAdapterError:
            raise
        except Exception as error:
            raise self._translate_error(error) from error
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()


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

        client = None
        try:
            client = self._client()
            response = client.models.generate_content(
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
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()
