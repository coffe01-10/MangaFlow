"""Regression tests for the helper's fixed-loopback relay (plan B, W-15).

The relay is a pure byte pipe between the Next standalone server's
build-time rewrite destination (127.0.0.1:39443) and the helper's dynamic
API port. These tests drive ``_bind_relay`` / ``_serve_relay`` directly
against local stub servers — no node, no API, no fixed port — so they run
in the plain sandbox:

    python3 -m pytest apps/desktop/scripts/test_sidecar_relay.py -v

They pin the pipe contract: once a relay connection is established, the
pump must not impose any read deadline of its own. The historical defect
was the connect-phase timeout (5s) staying armed on the upstream socket,
which dropped any response whose first byte took longer than 5s and
severed every keep-alive connection after 5s of silence.
"""

from __future__ import annotations

import socket
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sidecar"))

import mangaflow_desktop_helper as helper  # noqa: E402

REQUEST = b"GET /api/v1/health HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n"
RESPONSE = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: keep-alive\r\n\r\nok"


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class StubApi:
    """Minimal HTTP/1.1 keep-alive server standing in for uvicorn."""

    def __init__(self, response_delay: float = 0.0) -> None:
        self.response_delay = response_delay
        self._server = socket.socket()
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(16)
        self.port = self._server.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self._server.accept()
            except OSError:
                return  # listener closed by close()
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        try:
            while True:
                conn.settimeout(10)
                request = b""
                while b"\r\n\r\n" not in request:
                    chunk = conn.recv(65536)
                    if not chunk:
                        return
                    request += chunk
                if self.response_delay:
                    time.sleep(self.response_delay)
                conn.sendall(RESPONSE)
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def close(self) -> None:
        self._server.close()


def start_relay(monkeypatch: pytest.MonkeyPatch, api: StubApi) -> tuple[int, Callable[[], None]]:
    """Bind and run the helper's relay in front of ``api``; (port, stop)."""
    port = _free_port()
    monkeypatch.setattr(helper, "WEB_RELAY_PORT", port)
    relay = helper._bind_relay(api.port)
    assert relay is not None, "the relay bind must succeed on a free port"
    thread = threading.Thread(
        target=helper._serve_relay, args=(relay, api.port), daemon=True
    )
    thread.start()

    def stop() -> None:
        relay.close()  # accept() raises OSError and the pump thread returns
        thread.join(timeout=5)
        assert not thread.is_alive(), "relay pump thread must stop when the listener closes"

    return port, stop


def read_response(client: socket.socket, timeout_seconds: float) -> bytes:
    client.settimeout(0.5)
    deadline = time.monotonic() + timeout_seconds
    data = b""
    while time.monotonic() < deadline:
        try:
            chunk = client.recv(65536)
        except socket.timeout:
            continue
        if not chunk:
            pytest.fail("relay closed the connection before a full response")
        data += chunk
        if b"\r\n\r\n" in data:
            return data
    pytest.fail(f"no complete response within {timeout_seconds}s: {data!r}")


def test_relay_delivers_a_response_slower_than_the_connect_timeout(monkeypatch):
    """A >5s TTFB must survive: the connect timeout may not leak into the pipe.

    Regression: the upstream socket kept the 5s connect timeout armed, so an
    API response whose first byte took longer than 5s was dropped mid-pipe
    and the client received a bare FIN instead of the response.
    """
    api = StubApi(response_delay=5.6)
    try:
        port, stop = start_relay(monkeypatch, api)
        try:
            client = socket.create_connection(("127.0.0.1", port), timeout=15)
            try:
                client.sendall(REQUEST)
                body = read_response(client, timeout_seconds=10)
            finally:
                client.close()
            assert b"200 OK" in body, body
        finally:
            stop()
    finally:
        api.close()


def test_relay_serves_a_second_request_after_a_long_keep_alive_gap(monkeypatch):
    """A keep-alive connection idle >5s must stay usable, not be severed.

    Regression: with the upstream timeout armed, 5s of silence between two
    requests on the same connection tore the pipe down and half-closed the
    client side, so the web server's pooled connection died under it.
    """
    api = StubApi()
    try:
        port, stop = start_relay(monkeypatch, api)
        try:
            client = socket.create_connection(("127.0.0.1", port), timeout=15)
            try:
                client.sendall(REQUEST)
                first = read_response(client, timeout_seconds=4)
                time.sleep(5.5)  # longer than the old 5s upstream timeout
                client.sendall(REQUEST)
                second = read_response(client, timeout_seconds=4)
            finally:
                client.close()
            assert b"200 OK" in first, first
            assert b"200 OK" in second, second
        finally:
            stop()
    finally:
        api.close()
