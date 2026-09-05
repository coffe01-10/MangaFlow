"""Shared provider failure contract: one error-code set, per-protocol classifiers.

Every protocol classifier maps its native errors onto this single code set
(docs/architecture.md "模型错误统一归类为…"). Message wording stays owned by
each classifier, so the Vertex and Gemini-native classifiers keep their exact
user-visible behavior while sharing the code vocabulary below. Moving the
classifier functions themselves is deferred: their wording is part of the
current product behavior and must not change in this phase.
"""

from __future__ import annotations

from dataclasses import dataclass

# Canonical provider-neutral codes emitted across adapters and services:
# authentication, permission, model availability, rate limiting, content
# policy, configuration, unsupported capability, invalid input/output,
# timeout, and upstream transport failures.
PROVIDER_ERROR_CODES: frozenset[str] = frozenset(
    {
        "AUTHENTICATION",
        "CONTENT_POLICY",
        "CONFIGURATION",
        "INVALID_INPUT",
        "INVALID_OUTPUT",
        "MODEL_NOT_FOUND",
        "PERMISSION",
        "RATE_LIMIT",
        "TIMEOUT",
        "UNSUPPORTED_CAPABILITY",
        "UPSTREAM",
    }
)


@dataclass(frozen=True)
class ProviderFailure:
    """Classification result shared by every protocol classifier."""

    code: str
    message: str
    retryable: bool
    authentication: bool = False


MAX_RETRY_AFTER_SECONDS = 3600


def parse_retry_after_seconds(value: str | None) -> int | None:
    """Bound a provider ``Retry-After`` hint to a sane cooldown window.

    Third-party gateways echo epoch timestamps or garbage here; an unbounded
    value used to overflow ``timedelta``/``datetime`` inside
    ``mark_key_failure`` (crashing the failure path so the key never cooled
    down) or silently cool a key for decades. Unparseable, non-positive and
    oversized hints all fall back to the caller's default.
    """

    if not value:
        return None
    try:
        seconds = int(value)
    except ValueError:
        return None
    if seconds <= 0:
        return None
    return min(seconds, MAX_RETRY_AFTER_SECONDS)

