"""Protocol-neutral credential contract derived from the transport protocol.

The provider-neutrality audit (docs/v02-provider-neutrality-audit.md §4.2)
splits provider credentials into two shapes: per-connection API keys stored
encrypted in the database, and environment-managed service accounts. The
derivation may only inspect the transport protocol, never a provider name or
preset key, so every protocol maps onto the same two-value contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from app.config import Settings
from app.models import ProviderConnection

CredentialSource = Literal["CONNECTION_KEY", "ENV_SERVICE_ACCOUNT"]

CONNECTION_KEY: Final[CredentialSource] = "CONNECTION_KEY"
ENV_SERVICE_ACCOUNT: Final[CredentialSource] = "ENV_SERVICE_ACCOUNT"

# Transport protocols whose credentials live outside the database, keyed by
# protocol only. GOOGLE_NATIVE stays key-based: the Gemini API authenticates
# with an API key like any other compatible provider.
_ENV_SERVICE_ACCOUNT_PROTOCOLS: Final[frozenset[str]] = frozenset({"VERTEX_NATIVE"})


@dataclass(frozen=True)
class ProtocolCapabilities:
    """Product-facing capabilities declared by a transport protocol.

    These flags deliberately depend on the protocol rather than a provider
    preset.  A custom connection using the same transport must receive the
    same UI and API behavior as a built-in provider.
    """

    supports_model_discovery: bool
    supported_model_types: tuple[Literal["TEXT", "IMAGE"], ...]


_PROTOCOL_CAPABILITIES: Final[dict[str, ProtocolCapabilities]] = {
    "OPENAI": ProtocolCapabilities(True, ("TEXT", "IMAGE")),
    "ANTHROPIC": ProtocolCapabilities(False, ("TEXT",)),
    "GOOGLE_NATIVE": ProtocolCapabilities(True, ("TEXT", "IMAGE")),
    # Vertex model listing has not been verified against a stable upstream
    # contract.  Seeded/manual catalog entries remain available, while the UI
    # must not offer a discovery action that only echoes local rows.
    "VERTEX_NATIVE": ProtocolCapabilities(False, ("TEXT", "IMAGE")),
}
_DEFAULT_PROTOCOL_CAPABILITIES: Final[ProtocolCapabilities] = ProtocolCapabilities(
    False, ("TEXT",)
)


def credential_source_for_protocol(protocol: str) -> CredentialSource:
    """Return the credential shape required by a transport protocol."""

    if protocol in _ENV_SERVICE_ACCOUNT_PROTOCOLS:
        return ENV_SERVICE_ACCOUNT
    return CONNECTION_KEY


def connection_credential_source(connection: ProviderConnection) -> CredentialSource:
    """Return the credential shape for a connection's current protocol."""

    return credential_source_for_protocol(connection.protocol)


def environment_credentials_ready(settings: Settings, protocol: str) -> bool:
    """Return whether an environment-account protocol can authenticate.

    Product callers deliberately ask this adapter instead of reading a
    provider-specific setting. The compatibility setting remains confined to
    the credential boundary until the legacy environment contract is retired.
    """

    if credential_source_for_protocol(protocol) != ENV_SERVICE_ACCOUNT:
        return False
    return settings.vertex_configured


def capabilities_for_protocol(protocol: str) -> ProtocolCapabilities:
    """Return declared product capabilities for a transport protocol."""

    return _PROTOCOL_CAPABILITIES.get(protocol, _DEFAULT_PROTOCOL_CAPABILITIES)


def connection_protocol_capabilities(
    connection: ProviderConnection,
) -> ProtocolCapabilities:
    """Return declared capabilities for a connection's transport."""

    return capabilities_for_protocol(connection.protocol)
