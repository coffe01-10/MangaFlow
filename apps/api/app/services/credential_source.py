"""Protocol-neutral credential contract derived from the transport protocol.

The provider-neutrality audit (docs/v02-provider-neutrality-audit.md §4.2)
splits provider credentials into two shapes: per-connection API keys stored
encrypted in the database, and environment-managed service accounts. The
derivation may only inspect the transport protocol, never a provider name or
preset key, so every protocol maps onto the same two-value contract.
"""

from __future__ import annotations

from typing import Final, Literal

from app.models import ProviderConnection

CredentialSource = Literal["CONNECTION_KEY", "ENV_SERVICE_ACCOUNT"]

CONNECTION_KEY: Final[CredentialSource] = "CONNECTION_KEY"
ENV_SERVICE_ACCOUNT: Final[CredentialSource] = "ENV_SERVICE_ACCOUNT"

# Transport protocols whose credentials live outside the database, keyed by
# protocol only. GOOGLE_NATIVE stays key-based: the Gemini API authenticates
# with an API key like any other compatible provider.
_ENV_SERVICE_ACCOUNT_PROTOCOLS: Final[frozenset[str]] = frozenset({"VERTEX_NATIVE"})


def credential_source_for_protocol(protocol: str) -> CredentialSource:
    """Return the credential shape required by a transport protocol."""

    if protocol in _ENV_SERVICE_ACCOUNT_PROTOCOLS:
        return ENV_SERVICE_ACCOUNT
    return CONNECTION_KEY


def connection_credential_source(connection: ProviderConnection) -> CredentialSource:
    """Return the credential shape for a connection's current protocol."""

    return credential_source_for_protocol(connection.protocol)
