"""Regression tests for the provider-neutral contract foundation (Issue #41).

Covers the credential_source derivation, the shared provider error-code set
with the Vertex classifier re-export, and the declarative native capability
data. All checks are offline: no credentials are read and no provider call.
"""

from pathlib import Path
from types import SimpleNamespace

from app.config import Settings
from app.services.credential_source import (
    CONNECTION_KEY,
    ENV_SERVICE_ACCOUNT,
    connection_credential_source,
    credential_source_for_protocol,
    environment_credentials_ready,
)
from app.services.model_registry import build_registry
from app.services.provider_errors import PROVIDER_ERROR_CODES, ProviderFailure
from app.services.vertex_credentials import VertexFailure, classify_vertex_failure


def test_credential_source_depends_only_on_protocol():
    assert credential_source_for_protocol("VERTEX_NATIVE") == ENV_SERVICE_ACCOUNT
    assert credential_source_for_protocol("GOOGLE_NATIVE") == CONNECTION_KEY
    assert credential_source_for_protocol("OPENAI") == CONNECTION_KEY
    assert credential_source_for_protocol("ANTHROPIC") == CONNECTION_KEY
    assert credential_source_for_protocol("SOME_FUTURE_PROTOCOL") == CONNECTION_KEY


def test_connection_credential_source_ignores_provider_identity():
    account_a = SimpleNamespace(protocol="VERTEX_NATIVE")
    account_b = SimpleNamespace(protocol="VERTEX_NATIVE")
    keyed_a = SimpleNamespace(protocol="OPENAI")
    keyed_b = SimpleNamespace(protocol="ANTHROPIC")
    assert connection_credential_source(account_a) == connection_credential_source(account_b)
    assert connection_credential_source(keyed_a) == connection_credential_source(keyed_b)
    assert connection_credential_source(keyed_a) == CONNECTION_KEY
    assert connection_credential_source(account_a) == ENV_SERVICE_ACCOUNT


def test_environment_credential_readiness_stays_in_credential_adapter():
    configured = Settings(
        google_cloud_project="test-project",
        google_application_credentials=Path(__file__),
    )
    unconfigured = Settings()

    assert environment_credentials_ready(configured, "VERTEX_NATIVE") is True
    assert environment_credentials_ready(unconfigured, "VERTEX_NATIVE") is False
    assert environment_credentials_ready(configured, "OPENAI") is False


def test_provider_error_codes_contract_is_stable():
    assert PROVIDER_ERROR_CODES == frozenset(
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


def test_vertex_failure_type_is_shared_provider_failure():
    assert VertexFailure is ProviderFailure
    failure = VertexFailure("AUTHENTICATION", "message", True, authentication=True)
    assert (failure.code, failure.message, failure.retryable, failure.authentication) == (
        "AUTHENTICATION",
        "message",
        True,
        True,
    )


class CodedError(Exception):
    """Adapter-shaped error carrying the ``code`` attribute the classifier reads."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def test_classify_vertex_failure_keeps_existing_semantics():
    cases = [
        (RuntimeError("401 Unauthorized: invalid_grant"), "AUTHENTICATION", True, True),
        (RuntimeError("403 permission denied"), "PERMISSION", False, False),
        (RuntimeError("404 model not found"), "MODEL_NOT_FOUND", False, False),
        (RuntimeError("429 rate limit exceeded"), "RATE_LIMIT", True, False),
        (TimeoutError(), "TIMEOUT", True, False),
        (RuntimeError("connection reset by peer"), "UPSTREAM", True, False),
        (RuntimeError("blocked by safety policy"), "CONTENT_POLICY", False, False),
        (RuntimeError("credential service account private key"), "CONFIGURATION", False, False),
        (CodedError("INVALID_OUTPUT"), "INVALID_OUTPUT", False, False),
        (CodedError("INVALID_INPUT"), "CONFIGURATION", False, False),
    ]
    for error, code, retryable, authentication in cases:
        failure = classify_vertex_failure(error)
        assert failure.code == code
        assert failure.retryable is retryable
        assert failure.authentication is authentication


def test_classify_vertex_failure_codes_stay_in_shared_set():
    errors = [
        RuntimeError("401 invalid_grant"),
        RuntimeError("403 forbidden"),
        RuntimeError("404 not found"),
        RuntimeError("429 resource_exhausted"),
        TimeoutError(),
        RuntimeError("connection refused"),
        RuntimeError("model armor rejected the request"),
        RuntimeError("credential file unreadable"),
        RuntimeError("INVALID_OUTPUT"),
        RuntimeError("UNSUPPORTED_CAPABILITY"),
    ]
    for error in errors:
        assert classify_vertex_failure(error).code in PROVIDER_ERROR_CODES


def test_registry_declares_native_supported_parameters():
    settings = Settings(
        google_cloud_project="test-project",
        google_application_credentials=Path(__file__),
    )
    registry = build_registry(settings)
    assert registry["text.fast"].supported_parameters == (
        "max_output_tokens",
        "thinking_budget",
    )
    assert registry["image.nano_banana_2"].supported_parameters == ()
    assert registry["image.nano_banana_pro"].supported_parameters == ()
    as_dict = registry["text.fast"].to_dict()
    assert as_dict["supported_parameters"] == ["max_output_tokens", "thinking_budget"]
