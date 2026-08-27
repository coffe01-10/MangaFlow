from __future__ import annotations

import httpx

from app.model_adapters.base import ProviderAdapterError


def read_bounded_http_body(response: httpx.Response, max_bytes: int) -> bytes:
    """Read a streaming HTTP body, counting decompressed bytes.

    Content-Length is only a fast-fail hint. Actual size is measured after
    httpx has decoded the payload so a compressed bomb cannot bypass the cap.
    """

    declared = response.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max_bytes:
        raise ProviderAdapterError("INVALID_OUTPUT", "供应商响应超过允许的大小")
    content = bytearray()
    for chunk in response.iter_bytes():
        content.extend(chunk)
        if len(content) > max_bytes:
            raise ProviderAdapterError("INVALID_OUTPUT", "供应商响应超过允许的大小")
    return bytes(content)
