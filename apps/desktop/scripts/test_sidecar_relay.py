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


PIPE_SCENARIOS = [
    # (id, response_delay, close_after_response, client_half_close, requests, idle_gap)
    pytest.param("client-half-close", 0.0, False, True, 1, 0.0, id="client-half-close"),
    pytest.param("upstream-fin", 0.0, True, True, 1, 0.0, id="upstream-fin"),
    pytest.param("slow-first-byte", 5.6, False, False, 1, 0.0, id="slow-first-byte"),
    pytest.param("keep-alive-reuse", 0.0, False, False, 2, 5.5, id="keep-alive-reuse"),
]


@pytest.mark.parametrize(
    ("scenario", "response_delay", "close_after_response", "client_half_close",
     "requests", "idle_gap"),
    PIPE_SCENARIOS,
)
def test_relay_pipe_semantics_table(
    monkeypatch, scenario, response_delay, close_after_response, client_half_close,
    requests, idle_gap,
):
    """Table-driven pipe lifecycle: every per-connection shape must survive.

    - ``client-half-close``: a client that shuts its write side after a full
      request (HTTP pipelines do this) must still get the response.
    - ``upstream-fin``: when the API side closes first (uvicorn closing a
      keep-alive connection), the FIN must reach the client as EOF instead
      of a silent dead connection.
    - ``slow-first-byte``: a >5s TTFB must survive — the connect timeout may
      not leak into the pump (the original #252 regression).
    - ``keep-alive-reuse``: a connection idle >5s must stay usable, not be
      severed under the web server's pooled connections.
    """
    api = StubApi(response_delay=response_delay, close_after_response=close_after_response)
    read_budget = 10 if response_delay else 4
    try:
        port, stop = start_relay(monkeypatch, api)
        try:
            client = socket.create_connection(("127.0.0.1", port), timeout=15)
            try:
                responses = []
                for request_index in range(requests):
                    client.sendall(REQUEST)
                    responses.append(read_response(client, timeout_seconds=read_budget))
                    if request_index == 0 and requests == 2:
                        time.sleep(idle_gap)
                if client_half_close:
                    client.shutdown(socket.SHUT_WR)  # "no more request bytes"
                if scenario == "upstream-fin":
                    # EOF must flow on the SAME connection: the client must
                    # see the peer's close, never a silent dead connection.
                    tail = read_until_closed(client, timeout_seconds=4)
                    assert tail == b"", f"EOF expected right after the response: {tail!r}"
            finally:
                client.close()
            for response in responses:
                assert response.startswith(b"HTTP/1.1 200"), response
                assert response.endswith(b"ok"), response
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


ERROR_PATH_SCENARIOS = ["dead-upstream-recovery", "client-rst-isolation"]


@pytest.mark.parametrize("scenario", ERROR_PATH_SCENARIOS)
def test_relay_error_path_table(monkeypatch, scenario):
    """Table-driven error paths: one broken connection may never poison the
    accept loop, and the relay must recover the moment the cause clears.

    - ``dead-upstream-recovery``: a refused upstream ends the one request
      promptly (FIN **or** RST — the relay closes with the client's request
      bytes still unread), then serves again once the API port comes alive
      on the same port.
    - ``client-rst-isolation``: a client RST (SO_LINGER 0 close, as a
      WebView tab aborting a request mid-flight) kills only that
      connection's pump threads — the listener keeps serving.
    """
    api = StubApi()
    try:
        if scenario == "dead-upstream-recovery":
            # Relay in front of a port with no listener (uvicorn down).
            dead_port = _free_port()
            port, stop = start_relay_on(monkeypatch, dead_port)
        else:
            port, stop = start_relay(monkeypatch, api)
        try:
            if scenario == "dead-upstream-recovery":
                client = socket.create_connection(("127.0.0.1", port), timeout=15)
                try:
                    client.sendall(REQUEST)
                    assert read_until_closed(client, timeout_seconds=4) == b""
                finally:
                    client.close()
                # The API comes alive on the very same port.
                recovering = StubApi(port=dead_port)
                try:
                    client = socket.create_connection(("127.0.0.1", port), timeout=15)
                    try:
                        client.sendall(REQUEST)
                        body = read_response(client, timeout_seconds=4)
                    finally:
                        client.close()
                    assert body.endswith(b"ok"), body
                finally:
                    recovering.close()
            else:
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


@pytest.mark.parametrize(
    ("scenario", "explode_on"),
    [
        pytest.param(
            "outer-construction-fails",
            lambda kwargs: kwargs.get("name") == "mangaflow-web-relay-pipe",
            id="outer-construction-fails",
        ),
        pytest.param(
            "inner-construction-fails",
            lambda kwargs: getattr(kwargs.get("target"), "__name__", "") == "_pipe",
            id="inner-construction-fails",
        ),
    ],
)
def test_relay_releases_slot_when_thread_construction_fails(monkeypatch, scenario, explode_on):
    """Thread CONSTRUCTION failing (MemoryError under host-wide pressure)
    must release the limiter slot at BOTH levels - a permanent leak would
    ratchet the relay's effective capacity to zero, one connection at a
    time.

    Regression: #290's RuntimeError guards covered only `start()`; the
    `Thread(...)` allocations sat outside them, so a construction failure
    escaped `_pump` with the slot held (inner) or killed the accept loop
    (outer).
    """
    api = StubApi()
    try:
        monkeypatch.setattr(helper, "WEB_RELAY_MAX_CONNECTIONS", 1)
        port, stop = start_relay(monkeypatch, api)
        try:
            real_thread = threading.Thread

            def exploding_thread(*args, **kwargs):
                # Targeted explosion: the relay's own spawn sites only - a
                # blanket patch would also kill the StubApi's handler
                # threads, breaking the served phase below.
                if explode_on(kwargs):
                    raise MemoryError("host-wide pressure")
                return real_thread(*args, **kwargs)

            monkeypatch.setattr(helper.threading, "Thread", exploding_thread)
            refused = socket.create_connection(("127.0.0.1", port), timeout=15)
            try:
                refused.sendall(REQUEST)
                assert read_until_closed(refused, timeout_seconds=4) == b"", (
                    "a construction-failed connection must be dropped promptly"
                )
            finally:
                refused.close()

            # Restore construction: the released slot must serve the next
            # client (the leak would have ratcheted capacity to zero).
            monkeypatch.setattr(helper.threading, "Thread", real_thread)
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


def test_relay_caps_concurrent_connections(monkeypatch):
    """Live relay connections are bounded: overflow is closed fast, and a
    released slot serves the next client.

    Each pinned connection costs three threads (two pumps + one joiner)
    for as long as its peers keep it open; without a bound, one leaking
    client grows the helper without limit. The cap must be far above
    legitimate use (the Next server's pool) and must NOT sever existing
    connections when it trips.
    """
    api = StubApi()
    try:
        # The limiter snapshots the cap when _serve_relay (the accept
        # thread) starts, so the patch must land BEFORE start_relay.
        monkeypatch.setattr(helper, "WEB_RELAY_MAX_CONNECTIONS", 2)
        port, stop = start_relay(monkeypatch, api)
        try:
            pinned = []
            for _ in range(2):
                client = socket.create_connection(("127.0.0.1", port), timeout=15)
                client.sendall(REQUEST)
                body = read_response(client, timeout_seconds=4)
                assert body.endswith(b"ok"), body
                pinned.append(client)

            # Third connection while both slots are pinned: closed promptly,
            # with no response bytes.
            overflow = socket.create_connection(("127.0.0.1", port), timeout=15)
            try:
                overflow.sendall(REQUEST)
                assert read_until_closed(overflow, timeout_seconds=4) == b"", (
                    "an overflow connection must be closed without a response"
                )
            finally:
                overflow.close()

            # Releasing one pinned slot lets the next client through, and the
            # surviving pinned connection is unaffected. Bounded retry: the
            # release needs a few thread hops (FIN -> pumps -> join), and a
            # slow runner must not fail the cap logic with a false negative.
            pinned[0].close()
            deadline = time.monotonic() + 5.0
            served = None
            while served is None and time.monotonic() < deadline:
                client = socket.create_connection(("127.0.0.1", port), timeout=15)
                try:
                    client.sendall(REQUEST)
                    served = read_response(client, timeout_seconds=2)
                except (pytest.fail.Exception, OSError):
                    # Not free yet: refused (FIN/RST like the overflow path).
                    client.close()
                    time.sleep(0.2)
                else:
                    client.close()
            assert served is not None and served.endswith(b"ok"), (
                f"a slot was never released for a new client: {served!r}"
            )
            pinned[1].sendall(REQUEST)
            assert read_response(pinned[1], timeout_seconds=4).endswith(b"ok")
        finally:
            for client in pinned:
                client.close()
            stop()
    finally:
        api.close()
