from __future__ import annotations

import base64
import ipaddress
import json
import socket
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse

import httpcore
import httpx
from httpcore._backends.sync import SyncBackend
from pydantic import BaseModel

from app.http_bounds import read_bounded_http_body
from app.model_adapters.base import (
    ImageRequest,
    ModelResponse,
    MultimodalRequest,
    ProviderAdapterError,
    StructuredRequest,
)

_RESERVED_HEADERS = {"authorization", "host", "content-length", "x-api-key"}
_RESERVED_BODY = {"model", "messages", "input", "prompt", "stream", "image", "images"}


@dataclass(frozen=True)
class CompatibleRuntime:
    provider_name: str
    protocol: str
    base_url: str
    api_key: str
    model_id: str
    endpoint_templates: dict[str, str]
    extra_headers: dict[str, str] = field(default_factory=dict)
    use_responses_api: bool = False
    capabilities: dict[str, Any] = field(default_factory=dict)
    allow_private_networks: bool = False
    allow_http_loopback: bool = False
    proxy_url: str | None = None
    max_response_bytes: int = 20 * 1024 * 1024

    def endpoint(self, name: str) -> str:
        path = self.endpoint_templates.get(name)
        if not path:
            raise ProviderAdapterError(
                "UNSUPPORTED_CAPABILITY", f"当前连接未配置 {name} 端点"
            )
        return urljoin(f"{self.base_url.rstrip('/')}/", path.lstrip("/"))


def validate_provider_url(
    url: str,
    *,
    allow_private: bool = False,
    allow_http_loopback: bool = False,
    allow_query: bool = False,
) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("供应商地址必须是有效的 HTTP(S) URL")
    if (
        parsed.username
        or parsed.password
        or (parsed.query and not allow_query)
        or parsed.fragment
    ):
        raise ValueError("供应商地址不能包含凭据、查询参数或片段")
    if parsed.scheme != "https" and not allow_http_loopback:
        raise ValueError("供应商地址必须使用 HTTPS")
    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(
                parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM
            )
        }
    except socket.gaierror as error:
        raise ValueError("供应商地址无法解析") from error
    for address in addresses:
        ip = ipaddress.ip_address(address)
        dangerous = ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified
        private = ip.is_private
        if dangerous and not (allow_http_loopback and ip.is_loopback):
            raise ValueError("供应商地址指向受保护的本机或元数据网络")
        if private and not allow_private and not (allow_http_loopback and ip.is_loopback):
            raise ValueError("供应商地址指向私有网络，当前未显式允许")
    return sorted(addresses)[0]


class _PinnedNetworkBackend(SyncBackend):
    """Keep the validated DNS answer stable until the TCP connection is made."""

    def __init__(self, hostname: str, address: str) -> None:
        self.hostname = hostname.casefold()
        self.address = address

    def connect_tcp(self, host: str, *args, **kwargs):
        target = self.address if host.casefold() == self.hostname else host
        return super().connect_tcp(target, *args, **kwargs)


class _PinnedHTTPTransport(httpx.HTTPTransport):
    def __init__(self, hostname: str, address: str) -> None:
        # Provider requests deliberately bypass process-wide proxies: otherwise the
        # proxy would resolve the hostname again and reintroduce DNS rebinding.
        super().__init__(trust_env=False)
        self._pool.close()
        self._pool = httpcore.ConnectionPool(
            network_backend=_PinnedNetworkBackend(hostname, address)
        )


def _validated_proxy_url(value: str) -> str:
    parsed = urlparse(value)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("MANGAFLOW_PROXY_URL 必须是无凭据的本机 HTTP(S) 代理地址")
    return value


def provider_http_client(
    url: str,
    *,
    timeout: httpx.Timeout,
    allow_private: bool = False,
    allow_http_loopback: bool = False,
    proxy_url: str | None = None,
) -> httpx.Client:
    parsed = urlparse(url)
    if proxy_url:
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise ValueError("代理目标必须是无凭据的 HTTPS URL")
        transport = httpx.HTTPTransport(
            proxy=_validated_proxy_url(proxy_url),
            trust_env=False,
        )
    else:
        resolved_address = validate_provider_url(
            url,
            allow_private=allow_private,
            allow_http_loopback=allow_http_loopback,
            allow_query=True,
        )
        transport = _PinnedHTTPTransport(parsed.hostname or "", resolved_address)
    return httpx.Client(timeout=timeout, transport=transport, trust_env=False)


def _safe_headers(runtime: CompatibleRuntime) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    for key, value in runtime.extra_headers.items():
        if key.lower() in _RESERVED_HEADERS:
            continue
        headers[key] = str(value)
    if runtime.protocol == "ANTHROPIC":
        headers["x-api-key"] = runtime.api_key
        headers.setdefault("anthropic-version", "2023-06-01")
    else:
        headers["Authorization"] = f"Bearer {runtime.api_key}"
    return headers


def _safe_extra_body(runtime: CompatibleRuntime) -> dict[str, Any]:
    configured = runtime.capabilities.get("extra_body") or {}
    if not isinstance(configured, dict):
        return {}
    return {key: value for key, value in configured.items() if key not in _RESERVED_BODY}


def _supported_parameters(runtime: CompatibleRuntime) -> set[str] | None:
    configured = runtime.capabilities.get("supported_parameters")
    if not isinstance(configured, (list, tuple, set)) or not configured:
        return None
    return {str(item).strip().casefold() for item in configured if str(item).strip()}


def _supports(runtime: CompatibleRuntime, *names: str) -> bool:
    supported = _supported_parameters(runtime)
    return supported is None or bool(supported.intersection(name.casefold() for name in names))


def _schema_prompt(prompt: str, output_schema: type[BaseModel]) -> str:
    schema = json.dumps(
        output_schema.model_json_schema(),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return (
        f"{prompt}\n只输出一个符合以下 JSON Schema 的 JSON 对象，"
        f"不要使用 Markdown 代码块：{schema}"
    )


def _provider_error(response: httpx.Response) -> ProviderAdapterError:
    status = response.status_code
    retry_after = response.headers.get("retry-after")
    retry_after_seconds = int(retry_after) if retry_after and retry_after.isdigit() else None
    if status == 401:
        return ProviderAdapterError("AUTHENTICATION", "供应商 API Key 无效", retryable=False)
    if status == 403:
        return ProviderAdapterError("PERMISSION", "供应商拒绝访问该模型", retryable=False)
    if status == 404:
        return ProviderAdapterError("MODEL_NOT_FOUND", "供应商模型或端点不存在")
    if status == 429:
        return ProviderAdapterError(
            "RATE_LIMIT",
            "供应商请求频率或额度已达限制",
            retryable=True,
            retry_after_seconds=retry_after_seconds,
        )
    if 500 <= status < 600:
        return ProviderAdapterError("UPSTREAM", "供应商上游服务暂时不可用", retryable=True)
    return ProviderAdapterError("INVALID_INPUT", "供应商拒绝了当前请求")


class _CompatibleBase:
    def __init__(
        self,
        runtime: CompatibleRuntime,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.runtime = runtime
        self._injected_client = client

    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        if self._injected_client is None:
            base = urlparse(self.runtime.base_url)
            target = urlparse(url)
            same_origin = (
                base.scheme,
                base.hostname,
                base.port,
            ) == (
                target.scheme,
                target.hostname,
                target.port,
            )
            try:
                client = provider_http_client(
                    url,
                    timeout=httpx.Timeout(90.0, connect=10.0),
                    allow_private=self.runtime.allow_private_networks,
                    allow_http_loopback=self.runtime.allow_http_loopback,
                    proxy_url=self.runtime.proxy_url if same_origin else None,
                )
            except ValueError as error:
                raise ProviderAdapterError(
                    "INVALID_INPUT", "供应商请求地址指向了不允许的网络"
                ) from error
        else:
            client = self._injected_client
        try:
            try:
                request = client.build_request(method, url, **kwargs)
                response = client.send(request, stream=True, follow_redirects=False)
                try:
                    if 300 <= response.status_code < 400:
                        raise ProviderAdapterError("UPSTREAM", "供应商返回了未允许的重定向")
                    if response.is_error:
                        raise _provider_error(response)
                    content = read_bounded_http_body(
                        response, self.runtime.max_response_bytes
                    )
                    return httpx.Response(
                        response.status_code,
                        headers=response.headers,
                        content=content,
                        request=request,
                        extensions=response.extensions,
                    )
                finally:
                    response.close()
            except httpx.TimeoutException as error:
                raise ProviderAdapterError(
                    "TIMEOUT", "供应商请求超时", retryable=True
                ) from error
            except httpx.HTTPError as error:
                raise ProviderAdapterError(
                    "UPSTREAM", "无法连接供应商服务", retryable=True
                ) from error
        finally:
            if self._injected_client is None:
                client.close()


class OpenAICompatibleAdapter(_CompatibleBase):
    def capabilities(self) -> dict[str, Any]:
        return dict(self.runtime.capabilities)

    def generate_structured(
        self, request: StructuredRequest, output_schema: type[BaseModel]
    ) -> BaseModel:
        supports_schema = _supports(
            self.runtime,
            "response_format",
            "json_schema",
            "structured_outputs",
        )
        prompt = (
            request.prompt
            if supports_schema
            else _schema_prompt(request.prompt, output_schema)
        )
        if self.runtime.use_responses_api:
            payload: dict[str, Any] = {
                "model": self.runtime.model_id,
                "input": prompt,
            }
            if _supports(self.runtime, "temperature"):
                payload["temperature"] = request.temperature
            if supports_schema:
                payload["text"] = {
                    "format": {
                        "type": "json_schema",
                        "name": output_schema.__name__.lower(),
                        "schema": output_schema.model_json_schema(),
                        "strict": True,
                    }
                }
            if request.metadata.get("max_output_tokens") and _supports(
                self.runtime, "max_output_tokens"
            ):
                payload["max_output_tokens"] = request.metadata["max_output_tokens"]
            if request.system_instruction:
                payload["instructions"] = request.system_instruction
            payload.update(_safe_extra_body(self.runtime))
            response = self._request(
                "POST",
                self.runtime.endpoint("responses"),
                headers=_safe_headers(self.runtime),
                json=payload,
            )
            body = response.json()
            text = body.get("output_text") or self._responses_text(body)
        else:
            messages = []
            if request.system_instruction:
                messages.append({"role": "system", "content": request.system_instruction})
            messages.append({"role": "user", "content": prompt})
            payload = {
                "model": self.runtime.model_id,
                "messages": messages,
            }
            if _supports(self.runtime, "temperature"):
                payload["temperature"] = request.temperature
            if supports_schema:
                payload["response_format"] = self._response_format(output_schema)
            if request.metadata.get("max_output_tokens") and _supports(
                self.runtime, "max_tokens", "max_output_tokens"
            ):
                payload["max_tokens"] = request.metadata["max_output_tokens"]
            payload.update(_safe_extra_body(self.runtime))
            response = self._request(
                "POST",
                self.runtime.endpoint("chat"),
                headers=_safe_headers(self.runtime),
                json=payload,
            )
            body = response.json()
            text = self._chat_text(body)
        try:
            return output_schema.model_validate_json(text)
        except Exception as error:
            raise ProviderAdapterError(
                "INVALID_OUTPUT", "模型已响应，但结构化结果无法验证"
            ) from error

    def analyze_multimodal(
        self, request: MultimodalRequest, output_schema: type[BaseModel]
    ) -> BaseModel:
        if len(request.images) != len(request.mime_types):
            raise ProviderAdapterError("INVALID_INPUT", "图片与 MIME 类型数量不一致")
        supports_schema = _supports(
            self.runtime,
            "response_format",
            "json_schema",
            "structured_outputs",
        )
        prompt = (
            request.prompt
            if supports_schema
            else _schema_prompt(request.prompt, output_schema)
        )
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for data, mime_type in zip(request.images, request.mime_types, strict=True):
            encoded = base64.b64encode(data).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
                }
            )
        messages = []
        if request.system_instruction:
            messages.append({"role": "system", "content": request.system_instruction})
        messages.append({"role": "user", "content": content})
        payload = {
            "model": self.runtime.model_id,
            "messages": messages,
            **_safe_extra_body(self.runtime),
        }
        if _supports(self.runtime, "temperature"):
            payload["temperature"] = request.temperature
        if supports_schema:
            payload["response_format"] = self._response_format(output_schema)
        if request.metadata.get("max_output_tokens") and _supports(
            self.runtime, "max_tokens", "max_output_tokens"
        ):
            payload["max_tokens"] = request.metadata["max_output_tokens"]
        response = self._request(
            "POST",
            self.runtime.endpoint("chat"),
            headers=_safe_headers(self.runtime),
            json=payload,
        )
        try:
            return output_schema.model_validate_json(self._chat_text(response.json()))
        except Exception as error:
            raise ProviderAdapterError(
                "INVALID_OUTPUT", "模型已响应，但多模态结果无法验证"
            ) from error

    def generate_page(self, request: ImageRequest) -> ModelResponse:
        return self._generate_image(request)

    def generate_asset(self, request: ImageRequest) -> ModelResponse:
        return self._generate_image(request)

    def edit_region(self, request: ImageRequest) -> ModelResponse:
        """Whole-image reference edit (V02-44A matrix §1.1).

        The ``images_edit`` request body only carries model/prompt/size plus
        the reference files — there is no mask field and no instruction-only
        region surface, so region jobs are gated by the catalog capability
        bits (``services/model_capabilities.py``) and must never be routed
        here as a silent local-edit degrade.
        """

        if not request.reference_images:
            raise ProviderAdapterError("INVALID_INPUT", "图片编辑至少需要一张参考图")
        return self._generate_image(request)

    def _generate_image(self, request: ImageRequest) -> ModelResponse:
        headers = _safe_headers(self.runtime)
        if request.reference_images:
            headers.pop("Content-Type", None)
            files = []
            for index, (data, mime_type) in enumerate(
                zip(request.reference_images, request.reference_mime_types, strict=True)
            ):
                extension = mime_type.split("/")[-1].replace("jpeg", "jpg")
                files.append(("image[]", (f"reference-{index}.{extension}", data, mime_type)))
            response = self._request(
                "POST",
                self.runtime.endpoint("images_edit"),
                headers=headers,
                data={
                    "model": self.runtime.model_id,
                    "prompt": request.prompt,
                    "size": self._image_size(request),
                },
                files=files,
            )
        else:
            payload = {
                "model": self.runtime.model_id,
                "prompt": request.prompt,
                "n": 1,
                "size": self._image_size(request),
                **_safe_extra_body(self.runtime),
            }
            response = self._request(
                "POST",
                self.runtime.endpoint("images_generate"),
                headers=headers,
                json=payload,
            )
        body = response.json()
        images = tuple(self._image_bytes(item) for item in body.get("data") or [])
        if not images:
            raise ProviderAdapterError("INVALID_OUTPUT", "图片模型没有返回可用图片")
        return ModelResponse(
            model_id=body.get("model") or self.runtime.model_id,
            request_id=body.get("id") or response.headers.get("x-request-id"),
            usage=body.get("usage") or {},
            images=images,
        )

    def _image_bytes(self, item: dict[str, Any]) -> bytes:
        encoded = item.get("b64_json") or item.get("b64")
        if encoded:
            return base64.b64decode(encoded)
        url = item.get("url")
        if not url or not str(url).startswith("https://"):
            raise ProviderAdapterError("INVALID_OUTPUT", "图片结果缺少安全的下载地址")
        try:
            validate_provider_url(str(url), allow_query=True)
        except ValueError as error:
            raise ProviderAdapterError(
                "INVALID_OUTPUT", "图片下载地址指向了不允许的网络"
            ) from error
        response = self._request("GET", str(url), headers={})
        if not response.headers.get("content-type", "").startswith("image/"):
            raise ProviderAdapterError("INVALID_OUTPUT", "图片下载地址返回了非图片内容")
        return response.content

    def _image_size(self, request: ImageRequest) -> str:
        configured = self.runtime.capabilities.get("size_map") or {}
        key = f"{request.resolution}:{request.aspect_ratio}"
        return configured.get(key) or configured.get(request.resolution) or "1024x1536"

    def _response_format(self, output_schema: type[BaseModel]) -> dict[str, Any]:
        mode = self.runtime.capabilities.get("structured_output_mode", "JSON_MODE")
        if mode == "STRICT_SCHEMA":
            return {
                "type": "json_schema",
                "json_schema": {
                    "name": output_schema.__name__.lower(),
                    "strict": True,
                    "schema": output_schema.model_json_schema(),
                },
            }
        return {"type": "json_object"}

    @staticmethod
    def _chat_text(body: dict[str, Any]) -> str:
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise ProviderAdapterError("INVALID_OUTPUT", "文本模型没有返回内容") from error
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(str(item.get("text") or "") for item in content)
        raise ProviderAdapterError("INVALID_OUTPUT", "文本模型返回格式不受支持")

    @staticmethod
    def _responses_text(body: dict[str, Any]) -> str:
        values: list[str] = []
        for output in body.get("output") or []:
            for item in output.get("content") or []:
                if item.get("type") in {"output_text", "text"}:
                    values.append(item.get("text") or "")
        if not values:
            raise ProviderAdapterError("INVALID_OUTPUT", "Responses API 没有返回文本")
        return "".join(values)


class AnthropicCompatibleAdapter(_CompatibleBase):
    def generate_structured(
        self, request: StructuredRequest, output_schema: type[BaseModel]
    ) -> BaseModel:
        prompt = self._schema_prompt(request.prompt, output_schema)
        payload: dict[str, Any] = {
            "model": self.runtime.model_id,
            "max_tokens": request.metadata.get("max_output_tokens", 2048),
            "temperature": request.temperature,
            "messages": [{"role": "user", "content": prompt}],
            **_safe_extra_body(self.runtime),
        }
        if request.system_instruction:
            payload["system"] = request.system_instruction
        if self.runtime.capabilities.get("structured_output_mode") == "STRICT_SCHEMA":
            payload["output_config"] = {
                "format": {
                    "type": "json_schema",
                    "schema": output_schema.model_json_schema(),
                }
            }
        response = self._request(
            "POST",
            self.runtime.endpoint("messages"),
            headers=_safe_headers(self.runtime),
            json=payload,
        )
        try:
            return output_schema.model_validate_json(self._text(response.json()))
        except Exception as error:
            raise ProviderAdapterError(
                "INVALID_OUTPUT", "模型已响应，但结构化结果无法验证"
            ) from error

    def analyze_multimodal(
        self, request: MultimodalRequest, output_schema: type[BaseModel]
    ) -> BaseModel:
        if len(request.images) != len(request.mime_types):
            raise ProviderAdapterError("INVALID_INPUT", "图片与 MIME 类型数量不一致")
        content: list[dict[str, Any]] = []
        for data, mime_type in zip(request.images, request.mime_types, strict=True):
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": mime_type,
                        "data": base64.b64encode(data).decode("ascii"),
                    },
                }
            )
        content.append(
            {"type": "text", "text": self._schema_prompt(request.prompt, output_schema)}
        )
        payload: dict[str, Any] = {
            "model": self.runtime.model_id,
            "max_tokens": request.metadata.get("max_output_tokens", 2048),
            "temperature": request.temperature,
            "messages": [{"role": "user", "content": content}],
            **_safe_extra_body(self.runtime),
        }
        if request.system_instruction:
            payload["system"] = request.system_instruction
        response = self._request(
            "POST",
            self.runtime.endpoint("messages"),
            headers=_safe_headers(self.runtime),
            json=payload,
        )
        try:
            return output_schema.model_validate_json(self._text(response.json()))
        except Exception as error:
            raise ProviderAdapterError(
                "INVALID_OUTPUT", "模型已响应，但多模态结果无法验证"
            ) from error

    @staticmethod
    def _text(body: dict[str, Any]) -> str:
        values = [item.get("text") or "" for item in body.get("content") or []]
        text = "".join(values)
        if not text:
            raise ProviderAdapterError("INVALID_OUTPUT", "Anthropic 协议没有返回文本")
        return text

    @staticmethod
    def _schema_prompt(prompt: str, output_schema: type[BaseModel]) -> str:
        schema = output_schema.model_json_schema()
        return (
            f"{prompt}\n只输出一个符合以下 JSON Schema 的 JSON 对象，不要使用 Markdown 代码块："
            f"{json.dumps(schema, ensure_ascii=False, separators=(',', ':'))}"
        )
