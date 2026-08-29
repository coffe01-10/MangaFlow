"""Pure, side-effect-free validation for isolated acceptance endpoints.

Importing this module must not import the application, read dotenv, construct an
engine, or contact a server. Endpoint validation is not proof of resource ownership.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit


def _parse(url: str):
    if not url or any(ord(character) < 33 for character in url):
        raise ValueError("Security Violation: URL is missing or contains whitespace/control data.")
    try:
        parsed = urlsplit(url)
        _ = parsed.port
    except ValueError:
        raise ValueError("Security Violation: Invalid endpoint URL.") from None
    if parsed.query:
        raise ValueError("Security Violation: URL must not contain query parameters.")
    if parsed.fragment:
        raise ValueError("Security Violation: URL must not contain fragments.")
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Security Violation: URL must target local loopback.")
    return parsed


def validate_safe_acceptance_pg_url(url: str) -> str:
    parsed = _parse(url)
    if parsed.scheme not in {"postgresql", "postgresql+psycopg", "postgresql+psycopg2"}:
        raise ValueError("Security Violation: Unsupported PostgreSQL driver scheme.")
    if parsed.port != 55432:
        raise ValueError("Security Violation: PostgreSQL port must be 55432.")
    if not re.fullmatch(r"/mangaflow_acceptance(?:_[a-zA-Z0-9_]+)?", parsed.path):
        raise ValueError("Security Violation: Database must start with 'mangaflow_acceptance'.")
    return url


def validate_safe_acceptance_redis_url(url: str) -> str:
    parsed = _parse(url)
    if parsed.scheme not in {"redis", "rediss"}:
        raise ValueError("Security Violation: Unsupported Redis scheme.")
    if parsed.port != 56379:
        raise ValueError("Security Violation: Redis port must be 56379.")
    if re.fullmatch(r"/0+", parsed.path):
        raise ValueError("Security Violation: Redis DB 0 is strictly forbidden.")
    if not re.fullmatch(r"/(?:[1-9]|1[0-5])", parsed.path):
        raise ValueError("Security Violation: Redis path must be a canonical DB index 1..15.")
    return url


def mask_url(url: str) -> str:
    """Do not reflect query values, fragments, or malformed credentials in logs."""
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        if host not in {"127.0.0.1", "localhost", "::1"}:
            return "<redacted-endpoint>"
        port = parsed.port
        host = f"[{host}]" if ":" in host else host
        user = parsed.username or ""
        # Usernames are not required in diagnostics. Preserve ordinary test names
        # only for compatibility; never reflect punctuation or encoded data.
        user = user if re.fullmatch(r"[a-zA-Z0-9_-]*", user) else ""
        authority = f"{user}:***@{host}:{port}"
        path = parsed.path if re.fullmatch(r"/[a-zA-Z0-9_]+", parsed.path) else "/<redacted>"
        return urlunsplit((parsed.scheme, authority, path, "", ""))
    except (TypeError, ValueError):
        return "<redacted-endpoint>"
