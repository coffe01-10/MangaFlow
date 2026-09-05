from __future__ import annotations

import json
import re
from dataclasses import dataclass

from fastapi import HTTPException, Request
from starlette.datastructures import UploadFile
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import get_settings

UPLOAD_PATH_SUFFIXES = ("/assets/upload", "/sources/upload")
# JSON endpoints whose bodies legitimately exceed the generic JSON budget:
# pasted sources (POST .../sources/import) and edited chapter sources
# (POST .../chapters/{id}/revisions) are capped semantically at 2M characters
# by SourceImportRequest/SourceRevisionCreate, which can exceed 2MB once JSON
# encoded. They stay bounded by the upload budget instead of
# max_json_body_bytes, and are still depth-checked.
LARGE_TEXT_PATH_SUFFIXES = ("/sources/import", "/revisions")
MAX_UPLOAD_FILES = 1
MAX_UPLOAD_FIELDS = 8
ASSET_UPLOAD_OPENAPI = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["project_id", "kind", "file"],
                    "properties": {
                        "project_id": {"type": "string"},
                        "kind": {"type": "string"},
                        "file": {"type": "string", "format": "binary"},
                    },
                }
            }
        },
    }
}
SOURCE_UPLOAD_OPENAPI = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["file"],
                    "properties": {
                        "title": {"type": "string"},
                        "file": {"type": "string", "format": "binary"},
                    },
                }
            }
        },
    }
}


class BodyLimitExceeded(Exception):
    """Raised when the ASGI receive stream exceeds the upload budget."""


class JsonDepthExceeded(Exception):
    """Raised when a JSON body nests deeper than the configured maximum."""


def is_upload_path(path: str) -> bool:
    return path.endswith(UPLOAD_PATH_SUFFIXES)


def max_request_bytes() -> int:
    settings = get_settings()
    return settings.max_upload_bytes + settings.upload_form_overhead_bytes


def max_json_body_bytes(path: str) -> int:
    settings = get_settings()
    if path.endswith(LARGE_TEXT_PATH_SUFFIXES):
        return settings.max_upload_bytes + settings.upload_form_overhead_bytes
    return settings.max_json_body_bytes


def _is_json_content_type(content_type: str) -> bool:
    media_type = content_type.split(";", 1)[0].strip().lower()
    return media_type == "application/json" or media_type.endswith("+json")


# bytes.translate delete-table keeping only the JSON structural bytes
# (" \ { } [ ]); UTF-8 guarantees multi-byte sequences never contain them, so
# the depth scan below only iterates over brackets and quotes even for
# multi-megabyte text bodies.
_JSON_STRUCTURAL_BYTES = b'"\\{}[]'
_JSON_NON_STRUCTURAL_BYTES = bytes(
    byte for byte in range(256) if byte not in _JSON_STRUCTURAL_BYTES
)
# A JSON ``\ud800``-style escape decodes to a lone surrogate. Pydantic 422s
# typed str fields but echoes the invalid input back in the error response,
# which then crashes response encoding (ensure_ascii=False) with a 500;
# untyped dict payloads pass through and poison stored rows. Escaped
# backslashes (``\\ud800`` as literal text) are masked first so only real
# unicode escapes are rewritten; raw NUL cannot legally appear in JSON.
_JSON_SURROGATE_ESCAPE_RE = re.compile(
    rb"\\u(d[89a-f][0-9a-f][0-9a-f])(?:\\u(d[c-f][0-9a-f][0-9a-f]))?",
    re.IGNORECASE,
)


def _scrub_surrogate_escape(match: re.Match[bytes]) -> bytes:
    if match.group(2) is not None:  # well-formed surrogate pair: keep
        return match.group(0)
    return b"\\ufffd"


def sanitize_json_surrogate_escapes(data: bytes) -> bytes:
    """Replace lone ``\uD800``-style escapes with ``\uFFFD`` at the wire level."""

    masked = data.replace(b"\\\\", b"\x00\x00")
    return _JSON_SURROGATE_ESCAPE_RE.sub(_scrub_surrogate_escape, masked).replace(
        b"\x00\x00", b"\\\\"
    )


class _JsonDepthTracker:
    """Incremental structural depth scan over raw JSON bytes.

    Strings (including escapes) are skipped; the running nesting depth is
    compared against the configured maximum so a hostile deeply-nested body
    fails with a 422 before ``json.loads`` can hit a RecursionError 500.
    """

    __slots__ = ("depth", "in_string", "escaped", "max_depth")

    def __init__(self, max_depth: int) -> None:
        self.depth = 0
        self.in_string = False
        self.escaped = False
        self.max_depth = max_depth

    def feed(self, data: bytes) -> None:
        for byte in data.translate(None, _JSON_NON_STRUCTURAL_BYTES):
            if self.in_string:
                if self.escaped:
                    self.escaped = False
                elif byte == 0x5C:  # backslash
                    self.escaped = True
                elif byte == 0x22:  # closing quote
                    self.in_string = False
                continue
            if byte == 0x22:  # opening quote
                self.in_string = True
            elif byte in (0x7B, 0x5B):  # { [
                self.depth += 1
                if self.depth > self.max_depth:
                    raise JsonDepthExceeded()
            elif byte in (0x7D, 0x5D):  # } ]
                self.depth = max(0, self.depth - 1)


@dataclass
class ParsedUpload:
    texts: dict[str, str]
    file: UploadFile


class RequestBodyLimitMiddleware:
    """Count the real request stream before multipart is fully spooled.

    Upload paths keep the upload budget. Every other JSON POST/PUT/PATCH is
    bounded by ``max_json_body_bytes`` (413 on overflow) and structurally
    depth-scanned (422 beyond ``max_json_depth``) so hostile payloads fail
    before Pydantic or ``json.loads`` can turn them into a 500.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        method = scope.get("method", "")
        if method not in {"POST", "PUT", "PATCH"}:
            await self.app(scope, receive, send)
            return
        if is_upload_path(path):
            await self._enforce_byte_limit(
                scope, receive, send, max_request_bytes(), "请求体超过上传上限"
            )
            return
        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        if not _is_json_content_type(headers.get("content-type", "")):
            await self.app(scope, receive, send)
            return
        await self._enforce_json_limits(scope, receive, send, path)

    async def _enforce_byte_limit(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        limit: int,
        message: str,
    ) -> None:
        declared = _declared_content_length(scope)
        if declared is not None and declared > limit:
            await _send_json(send, 413, message)
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
            await _send_json(send, 413, message)

    async def _enforce_json_limits(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        path: str,
    ) -> None:
        settings = get_settings()
        limit = max_json_body_bytes(path)
        declared = _declared_content_length(scope)
        if declared is not None and declared > limit:
            await _send_json(send, 413, "请求体超过大小上限")
            return

        # Buffer the bounded body and validate before dispatching: raising
        # from inside receive() would only surface as FastAPI's generic 400
        # "error parsing the body", and an unbounded body must never reach
        # json.loads (RecursionError) or Pydantic. Rejections are answered
        # here so the app below is never invoked for them.
        chunks: list[bytes] = []
        received = 0
        disconnected = False
        tracker = _JsonDepthTracker(settings.max_json_depth)
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                disconnected = True
                break
            if message["type"] != "http.request":
                continue
            chunk = message.get("body", b"")
            received += len(chunk)
            if received > limit:
                await _send_json(send, 413, "请求体超过大小上限")
                return
            try:
                tracker.feed(chunk)
            except JsonDepthExceeded:
                await _send_json(send, 422, "JSON 嵌套深度超过上限")
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break

        # Scrub lone surrogate escapes on the full buffered body: escapes can
        # straddle chunk boundaries, and neither the Pydantic 422 (which
        # echoes the bad input and crashes response encoding) nor a poisoned
        # stored row is an acceptable outcome. Valid surrogate pairs and
        # literal "\udXXX" text are preserved.
        body = sanitize_json_surrogate_escapes(b"".join(chunks))
        replay_chunks: list[bytes] = [body] if body else []

        async def replay_receive() -> Message:
            if replay_chunks:
                return {
                    "type": "http.request",
                    "body": replay_chunks.pop(0),
                    "more_body": bool(replay_chunks),
                }
            if disconnected:
                return {"type": "http.disconnect"}
            return {"type": "http.request", "body": b"", "more_body": False}

        await self.app(scope, replay_receive, send)


def _declared_content_length(scope: Scope) -> int | None:
    for key, value in scope.get("headers", []):
        if key.decode("latin-1").lower() == "content-length":
            decoded = value.decode("latin-1")
            if decoded.isdigit():
                return int(decoded)
            return None
    return None


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
