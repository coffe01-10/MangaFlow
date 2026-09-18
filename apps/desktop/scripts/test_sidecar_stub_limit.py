"""Stub health-server connection cap (#873).

``_StubServer`` is a ThreadingHTTPServer: without a limiter, a local flood
of idle connections grows the helper one thread per accept until
``Thread.start`` raises and the stub loop dies. The relay already has
``_RelayLimiter``; the stub now gates ``process_request`` through the same
class. These tests drive the stub server directly — no helper process.

    python3 -m pytest apps/desktop/scripts/test_sidecar_stub_limit.py -v
"""

from __future__ import annotations

import socket
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sidecar"))

import mangaflow_desktop_helper as helper  # noqa: E402

REQUEST = b"GET /api/v1/health HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n"


def read_until_closed(client: socket.socket, timeout_seconds: float) -> bytes:
    client.settimeout(0.5)
    deadline = time.monotonic() + timeout_seconds
    data = b""
    while time.monotonic() < deadline:
        try:
            chunk = client.recv(65536)
        except socket.timeout:
            continue
        except OSError:
            return data
        if not chunk:
            return data
        data += chunk
    pytest.fail(
        f"stub never closed the overflow connection within {timeout_seconds}s: {data!r}"
    )


def read_health_body(client: socket.socket, timeout_seconds: float) -> bytes:
    client.settimeout(0.5)
    deadline = time.monotonic() + timeout_seconds
    data = b""
    while time.monotonic() < deadline:
        try:
            chunk = client.recv(65536)
        except socket.timeout:
            continue
        except OSError as error:
            pytest.fail(f"health connection died early: {error!r} bytes={data!r}")
        if not chunk:
            pytest.fail(f"health connection closed before a full response: {data!r}")
        data += chunk
        header_end = data.find(b"\r\n\r\n")
        if header_end < 0:
            continue
        headers = data[:header_end].decode("latin-1")
        length = None
        for line in headers.split("\r\n"):
            if line.lower().startswith("content-length:"):
                length = int(line.split(":", 1)[1].strip())
                break
        if length is None:
            continue
        body = data[header_end + 4 :]
        if len(body) >= length:
            return body[:length]
    pytest.fail(f"health response incomplete within {timeout_seconds}s: {data!r}")


def start_stub() -> tuple[helper._StubServer, threading.Thread]:
    server = helper._StubServer(("127.0.0.1", 0), helper._StubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_stub_server_caps_concurrent_connections(monkeypatch):
    """Live stub connections are bounded: overflow is closed fast, and a
    released slot serves the next client. The limiter snapshots the cap
    at construction, so the patch must land BEFORE start_stub.

    Idle TCP connections (no request bytes) are the issue's flood shape:
    ``handle_one_request`` blocks on readline, so the handler thread holds
    its limiter slot until the client closes. Completing a request would
    release the slot (the stub speaks HTTP/1.0) and make the cap
    unobservable.
    """

    monkeypatch.setattr(helper, "STUB_MAX_CONNECTIONS", 2)
    server, _thread = start_stub()
    try:
        port = server.server_address[1]
        assert server._limiter.max_connections == 2, server._limiter.max_connections
        pinned: list[socket.socket] = []
        try:
            for _ in range(2):
                pinned.append(
                    socket.create_connection(("127.0.0.1", port), timeout=15)
                )
            deadline = time.monotonic() + 5.0
            while server._limiter._live < 2 and time.monotonic() < deadline:
                time.sleep(0.05)
            assert server._limiter._live == 2, (
                f"both idle connections must occupy limiter slots: "
                f"{server._limiter._live}"
            )

            overflow = socket.create_connection(("127.0.0.1", port), timeout=15)
            try:
                overflow.sendall(REQUEST)
                assert read_until_closed(overflow, timeout_seconds=15) == b"", (
                    "an overflow connection must be closed without a response"
                )
            finally:
                overflow.close()

            pinned[0].close()
            deadline = time.monotonic() + 5.0
            served = None
            while served is None and time.monotonic() < deadline:
                client = socket.create_connection(("127.0.0.1", port), timeout=15)
                try:
                    client.sendall(REQUEST)
                    served = read_health_body(client, timeout_seconds=2)
                except (pytest.fail.Exception, OSError):
                    client.close()
                    time.sleep(0.2)
                else:
                    client.close()
            assert served is not None and served.endswith(b'"desktop-stub"}'), (
                f"a slot was never released for a new client: {served!r}"
            )
        finally:
            for client in pinned:
                try:
                    client.close()
                except OSError:
                    pass
    finally:
        server.shutdown()
        server.server_close()


def test_stub_server_process_request_uses_the_relay_limiter():
    """Structural pin: overflow gating must go through ``_RelayLimiter``,
    not a one-off counter that can drift from the relay. Reverting to a
    bare ThreadingHTTPServer flips this red."""
    import inspect

    source = inspect.getsource(helper._StubServer)
    assert "_RelayLimiter" in source, source
    assert "try_acquire" in source, source
    assert "process_request" in source, source
    assert "STUB_MAX_CONNECTIONS" in inspect.getsource(helper._StubServer.__init__), (
        inspect.getsource(helper._StubServer.__init__)
    )
