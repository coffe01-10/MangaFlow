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


BIND_SCENARIOS = ["own-time-wait", "foreign-live-listener", "foreign-reuseaddr-listener"]


def _make_foreign_listener(reuse_addr: bool) -> tuple[socket.socket, int]:
    """A foreign LISTEN socket owning a free port (SO_REUSEADDR optional)."""
    foreign = socket.socket()
    if reuse_addr:
        foreign.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    foreign.bind(("127.0.0.1", 0))
    foreign.listen(1)
    return foreign, foreign.getsockname()[1]


@pytest.mark.parametrize("scenario", BIND_SCENARIOS)
def test_relay_bind_policy_table(monkeypatch, scenario):
    """Table-driven bind policy for the fixed relay port (127.0.0.1:39443).

    - ``own-time-wait``: the app's OWN TIME_WAIT remnants must not block the
      bind (the relay is the active close whenever the API side finishes
      first) — POSIX SO_REUSEADDR, Windows keeps strict semantics.
    - ``foreign-live-listener`` / ``foreign-reuseaddr-listener``: a foreign
      process holding the port must always fail the bind closed, whether or
      not the foreign socket itself sets SO_REUSEADDR (POSIX still requires
      SO_REUSEPORT on BOTH sockets to share a listening port — one flag on
      the newcomer cannot take the port).
    """
    if scenario == "own-time-wait":
        if sys.platform == "win32":
            pytest.skip("the fix is POSIX-only; Windows keeps strict TIME_WAIT semantics")
        port = _free_port()
        listener = socket.socket()
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", port))
        listener.listen(1)
        client = socket.create_connection(("127.0.0.1", port), timeout=2)
        accepted, _ = listener.accept()
        accepted.close()  # active close: TIME_WAIT now occupies 127.0.0.1:<port>
        listener.close()
        client.close()
        # Precondition: without SO_REUSEADDR the port must genuinely be
        # blocked right now; retry briefly in case close ordering lags.
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
        must_bind = True
    else:
        foreign, port = _make_foreign_listener(reuse_addr=scenario == "foreign-reuseaddr-listener")
        must_bind = False

    monkeypatch.setattr(helper, "WEB_RELAY_PORT", port)
    relay = helper._bind_relay(1)
    try:
        if must_bind:
            assert relay is not None, "the relay bind must survive its own TIME_WAIT remnants"
        else:
            assert relay is None, (
                f"{scenario}: a foreign live listener must fail the bind closed"
            )
    finally:
        if relay is not None:
            relay.close()
