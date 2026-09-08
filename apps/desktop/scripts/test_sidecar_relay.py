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
import struct
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

    def __init__(self, response_delay: float = 0.0, close_after_response: bool = False,
                 port: int | None = None) -> None:
        self.response_delay = response_delay
        self.close_after_response = close_after_response
        self._server = socket.socket()
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", port if port is not None else 0))
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
                if self.close_after_response:
                    conn.close()
                    return
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def close(self) -> None:
        self._server.close()


def start_relay_on(
    monkeypatch: pytest.MonkeyPatch, api_port: int
) -> tuple[int, Callable[[], None]]:
    """Bind and run the helper's relay in front of ``api_port``; (port, stop).

    ``api_port`` may be a port with no listener (dead-upstream scenarios).
    """
    port = _free_port()
    monkeypatch.setattr(helper, "WEB_RELAY_PORT", port)
    relay = helper._bind_relay(api_port)
    assert relay is not None, "the relay bind must succeed on a free port"
    thread = threading.Thread(
        target=helper._serve_relay, args=(relay, api_port), daemon=True
    )
    thread.start()

    def stop() -> None:
        relay.close()  # accept() raises OSError and the pump thread returns
        thread.join(timeout=5)
        assert not thread.is_alive(), "relay pump thread must stop when the listener closes"

    return port, stop


def start_relay(monkeypatch: pytest.MonkeyPatch, api: StubApi) -> tuple[int, Callable[[], None]]:
    return start_relay_on(monkeypatch, api.port)


def read_response(client: socket.socket, timeout_seconds: float) -> bytes:
    """Read one full response: headers plus the declared Content-Length body.

    Truncation after the header boundary is a failure, not a pass — the
    body bytes are part of the pipe contract under test.
    """
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
        header, sep, body = data.partition(b"\r\n\r\n")
        if sep:
            declared = next(
                (
                    int(line.split(b":", 1)[1])
                    for line in header.split(b"\r\n")
                    if line.lower().startswith(b"content-length:")
                ),
                None,
            )
            if declared is not None and len(body) >= declared:
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
            assert body.endswith(b"ok"), body
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
            assert first.startswith(b"HTTP/1.1 200") and first.endswith(b"ok"), first
            assert second.startswith(b"HTTP/1.1 200") and second.endswith(b"ok"), second
        finally:
            stop()
    finally:
        api.close()


def read_until_closed(client: socket.socket, timeout_seconds: float) -> bytes:
    """Read until the connection ends (clean FIN **or** RST); return the
    bytes that arrived. Fails only on a hang — a dead connection that never
    tells the client is the defect this guard exists for."""
    client.settimeout(0.5)
    deadline = time.monotonic() + timeout_seconds
    data = b""
    while time.monotonic() < deadline:
        try:
            chunk = client.recv(65536)
        except socket.timeout:
            continue
        except OSError:
            return data  # RST: an abrupt but definite end
        if not chunk:
            return data
        data += chunk
    pytest.fail(
        f"relay never closed the connection within {timeout_seconds}s: {data!r}"
    )


@pytest.mark.parametrize(
    ("scenario", "close_after_response"),
    [
        pytest.param("client-half-close", False, id="client-half-close"),
        pytest.param("upstream-fin", True, id="upstream-fin"),
    ],
)
def test_relay_pipe_semantics_table(monkeypatch, scenario, close_after_response):
    """Table-driven half-close semantics: EOF must flow through the pipe.

    - ``client-half-close``: a client that shuts its write side after a full
      request (HTTP pipelines do this) must still get the response — the
      relay forwards the request even though no further bytes will come.
    - ``upstream-fin``: when the API side closes first (uvicorn closing a
      keep-alive connection), the relay must deliver that FIN to the client
      as EOF instead of hanging it with an open, dead connection.
    """
    api = StubApi(close_after_response=close_after_response)
    try:
        port, stop = start_relay(monkeypatch, api)
        try:
            client = socket.create_connection(("127.0.0.1", port), timeout=15)
            try:
                client.sendall(REQUEST)
                client.shutdown(socket.SHUT_WR)  # "no more request bytes"
                body = read_response(client, timeout_seconds=4)
                assert body.endswith(b"ok"), body
                # Whatever closes first, the client must eventually see the
                # peer's EOF — never a silent, dead connection.
                tail = read_until_closed(client, timeout_seconds=4)
            finally:
                client.close()
            if scenario == "upstream-fin":
                assert tail == b"", f"EOF expected right after the response: {tail!r}"
            else:
                # Half-close: any tail bytes must be response body, never junk.
                assert set(tail) <= set(b"ok"), f"unexpected tail {tail!r}"
        finally:
            stop()
    finally:
        api.close()


def test_relay_survives_a_dead_upstream_and_recovers(monkeypatch):
    """Error path: a refused upstream must fail the one request fast, never
    kill the accept loop — and the relay must serve again once the API port
    comes alive on the same port.

    Regression shape: uvicorn is down while node already proxies; the next
    connection after recovery must work (the relay's accept loop and pump
    threads are per-connection, one failure may not poison the listener).
    """
    dead_port = _free_port()
    port, stop = start_relay_on(monkeypatch, dead_port)
    try:
        # While the API port refuses connections: connect succeeds (the relay
        # owns the listener), then the client's connection ends promptly -
        # a FIN or, because the relay closes with the client's request
        # bytes still unread, an RST; either way never a silent hang.
        client = socket.create_connection(("127.0.0.1", port), timeout=15)
        try:
            client.sendall(REQUEST)
            assert read_until_closed(client, timeout_seconds=4) == b""
        finally:
            client.close()

        # The API comes alive on the very same port (uvicorn finished booting).
        api = StubApi(port=dead_port)
        try:
            client = socket.create_connection(("127.0.0.1", port), timeout=15)
            try:
                client.sendall(REQUEST)
                body = read_response(client, timeout_seconds=4)
            finally:
                client.close()
            assert body.endswith(b"ok"), body
        finally:
            api.close()
    finally:
        stop()


def test_relay_accept_loop_survives_a_client_reset(monkeypatch):
    """Error path: a client RST (SO_LINGER 0 close) kills only that
    connection's pump threads — the relay listener must keep serving.

    Regression shape: a WebView tab aborting a request mid-flight resets the
    proxy connection; one reset may not take the whole relay down.
    """
    api = StubApi()
    try:
        port, stop = start_relay(monkeypatch, api)
        try:
            resetter = socket.create_connection(("127.0.0.1", port), timeout=15)
            resetter.setsockopt(
                socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
            )
            resetter.sendall(b"GET /aborted HTTP/1.1\r\nHost: 127.0.0.1\r\n")
            resetter.close()  # linger(1,0) close is abortive: always an RST
            time.sleep(0.3)  # let the pump meet the reset

            client = socket.create_connection(("127.0.0.1", port), timeout=15)
            try:
                client.sendall(REQUEST)
                body = read_response(client, timeout_seconds=4)
            finally:
                client.close()
            assert body.endswith(b"ok"), body
        finally:
            stop()
    finally:
        api.close()
