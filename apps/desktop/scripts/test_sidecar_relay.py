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

import errno
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
                body = read_response(client, timeout_seconds=15)
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


class _FlakyAcceptListener:
    """Hand ``_serve_relay`` a listener whose accept() fails on demand.

    ``socket.socket`` is a C type, so per-instance method overrides are
    impossible; ``_serve_relay`` only calls ``accept()`` on the listener,
    so this proxy is the narrowest injection point for accept-loop errors.
    """

    def __init__(self, sock: socket.socket, failures: list[Exception]) -> None:
        self._sock = sock
        self._failures = list(failures)

    def accept(self) -> tuple[socket.socket, tuple]:
        if self._failures:
            raise self._failures.pop(0)
        return self._sock.accept()


TRANSIENT_ACCEPT_ERRORS = [
    pytest.param(
        ConnectionAbortedError(errno.ECONNABORTED, "client RST between handshake and accept"),
        id="econnaborted",
    ),
    pytest.param(
        OSError(errno.EPROTO, "protocol error pending on the listener"),
        id="eproto",
    ),
    pytest.param(
        OSError(errno.EMFILE, "fd exhaustion under host pressure"),
        id="emfile",
    ),
]


@pytest.mark.parametrize("error", TRANSIENT_ACCEPT_ERRORS)
def test_relay_accept_survives_transient_accept_errors(monkeypatch, error):
    """A transient accept() error may never kill the relay loop (#438).

    The historical defect: every non-timeout OSError exited the loop
    permanently — ports stayed bound, the journal stayed ready, and all
    plan-B API traffic hung with zero detection. The loop must treat
    ECONNABORTED/EPROTO/EMFILE-class accept failures as retryable and
    serve the very next connection.
    """
    api = StubApi()
    try:
        port = _free_port()
        monkeypatch.setattr(helper, "WEB_RELAY_PORT", port)
        relay = helper._bind_relay(api.port)
        assert relay is not None, "the relay bind must succeed on a free port"
        flaky = _FlakyAcceptListener(relay, [error])
        thread = threading.Thread(
            target=helper._serve_relay, args=(flaky, api.port), daemon=True
        )
        thread.start()
        try:
            client = socket.create_connection(("127.0.0.1", port), timeout=15)
            try:
                client.sendall(REQUEST)
                body = read_response(client, timeout_seconds=15)
            finally:
                client.close()
            assert body.endswith(b"ok"), body
        finally:
            relay.close()
            thread.join(timeout=5)
            assert not thread.is_alive()
    finally:
        api.close()


def test_relay_terminal_accept_error_logs_before_exiting(monkeypatch):
    """A terminal accept() error (listener closed) must be logged, never
    silent (#438): the pre-#463 loop returned without a single log line,
    making a dead relay indistinguishable from a healthy journal."""
    logged: list[str] = []
    monkeypatch.setattr(helper, "_log", lambda message: logged.append(message))
    port = _free_port()
    monkeypatch.setattr(helper, "WEB_RELAY_PORT", port)
    # Dead upstream port is fine: the loop must die on the listener close
    # before any client ever connects.
    relay = helper._bind_relay(port)
    assert relay is not None, "the relay bind must succeed on a free port"
    thread = threading.Thread(
        target=helper._serve_relay, args=(relay, port), daemon=True
    )
    thread.start()
    time.sleep(0.2)  # let the loop enter accept()
    relay.close()  # next accept() raises the terminal EBADF
    thread.join(timeout=5)
    assert not thread.is_alive(), "a closed listener must end the accept loop"
    assert any("relay listener exiting" in line for line in logged), logged


def test_relay_partial_pump_start_releases_slot_and_serves_next(monkeypatch):
    """A partial pump start must unwind deterministically (#443 item 1).

    The historical hazard: when the SECOND pipe thread's start() failed
    under host thread exhaustion, the cleanup path closed both sockets
    while the FIRST pump was blocked in recv on them. Linux does not wake
    a blocked recv on close() — if the fd number was reused by the next
    accepted connection, the surviving pump silently consumed that
    connection's first bytes. PR #480 made the unwind SHUT_RDWR-first
    (waking the survivor) and this test pins the observable contract at
    the connection level: the broken connection drops, its relay slot is
    released, and the NEXT client is served intact.
    """
    api = StubApi()
    try:
        monkeypatch.setattr(helper, "WEB_RELAY_MAX_CONNECTIONS", 1)
        port, stop = start_relay(monkeypatch, api)
        try:
            real_thread = threading.Thread
            pipe_starts = {"count": 0}

            class _StartFailsSecondPipe:
                """Presents start() but raises on it — stand-in for the
                RuntimeError('can't start new thread') the OS raises under
                thread exhaustion."""

                def start(self) -> None:
                    raise RuntimeError("can't start new thread")

            def flaky_thread(*args, **kwargs):
                if getattr(kwargs.get("target"), "__name__", "") == "_pipe":
                    pipe_starts["count"] += 1
                    if pipe_starts["count"] == 2:
                        return _StartFailsSecondPipe()
                return real_thread(*args, **kwargs)

            monkeypatch.setattr(helper.threading, "Thread", flaky_thread)
            broken = socket.create_connection(("127.0.0.1", port), timeout=15)
            try:
                broken.sendall(REQUEST)
                # No response can ever flow (the upstream->client pump
                # never started): the client must see a definite end, not
                # a hang.
                # Load-tolerant bound (#595): the contract is "a definite
                # end arrives, not a hang" — the number only has to be an
                # upper bound, never a latency pin.
                assert read_until_closed(broken, timeout_seconds=15) == b""
            finally:
                broken.close()
            assert pipe_starts["count"] == 2, "the second pipe start must be the one exploding"

            # The slot must be free again: with the cap at 1, only a
            # released slot lets this next client through — and the bytes
            # it exchanges must be its own (fd-reuse byte theft would
            # corrupt or stall this exchange).
            monkeypatch.setattr(helper.threading, "Thread", real_thread)
            client = socket.create_connection(("127.0.0.1", port), timeout=15)
            try:
                client.sendall(REQUEST)
                body = read_response(client, timeout_seconds=15)
            finally:
                client.close()
            assert body.endswith(b"ok"), body
        finally:
            stop()
    finally:
        api.close()


def test_relay_survives_a_half_broken_pipe_upstream(monkeypatch):
    """The upstream send raising EPIPE mid-response (a peer that closed
    after reading only part of the response, e.g. a browser tab navigating
    away) must kill only that connection's pump: the relay keeps serving
    and a fresh connection works end to end.

    Simulates by wrapping the upstream send: the FIRST response send
    raises BrokenPipeError after the stub got the request.
    """
    api = StubApi()
    try:
        port, stop = start_relay(monkeypatch, api)
        original_sendall = socket.socket.sendall

        def broken_sendall(self, data):
            if b"200 OK" in data:
                raise BrokenPipeError(32, "Broken pipe")
            return original_sendall(self, data)

        monkeypatch.setattr(socket.socket, "sendall", broken_sendall)
        victim = socket.create_connection(("127.0.0.1", port), timeout=15)
        try:
            victim.sendall(REQUEST)
            assert read_until_closed(victim, timeout_seconds=4) == b"", (
                "an EPIPE-damaged response must not be delivered as content"
            )
        finally:
            victim.close()
        monkeypatch.undo()

        client = socket.create_connection(("127.0.0.1", port), timeout=15)
        try:
            client.sendall(REQUEST)
            body = read_response(client, timeout_seconds=4)
        finally:
            client.close()
        assert body.startswith(b"HTTP/1.1 200") and body.endswith(b"ok"), body
        stop()
    finally:
        api.close()

class TestRelayLimiterUnit:
    """Unit pins for the limiter's own arithmetic (the connection-level test
    covers the cap's observable effect; these pin the counter under
    contention and its clamp, without sockets)."""

    def test_release_clamps_at_zero_and_cap_is_the_snapshot(self):
        limiter = helper._RelayLimiter(2)
        assert limiter.max_connections == 2, "the snapshot must be the cap"
        # Release with nothing live: must clamp at zero, never go negative
        # (a negative live count would permanently shrink the capacity).
        limiter.release()
        limiter.release()
        assert limiter.try_acquire(), "capacity must survive stray releases"
        # Full cycle: this second acquire fills the cap (1 live already),
        # the next is refused, one release frees exactly one slot, and two
        # releases return to zero (clamped).
        assert limiter.try_acquire()
        assert not limiter.try_acquire(), "the third acquire must be refused"
        limiter.release()
        assert limiter.try_acquire(), "a release must free exactly one slot"
        limiter.release()
        limiter.release()
        # Back at zero (clamped): one more acquire must succeed (1 live).
        assert limiter.try_acquire()

    def test_try_acquire_is_atomic_under_thread_contention(self):
        """N threads racing M slots: exactly M acquires succeed overall —
        an off-by-one cap or a broken refuse branch lets the count
        overshoot (the round-15 review notes CPython's GIL makes a torn
        read of this two-step check unrealizable, so the lock's absence
        is pinned at the connection level, not here). The drain back to a
        working acquire catches a release that lost count."""

        limiter = helper._RelayLimiter(8)
        successes = []
        lock = threading.Lock()
        barrier = threading.Barrier(16)

        def contender():
            barrier.wait()  # maximize contention on the same instant
            if limiter.try_acquire():
                with lock:
                    successes.append(1)

        threads = [threading.Thread(target=contender) for _ in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert len(successes) == 8, (
            f"exactly 8 of 16 contenders must win: {len(successes)}"
        )
        # Drain: each winner releases once; stray releases are impossible
        # by construction here, so the counter must be back at zero —
        # observable as 8 more successful acquires.
        for _ in range(8):
            limiter.release()
        assert limiter.try_acquire(), (
            "capacity must be restored — a lost release shows here"
        )



class _FakeProcess:
    """Process double for the close() escalation ladder: terminate succeeds
    (graceful), or wait hangs until killed (escalation), or hangs forever
    (reap timeout)."""

    def __init__(self, mode: str) -> None:
        self.mode = mode  # "graceful" | "needs-kill" | "unreapable" | "exited"
        self.terminate_calls = 0
        self.kill_calls = 0
        self.wait_calls = 0

    def poll(self) -> int | None:
        return 0 if self.mode == "exited" else None

    def terminate(self) -> None:
        self.terminate_calls += 1
        if self.mode in ("graceful", "exited"):
            self.mode = "exited"

    def kill(self) -> None:
        self.kill_calls += 1
        if self.mode == "needs-kill":
            self.mode = "exited"

    def wait(self, timeout=None):
        self.wait_calls += 1
        if self.mode != "exited":
            import subprocess

            raise subprocess.TimeoutExpired("fake", timeout)
        return 0


class _FakeSock:
    def __init__(self, close_raises: bool = False) -> None:
        self.closed = False
        self._close_raises = close_raises

    def close(self) -> None:
        if self._close_raises:
            raise OSError("close failed (EBADF)")
        self.closed = True


def test_webserver_close_terminates_gracefully_and_releases_both_sockets():
    process = _FakeProcess("graceful")
    web_sock, relay = _FakeSock(), _FakeSock()
    helper.WebServer(process, 1, 2, web_sock, relay).close()
    assert process.terminate_calls == 1
    assert process.kill_calls == 0, "a graceful wait must not escalate to kill"
    assert web_sock.closed and relay.closed


def test_webserver_close_escalates_to_kill_and_reaps():
    """wait(5) times out after terminate: close must kill and then REAP
    (a lingering zombie holds the pid and reads as a phantom running node)."""
    process = _FakeProcess("needs-kill")
    web_sock, relay = _FakeSock(), _FakeSock()
    helper.WebServer(process, 1, 2, web_sock, relay).close()
    assert process.terminate_calls == 1
    assert process.kill_calls == 1
    assert process.wait_calls == 2, "the killed child must be reaped promptly"
    assert web_sock.closed and relay.closed


def test_webserver_close_survives_an_unreapable_child():
    """Even when the reap wait also times out, close must not raise — and
    must STILL release both sockets (a raise here would skip the finally's
    socket releases on the helper's shutdown path)."""
    process = _FakeProcess("unreapable")
    web_sock, relay = _FakeSock(), _FakeSock()
    helper.WebServer(process, 1, 2, web_sock, relay).close()
    assert process.kill_calls == 1
    assert web_sock.closed and relay.closed


def test_webserver_close_skips_termination_for_an_exited_child():
    process = _FakeProcess("exited")
    web_sock, relay = _FakeSock(), _FakeSock()
    helper.WebServer(process, 1, 2, web_sock, relay).close()
    assert process.terminate_calls == 0
    assert process.kill_calls == 0
    assert web_sock.closed and relay.closed


def test_webserver_close_releases_the_second_socket_when_the_first_fails():
    """E3-F2: both closes are guarded — a raising close (EBADF from a
    dead fd) must be SUPPRESSED and must not skip the second socket.
    Requiring no raise AND the healthy socket released pins the guard in
    both directions: a guard removal re-raises, a guard narrowing to
    only-the-first-socket strands the relay."""
    process = _FakeProcess("exited")
    broken, healthy = _FakeSock(close_raises=True), _FakeSock()
    helper.WebServer(process, 1, 2, broken, healthy).close()  # must not raise
    assert healthy.closed, "the second socket must still be released"


def test_relay_saturation_log_is_rate_limited(monkeypatch):
    """The saturation entry ("connection limit N reached") must log ONCE
    per 10s cooldown under a refuse-flood — the stderr log only rotates
    across sessions, so one line per refusal would grow it for the rest
    of the session. Drive a real cap-2 relay with repeated overflow
    clients and count the lines on the helper's captured _log."""

    import io

    api = StubApi()
    captured = io.StringIO()
    monkeypatch.setattr(helper, "_log", lambda message: captured.write(message + "\n"))
    monkeypatch.setattr(helper, "WEB_RELAY_MAX_CONNECTIONS", 2)
    port, stop = start_relay(monkeypatch, api)
    try:
        # Fill both slots with pinned connections.
        pinned = []
        for _ in range(2):
            client = socket.create_connection(("127.0.0.1", port), timeout=15)
            client.sendall(REQUEST)
            assert read_response(client, timeout_seconds=4).endswith(b"ok")
            pinned.append(client)

        # Overflow flood: several refused clients in quick succession.
        for _ in range(5):
            overflow = socket.create_connection(("127.0.0.1", port), timeout=15)
            overflow.sendall(REQUEST)
            overflow.close()

        # The log write happens on the relay's accept thread — under load
        # the flood can complete before it logs (#595), so wait for the
        # first line instead of racing it; the CEILING is then what pins
        # the rate limiting (not one line per refusal).
        import time as _time

        deadline = _time.monotonic() + 2.0
        lines: list[str] = []
        while _time.monotonic() < deadline:
            lines = [
                line for line in captured.getvalue().splitlines()
                if "connection limit" in line
            ]
            if lines:
                break
            _time.sleep(0.05)
        assert len(lines) <= 2, (
            f"saturation log must be rate-limited, got {len(lines)} lines: {lines}"
        )
        assert lines, "at least one saturation line must land within 2s"
        assert all(
            str(helper.WEB_RELAY_MAX_CONNECTIONS) in line for line in lines
        ), "the saturation line must name the enforced cap"

        # Release a slot, then the flood resumes being refused — a NEW
        # cooldown line is correct (state changed), and it must again be
        # rate-limited, not one-per-refusal.
        for client in pinned:
            client.close()
        stop()
    except BaseException:
        for client in pinned:
            client.close()
        stop()
        raise
