from __future__ import annotations

import json
from dataclasses import dataclass

from fastapi import HTTPException, Request
from starlette.datastructures import UploadFile
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import get_settings

UPLOAD_PATH_SUFFIXES = ("/assets/upload", "/sources/upload")
MAX_UPLOAD_FILES = 1
MAX_UPLOAD_FIELDS = 8


class BodyLimitExceeded(Exception):
    """Raised when the ASGI receive stream exceeds the upload budget."""


def is_upload_path(path: str) -> bool:
    return path.endswith(UPLOAD_PATH_SUFFIXES)


def max_request_bytes() -> int:
    settings = get_settings()
    return settings.max_upload_bytes + settings.upload_form_overhead_bytes


@dataclass
class ParsedUpload:
    texts: dict[str, str]
    file: UploadFile


class RequestBodyLimitMiddleware:
    """Count the real request stream before multipart is fully spooled."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        method = scope.get("method", "")
        if method not in {"POST", "PUT", "PATCH"} or not is_upload_path(path):
            await self.app(scope, receive, send)
            return

        limit = max_request_bytes()
        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        declared = headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            await _drain(receive)
            await _send_json(send, 413, "请求体超过上传上限")
            return

        received = 0
        overflow = False

        async def limited_receive() -> Message:
            nonlocal received, overflow
            if overflow:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    overflow = True
                    raise BodyLimitExceeded()
            return message

        try:
            await self.app(scope, limited_receive, send)
        except BodyLimitExceeded:
            await _send_json(send, 413, "请求体超过上传上限")


async def parse_single_file_form(
    request: Request,
    *,
    required_fields: tuple[str, ...],
    optional_fields: tuple[str, ...] = (),
) -> ParsedUpload:
    settings = get_settings()
    allowed = {"file", *required_fields, *optional_fields}
    try:
        form = await request.form(
            max_files=MAX_UPLOAD_FILES,
            max_fields=MAX_UPLOAD_FIELDS,
            max_part_size=settings.max_upload_bytes,
        )
    except HTTPException as error:
        detail = str(error.detail)
        if "exceeded maximum size" in detail.lower():
            raise HTTPException(status_code=413, detail="请求体超过上传上限") from error
        if "too many files" in detail.lower():
            raise HTTPException(status_code=422, detail="只接受一个文件字段") from error
        if "too many fields" in detail.lower():
            raise HTTPException(status_code=422, detail="表单字段数量超过上限") from error
        raise

    texts: dict[str, str] = {}
    files: list[tuple[str, UploadFile]] = []
    seen: set[str] = set()
    for key, value in form.multi_items():
        if key in seen:
            raise HTTPException(status_code=422, detail="表单字段不能重复")
        seen.add(key)
        if isinstance(value, UploadFile):
            files.append((key, value))
        else:
            texts[key] = str(value)
    unexpected = (set(texts) | {name for name, _ in files}) - allowed
    if unexpected:
        raise HTTPException(status_code=422, detail="包含未允许的表单字段")
    if len(files) != 1 or files[0][0] != "file":
        raise HTTPException(status_code=422, detail="请使用单个 file 字段上传")
    missing = [name for name in required_fields if not texts.get(name)]
    if missing:
        raise HTTPException(status_code=422, detail="缺少必要的表单字段")
    return ParsedUpload(texts=texts, file=files[0][1])


async def _drain(receive: Receive) -> None:
    while True:
        message = await receive()
        if message["type"] != "http.request" or not message.get("more_body"):
            return


async def _send_json(send: Send, status_code: int, detail: str) -> None:
    body = json.dumps({"detail": detail}, ensure_ascii=False).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status_code,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
