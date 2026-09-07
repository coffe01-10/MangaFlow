"""Regression tests for the relay listener's bind semantics (plan B, W-15).

The helper owns the fixed loopback relay port (127.0.0.1:39443) with a
fail-closed bind: when the port is taken, the web server is skipped for
the session rather than served against a wrong target. These tests pin
the two sides of that contract:

- the bind must survive the app's OWN TIME_WAIT remnants (the relay is the
  active close whenever the API side finishes first, so its accepted
  sockets routinely sit in TIME_WAIT on the fixed port; a relaunch inside
  the ~60s window used to fail the bind and silently downgrade the session
  to the static-export form);
- the bind must still refuse a foreign LIVE listener (SO_REUSEADDR must
  not become a port-hijacking flag).

    python3 -m pytest apps/desktop/scripts/test_sidecar_relay_bind.py -v
"""

from __future__ import annotations

import socket
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sidecar"))

import mangaflow_desktop_helper as helper  # noqa: E402


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="the fix is POSIX-only; Windows keeps strict TIME_WAIT bind semantics",
)
def test_relay_bind_survives_its_own_time_wait_remnants(monkeypatch):
    """Relaunching over a just-closed session's TIME_WAIT must keep plan B up.

    Regression: `_bind_relay` set no SO_REUSEADDR, so any relay socket left
    in TIME_WAIT on the fixed port (the normal state after a session that
    served API traffic) blocked the next session's bind for ~60s and the
    helper silently started without the web server.
    """
    port = _free_port()
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", port))
    listener.listen(1)
    client = socket.create_connection(("127.0.0.1", port), timeout=2)
    accepted, _ = listener.accept()
    # Active close from the accepted side (the relay's role whenever the API
    # finishes first): TIME_WAIT now occupies 127.0.0.1:<port>.
    accepted.close()
    listener.close()
    client.close()

    # Precondition: without SO_REUSEADDR the port must genuinely be blocked
    # right now; retry briefly in case the close ordering lags.
    deadline = time.monotonic() + 3.0
    while True:
        probe = socket.socket()
        try:
            probe.bind(("127.0.0.1", port))
            probe.close()
            if time.monotonic() >= deadline:
                pytest.skip("TIME_WAIT block did not materialize on this platform")
            time.sleep(0.1)
        except OSError:
            break

    monkeypatch.setattr(helper, "WEB_RELAY_PORT", port)
    relay = helper._bind_relay(1)
    try:
        assert relay is not None, "the relay bind must survive its own TIME_WAIT remnants"
    finally:
        if relay is not None:
            relay.close()


def test_relay_bind_stays_fail_closed_against_a_foreign_live_listener(monkeypatch):
    """SO_REUSEADDR must not let the relay take a port another process holds."""

    foreign = socket.socket()
    foreign.bind(("127.0.0.1", 0))
    foreign.listen(1)
    try:
        monkeypatch.setattr(helper, "WEB_RELAY_PORT", foreign.getsockname()[1])
        assert helper._bind_relay(1) is None, (
            "a foreign live listener must still fail the bind closed"
        )
    finally:
        foreign.close()
