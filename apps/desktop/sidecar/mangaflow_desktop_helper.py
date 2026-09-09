"""MangaFlow desktop sidecar helper (V02-54 delivery; grown from V02-53B PoC).

Implements the frozen ADR startup protocol (`docs/adr/v02-desktop-shell-evaluation.md`
§4.2/§4.4) in the form the desktop shell consumes:

1. The shell creates the runtime directory ``mangaflow-desktop-<token>`` and spawns
   this helper as its direct child with ``MANGAFLOW_DESKTOP_TOKEN`` and
   ``MANGAFLOW_DESKTOP_JOURNAL`` in the environment.
2. The helper atomically binds ``127.0.0.1:0`` (no probe-release-rebind), runs
   Alembic migrations (app mode), then publishes the readiness journal
   ``{pid, api_origin, owner_token, ...}`` and prints one ``MANGAFLOW_READY``
   line on stdout.
3. The helper does NOT start serving traffic until the shell verifies the
   handshake (token + PID + journal) and writes ``MANGAFLOW_GO <token>`` on
   stdin. Any other stdin content or EOF aborts with exit code 75.
4. Ownership: on Linux the shell sets ``PR_SET_PDEATHSIG`` so the helper dies
   with the shell, and the helper puts itself into its own session so the shell
   can ``kill(-pgid)`` the whole tree. On Windows the shell assigns the helper
   to the root Job Object with ``KILL_ON_JOB_CLOSE`` (see shell-core
   ``ownership.rs``; real Windows verification is NOT RUN in this sandbox).
   Graceful stop on both platforms: the shell closes the helper's stdin pipe
   (Windows' only cooperative channel — a Job Object cannot deliver SIGTERM),
   and an EOF watcher thread inside the helper reacts by raising SIGTERM to
   itself, which runs the ``sys.exit(0)`` handler registered here (or
   uvicorn's own graceful SIGTERM handling once it owns the process), so the
   serve loop unwinds through the FastAPI lifespan shutdown. The shell's
   escalation (Unix SIGKILL / Windows ``TerminateJobObject``) is the backstop
   if the process cannot exit cooperatively within the grace window.

Modes:
  stub -- the handshake is exercised without FastAPI (serves /api/v1/health).
  app  -- runs the real ``app.main:app`` (Alembic + SQLite + optional fake
          model channel) on the pre-bound socket via uvicorn.

Journal records carry identity only (token/pid/port/origin/state); commands,
environment and secrets are never written to the journal.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TOKEN_RE = re.compile(r"[0-9a-f]{32}")
GO_PREFIX = "MANGAFLOW_GO "
EXIT_HANDSHAKE_REFUSED = 75


def _log(message: str) -> None:
    print(f"[desktop-helper] {message}", file=sys.stderr, flush=True)


def _read_context() -> tuple[str, Path]:
    token = os.environ.get("MANGAFLOW_DESKTOP_TOKEN", "")
    journal = Path(os.environ.get("MANGAFLOW_DESKTOP_JOURNAL", ""))
    if TOKEN_RE.fullmatch(token) is None:
        raise ValueError("invalid process ownership token")
    if journal.is_symlink() or not journal.is_absolute():
        raise ValueError("process journal must be an absolute real path")
    directory = journal.parent
    if directory.name != f"mangaflow-desktop-{token}":
        raise ValueError("process journal/runtime ownership mismatch")
    if directory.resolve() != directory.absolute():
        raise ValueError("process runtime path/ownership mismatch")
    return token, journal


def _write_journal(journal: Path, record: dict) -> None:
    pending = journal.with_name(journal.name + ".pending")
    for target in (journal, pending):
        if target.is_symlink():
            raise RuntimeError("process journal must not be a link")
    pending.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")
    os.replace(pending, journal)


def _bind_loopback() -> socket.socket:
    """Atomically claim an ephemeral loopback port for the API server.

    Unix: SO_REUSEADDR is the classic listen-socket option (TIME_WAIT
    rebind); it cannot let a second listener share an actively bound
    specific address. Windows: SO_REUSEADDR means the OPPOSITE — it invites
    a second bind of the same port, so a local process could share or steal
    the session's traffic after the origin was verified and injected. win32
    therefore sets SO_EXCLUSIVEADDRUSE instead, which makes ANY conflicting
    bind fail outright (fail-closed).
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if sys.platform == "win32":
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    else:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    return sock


# Fixed loopback relay port (plan B, W-15). The Next standalone bundle
# compiles next.config.ts rewrites at BUILD time, so the bundle's API
# destination is this constant; at runtime the helper owns the port and
# relays those connections to its own dynamic API port. Loopback only, and
# the session-start bind is fail-closed (see _spawn_web_server).
WEB_RELAY_PORT = 39443

# Upper bound on live relay connections (see _serve_relay). The only real
# client is the local Next standalone server; 128 is two orders of magnitude
# above anything it pools while bounding the helper's thread count.
WEB_RELAY_MAX_CONNECTIONS = 128


class _RelayLimiter:
    """Thread-safe counter bounding live relay connections."""

    def __init__(self, max_connections: int) -> None:
        self._max = max_connections
        self._lock = threading.Lock()
        self._live = 0

    def try_acquire(self) -> bool:
        with self._lock:
            if self._live >= self._max:
                return False
            self._live += 1
            return True

    def release(self) -> None:
        with self._lock:
            self._live = max(0, self._live - 1)


def _bind_relay(api_port: int) -> socket.socket | None:
    """Claim the fixed relay port and listen; None when it is taken.

    POSIX: sets SO_REUSEADDR so the bind survives this app's own TIME_WAIT
    remnants (#253). The relay is the active closer whenever the API side
    finishes first, so its accepted sockets routinely end up in TIME_WAIT on
    the fixed port; without the flag, relaunching the shell within the ~60s
    TIME_WAIT window failed the bind and silently downgraded the session to
    the static-export form — exactly the degradation the fail-closed bind
    was meant to reserve for a foreign owner. SO_REUSEADDR cannot take the
    port from a foreign live listener (Linux would require SO_REUSEPORT on
    both sockets), so fail-closed against a foreign owner is unchanged.

    Windows: sets SO_EXCLUSIVEADDRUSE (PR #256). A plain no-option bind is
    the platform default there, but it still yields to a later binder that
    sets SO_REUSEADDR — the Windows semantic is the opposite of POSIX and
    allows the second process to share/steal the session's relayed traffic.
    SO_EXCLUSIVEADDRUSE rejects even those binders outright (any conflicting
    bind fails), at the cost of retaining the TIME_WAIT residual on this
    platform: within the ~60s window a relaunch reports the port taken and
    downgrades to the static-export form, which stays fail-closed.
    """

    relay = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if sys.platform == "win32":
        relay.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    else:
        relay.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        relay.bind(("127.0.0.1", WEB_RELAY_PORT))
        relay.listen(64)
    except OSError as error:
        _log(f"relay port {WEB_RELAY_PORT} unavailable ({error!r}); starting without the web server")
        relay.close()
        return None
    relay.settimeout(0.5)
    return relay


def _serve_relay(relay: socket.socket, api_port: int) -> None:
    """Accept relay connections and pipe bytes to the dynamic API port.

    A pure byte pipe: the API is on the loopback adapter, the relay adds no
    parsing, no auth and no TLS — it exists only so a build-time-fixed
    rewrite destination reaches this session's dynamic API port. Runs on a
    daemon thread; the helper exit paths simply drop the listening socket
    and the process teardown closes all piped connections.

    Concurrent connections are bounded (``WEB_RELAY_MAX_CONNECTIONS``): each
    pinned connection costs three threads (two pumps + one joiner) for as
    long as both peers keep it open, so an unbounded accept loop would let one leaking or hostile
    local client grow the helper without limit. The real client is the Next
    standalone server on the same host — tens of pooled connections at most
    — so the bound is far above legitimate use while keeping the failure
    mode explicit: overflow connections are closed immediately (fail-fast,
    same shape as a refused upstream) instead of being silently starved.
    """

    limiter = _RelayLimiter(WEB_RELAY_MAX_CONNECTIONS)
    log_cooldown = 0.0
    while True:
        try:
            client, _ = relay.accept()
        except socket.timeout:
            continue
        except OSError:
            return  # listener closed — helper is shutting down
        if not limiter.try_acquire():
            # Rate-limited: the stderr log only rotates across sessions, so
            # one line per refused connection under a refuse-flood would
            # grow it for the rest of the session. Log the transition into
            # saturation (and its end) instead of every refusal.
            now = time.monotonic()
            if now >= log_cooldown:
                _log(
                    f"relay connection limit {WEB_RELAY_MAX_CONNECTIONS} "
                    "reached; closing overflow clients"
                )
                log_cooldown = now + 10.0
            client.close()
            continue
        try:
            upstream = socket.create_connection(("127.0.0.1", api_port), timeout=5)
        except OSError:
            client.close()
            limiter.release()
            continue
        # The 5s timeout above bounds the CONNECT only; the byte pipe itself
        # must never time out. With the timeout left armed on the upstream
        # socket, a response whose first byte took longer than 5s to produce
        # was dropped mid-flight (the client saw a bare FIN), and every
        # keep-alive connection was severed after 5s of silence. The accepted
        # client side is already blocking (a timeout-mode listener accepts in
        # blocking mode), so the pump below runs without any read deadline on
        # both directions; liveness is the peers' business (HTTP closes,
        # process teardown closes the sockets).
        upstream.settimeout(None)

        def _pipe(src: socket.socket, dst: socket.socket) -> None:
            try:
                while True:
                    data = src.recv(65536)
                    if not data:
                        break
                    dst.sendall(data)
            except OSError:
                pass
            finally:
                try:
                    dst.shutdown(socket.SHUT_WR)
                except OSError:
                    pass

        def _pump(pair_client: socket.socket, pair_upstream: socket.socket) -> None:
            # Two pumps, one slot: the connection holds its limiter slot
            # until BOTH directions are done, then releases it for the next
            # accepted client. The sockets arrive as ARGUMENTS, not through
            # the accept loop's shared cells: those are rebound on every
            # iteration, and a deferred read from this thread could pipe a
            # foreign pair (the classic late-binding closure hazard).
            pumps = [
                threading.Thread(target=_pipe, args=(pair_client, pair_upstream), daemon=True),
                threading.Thread(target=_pipe, args=(pair_upstream, pair_client), daemon=True),
            ]
            try:
                for pump in pumps:
                    pump.start()
            except RuntimeError:
                # Thread creation failed under host-wide pressure (OS
                # thread/memory limits - not our own counter, which the cap
                # bounds). Drop the pair deterministically; the accept loop
                # must stay immortal even then.
                for sock in (pair_client, pair_upstream):
                    try:
                        sock.close()
                    except OSError:
                        pass
                limiter.release()
                return
            for pump in pumps:
                pump.join()
            # Both directions are done; close explicitly so the pair's fate
            # does not depend on refcount timing.
            for sock in (pair_client, pair_upstream):
                try:
                    sock.close()
                except OSError:
                    pass
            limiter.release()

        spawn = threading.Thread(
            target=_pump, args=(client, upstream), name="mangaflow-web-relay-pipe", daemon=True
        )
        try:
            spawn.start()
        except RuntimeError:
            # Same host-wide failure, one level up: without this guard the
            # exception would kill the ACCEPT loop while 39443 stays bound,
            # leaving backlog clients hanging for the rest of the session.
            client.close()
            upstream.close()
            limiter.release()
            continue


def _pid_starttime() -> int | None:
    """Linux identity anchor equivalent to Windows GetProcessTimes creation."""
    try:
        fields = Path("/proc/self/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
        return int(fields[19])
    except (OSError, IndexError, ValueError):
        return None


def _await_go(token: str) -> bool:
    line = sys.stdin.readline()
    if line.strip() != GO_PREFIX + token:
        _log("handshake GO line rejected; refusing to serve")
        return False
    return True


def _start_stdin_eof_watch() -> None:
    """Arm the cooperative stop channel: stdin EOF -> SIGTERM to this process.

    The Windows shell cannot deliver a signal across the Job Object boundary,
    so its graceful stop closes the helper's stdin pipe instead; Unix shells
    signal the process group directly and never depend on this thread. On EOF
    the watcher raises SIGTERM into this process: before uvicorn starts, the
    handler registered in ``main()`` (``sys.exit(0)``) runs and unwinds through
    ``main()``'s cleanup; once uvicorn owns the process — its Windows fallback
    installs ``signal.signal(SIGTERM, ...)`` on its handled signals — the same
    raise flips uvicorn's ``should_exit`` and the serve loop shuts down through
    the FastAPI lifespan. Python executes signal handlers on the main thread,
    and uvicorn's ~0.1 s serve tick guarantees prompt dispatch.

    Started only AFTER the GO line was consumed so it never races
    ``_await_go`` for stdin; any post-GO stdin bytes are drained and ignored.
    ``daemon=True``: if the main thread is ever stuck inside a C call that
    never checks for signals, this thread must not block process exit — the
    shell's escalation (Unix SIGKILL / Windows TerminateJobObject) is the
    documented backstop for that residual case.
    """

    def _watch() -> None:
        try:
            while sys.stdin.readline() != "":
                pass  # drain post-GO stdin bytes; EOF ends the loop
        except Exception:  # noqa: BLE001 - any read failure means the pipe is gone
            pass
        signal.raise_signal(signal.SIGTERM)

    threading.Thread(target=_watch, name="mangaflow-stdin-eof", daemon=True).start()


def _spawn_grandchild() -> subprocess.Popen[str] | None:
    """Spawn a test descendant that dies with the helper (Linux PDEATHSIG)."""
    if sys.platform == "win32":
        return None

    def _preexec() -> None:  # pragma: no cover - runs in forked child
        import ctypes

        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGKILL, 0, 0, 0)

    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(3600)"],
        preexec_fn=_preexec,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


class _StubServer(ThreadingHTTPServer):
    """stub 模式的健康服务器，绑定策略与 ``_bind_loopback`` 一致：
    Windows 上不设 SO_REUSEADDR（``allow_reuse_address``），改设
    SO_EXCLUSIVEADDRUSE，避免同端口二次绑定劫持；Unix 行为不变。"""

    allow_reuse_address = sys.platform != "win32"

    def server_bind(self) -> None:
        if sys.platform == "win32":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class _StubHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        if self.path != "/api/v1/health":
            self.send_error(404)
            return
        body = b'{"status":"ok","channel":"desktop-stub"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:  # keep stdout to the protocol only
        return


def _run_stub(journal: Path, record: dict, grandchild: bool) -> int:
    server = _StubServer(("127.0.0.1", 0), _StubHandler)
    try:
        port = server.server_address[1]
        record.update(
            state="ready",
            pid=os.getpid(),
            pid_starttime=_pid_starttime(),
            api_origin=f"http://127.0.0.1:{port}",
            port=port,
        )
        _write_journal(journal, record)
        print(f"MANGAFLOW_READY {json.dumps({k: record[k] for k in ('token', 'pid', 'api_origin')})}", flush=True)
        if grandchild:
            child = _spawn_grandchild()
            if child is not None:
                record["grandchild_pid"] = child.pid
                _write_journal(journal, record)
        if not _await_go(record["token"]):
            server.server_close()
            return EXIT_HANDSHAKE_REFUSED
        _start_stdin_eof_watch()
        server.serve_forever()
        return 0
    finally:
        server.server_close()


def _run_app(args: argparse.Namespace, journal: Path, record: dict) -> int:
    api_root = Path(args.api_root).resolve()
    sys.path.insert(0, str(api_root))
    user_data = Path(args.user_data).resolve()
    # The shell supplies the user-data root; the helper lays out the
    # database directory under it (ADR §4.1 install-form discipline).
    (user_data / "data").mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MANGAFLOW_DISABLE_DOTENV", "1")
    os.environ["DATABASE_URL"] = f"sqlite:///{user_data / 'data' / 'mangaflow.db'}"
    os.environ["STORAGE_ROOT"] = str(user_data / "storage")
    os.environ["UPLOAD_ROOT"] = str(user_data / "uploads")
    os.environ["WEB_ORIGIN"] = args.web_origin

    node = None
    sock = None
    relay = None
    web_shutdown = threading.Event()
    try:
        sock = _bind_loopback()
        port = sock.getsockname()[1]
        # The web server's compiled rewrites target this exact API origin, so
        # spawn it only after the API port is bound (plan B, W-15).
        node, web_port, relay = _spawn_web_server(args, port)
        try:
            from alembic import command
            from alembic.config import Config as AlembicConfig

            alembic_config = AlembicConfig(str(api_root / "alembic.ini"))
            alembic_config.set_main_option(
                "sqlalchemy.url", os.environ["DATABASE_URL"]
            )
            command.upgrade(alembic_config, "head")
        except BaseException as error:  # noqa: BLE001 - journal the failure, then exit
            record.update(state="failed", error=f"alembic:{type(error).__name__}")
            _write_journal(journal, record)
            raise

        if args.fake_channel:
            from fake_channel import install  # provided next to this helper

            install()

        import uvicorn

        if node is not None:
            # Red team 2026-09-08: publishing web_origin is only allowed for
            # a node that provably owns its port. Spawn alone proves nothing
            # — node can die at boot (EADDRINUSE lost to a bind-close race
            # winner, or any crash) while this helper sails on to announce
            # the still-free port, and the shell then navigates its WebView
            # into whatever local process claims it. Fail-closed: a node
            # that never answers is reaped and the session continues without
            # a web server (the shell falls back to the static export).
            node, web_port = _await_web_server(node, web_port)
            if node is not None:
                # Ownership of the announced origin now includes noticing
                # when it dies: arm the mid-session exit watch (log-only,
                # ADR §4.5 detection scope).
                _start_web_exit_watch(node, web_shutdown)
        if node is None and relay is not None:
            # A degraded (static-export) session has no web server the relay
            # could feed: release the fixed relay port now instead of
            # squatting on it until process exit. Spawn-degraded sessions
            # never reach here (their relay was already closed inside
            # _spawn_web_server and arrives as None); boot-degraded sessions
            # arrive with the node just reaped above. Hoisted out of the
            # `if node is not None:` guard so the release does not read as
            # depending on the spawn-time node handle.
            relay.close()
            relay = None

        record.update(
            state="ready",
            pid=os.getpid(),
            pid_starttime=_pid_starttime(),
            api_origin=f"http://127.0.0.1:{port}",
            port=port,
        )
        if node is not None:
            # Plan B (W-15): the web server is a helper child (Job member,
            # killed with the tree) serving the Next standalone bundle; the
            # shell builds the WebView against this loopback origin only.
            record.update(web_origin=f"http://127.0.0.1:{web_port}", web_port=web_port)
        _write_journal(journal, record)
        ready_fields = ["token", "pid", "api_origin"]
        if node is not None:
            ready_fields.append("web_origin")
        print(f"MANGAFLOW_READY {json.dumps({k: record[k] for k in ready_fields})}", flush=True)

        if not _await_go(record["token"]):
            return EXIT_HANDSHAKE_REFUSED

        # Arm the cooperative stop channel before the (slow) app import: the
        # shell's graceful stop may arrive while third-party libraries are still
        # loading, and it must unwind cleanly through main()'s cleanup rather
        # than waiting for the kill escalation.
        _start_stdin_eof_watch()

        from app.main import app

        config = uvicorn.Config(app, log_level="warning", access_log=False, lifespan="on")
        server = uvicorn.Server(config)
        server.run(sockets=[sock])
        return 0
    finally:
        # The web server is a helper child: terminate it on every helper exit
        # path. Windows job-kill covers the crash paths (node is a Job
        # member through the helper); Unix pdeathsig (set on the node spawn
        # above) covers the shell/helper death; this finally covers the
        # cooperative and refused-GO paths.
        web_shutdown.set()  # deliberate stop: the exit watcher must stay silent
        if node is not None and node.poll() is None:
            node.terminate()
            try:
                node.wait(timeout=5)
            except subprocess.TimeoutExpired:
                node.kill()
        if sock is not None:
            sock.close()
        if relay is not None:
            # Stop the accept loop explicitly (process teardown would close
            # the fd anyway); a still-open relay here means the success path
            # is exiting, so nothing needs the fixed port any more.
            relay.close()
            relay = None


def _find_node(web_dist: Path) -> str | None:
    """Resolve the node runtime for the Next standalone server.

    Order: a node bundled next to the web bundle (``<web_dist>/../node/``,
    the install-form resource layout assembled by the packaging step), then
    a node next to this helper script, then the PATH node (dev form).
    None = run without a web server (shell then serves the static export,
    the pre-W-15 form).
    """

    exe = "node.exe" if sys.platform == "win32" else "node"
    candidates = [
        web_dist.parent / "node" / exe,
        Path(__file__).resolve().parent / "node" / exe,
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    import shutil

    found = shutil.which("node")
    return str(found) if found else None


def _spawn_web_server(
    args: argparse.Namespace, api_port: int
) -> tuple[subprocess.Popen | None, int | None, socket.socket | None]:
    """Start the Next standalone server (plan B, W-15) as a helper child.

    --web-dist must point at the standalone bundle directory (containing
    server.js). The web port is claimed by a temporary bind(0), handed to
    node via PORT, and released before spawn — the tiny bind-close race is
    a documented, accepted residual (loopback, per-user dir, single-instance
    shell).

    Rewrites destination: the standalone bundle compiles next.config.ts
    rewrites at BUILD time (routes-manifest.json), so the destination cannot
    be re-pointed at this session's dynamic API port via the environment.
    Instead the bundle is built with the fixed relay port
    ``WEB_RELAY_PORT`` (127.0.0.1:39443) as the rewrite destination, and
    this helper binds that relay port for the session and forwards
    connections to its actual dynamic API port (``api_port``). The relay is
    a pure byte pipe on the loopback adapter — no TLS, no HTTP parsing —
    and lives and dies with the helper. Binding the fixed relay port is
    fail-closed: if another process owns 39443 (e.g. a foreign app), the
    web server is skipped rather than served against a wrong target.
    No node on PATH and no --web-dist = no web server (None), which keeps
    the pre-W-15 static-export form working unchanged.
    """

    web_dist = getattr(args, "web_dist", None)
    if not web_dist:
        return None, None, None
    dist_path = Path(web_dist).resolve()
    node = _find_node(dist_path)
    if node is None:
        _log("no node runtime found; starting without the web server")
        return None, None, None
    server_js = dist_path / "server.js"
    if not server_js.is_file():
        _log(f"web dist {server_js} has no server.js; starting without the web server")
        return None, None, None
    relay = _bind_relay(api_port)
    if relay is None:
        return None, None, None
    claim = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        claim.bind(("127.0.0.1", 0))
        web_port = claim.getsockname()[1]
    finally:
        claim.close()
    env = dict(
        os.environ,
        PORT=str(web_port),
        HOSTNAME="127.0.0.1",
        # Only relevant when the bundle was built without the fixed relay
        # destination (dev form); the shipped bundle has the relay baked in.
        MANGAFLOW_API_ORIGIN=f"http://127.0.0.1:{WEB_RELAY_PORT}",
        NODE_ENV="production",
    )
    try:
        # Unix: die with the helper exactly like the helper itself does
        # (PR_SET_PDEATHSIG before the first instruction). Windows needs no
        # equivalent here — node inherits the root Job through the helper.
        preexec = None
        if sys.platform != "win32":
            def _pdeathsig() -> None:  # pragma: no cover - runs in forked child
                import ctypes

                ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGKILL, 0, 0, 0)

            preexec = _pdeathsig
        node_process = subprocess.Popen(
            [node, str(server_js)],
            stdin=subprocess.DEVNULL,
            # stdout/stderr land in the helper's stderr stream (which the
            # shell already redirects to helper-<token>.stderr.log) so web
            # boot failures are diagnosable in the unified logs.
            stdout=sys.stderr,
            stderr=sys.stderr,
            env=env,
            cwd=str(server_js.parent),
            preexec_fn=preexec,
        )
    except OSError as error:
        _log(f"node spawn failed: {error!r}; starting without the web server")
        relay.close()
        return None, None, None
    threading.Thread(
        target=_serve_relay,
        args=(relay, api_port),
        name="mangaflow-web-relay",
        daemon=True,
    ).start()
    # The relay handle travels with the result: the caller closes it when the
    # web boot fails (a degraded session must not squat on the fixed port) and
    # on every helper exit path.
    return node_process, web_port, relay


WEB_BOOT_TIMEOUT_SECONDS = 10.0
# Mid-session death detection cadence (ADR §4.5 scope note, 2026-09-08: 0.2.x
# delivers DETECTION + clean teardown + manual reconnect; automatic restart is
# out of scope). The WPF leg's 250ms budget is a UX-banner latency contract;
# here the detection output is a forensic log line, so the cadence only bounds
# how late the evidence appears in the unified logs.
WEB_EXIT_WATCH_INTERVAL_SECONDS = 0.25


def _start_web_exit_watch(node: subprocess.Popen, shutdown: threading.Event) -> threading.Thread:
    """Detect the plan-B web server dying mid-session and log it.

    Boot verification (#271) proves node owns its port at READY time; nothing
    watched it afterwards, so a mid-session crash left the WebView pointed at
    a dead origin with only node's own crash output (if any) in the unified
    logs. This watcher adds the ADR's DETECTION half for the Tauri leg: a
    daemon thread polls the child and, when it exits WITHOUT a helper
    shutdown in progress, records the exit code as a forensic milestone. It
    never restarts anything (out of 0.2.x scope) and never blocks shutdown —
    the helper's finally sets the event first, so cooperative and escalation
    stops stay silent.
    """

    def _watch() -> None:
        while not shutdown.is_set():
            code = node.poll()
            if code is not None:
                _log(
                    f"web server exited mid-session (code {code}); the plan-B "
                    "web origin is dead - restart the app to recover"
                )
                return
            time.sleep(WEB_EXIT_WATCH_INTERVAL_SECONDS)

    thread = threading.Thread(target=_watch, name="mangaflow-web-exit-watch", daemon=True)
    thread.start()
    return thread


def _await_web_server(
    node: subprocess.Popen, web_port: int
) -> tuple[subprocess.Popen | None, int | None]:
    """Verify the web server is alive AND accepting on its announced port.

    The spawn is not proof of ownership: between the claim-port close and
    node's own bind, the port is free (the documented bind-close race), and
    any boot crash frees it for good. Publishing ``web_origin`` for a port
    nobody owns hands the WebView — with the injected API origin and the
    unauthenticated loopback API behind it — to whichever local process
    claims the port instead. The dual check closes the pair of races: a
    dead node fails ``poll()`` (its port-claim died with it), and a claimed
    port must ANSWER while node is still alive; a final ``poll()`` after the
    connect catches the case where a hijacker's bind evicted node and the
    exit status has not been reaped yet.
    """

    deadline = time.monotonic() + WEB_BOOT_TIMEOUT_SECONDS
    while True:
        if node.poll() is not None:
            _log(
                f"web server exited during boot (code {node.returncode}); "
                "continuing without the web server"
            )
            return None, None
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.settimeout(1.0)
        try:
            answering = probe.connect_ex(("127.0.0.1", web_port)) == 0
        except OSError:
            answering = False
        finally:
            probe.close()
        if answering and node.poll() is None:
            return node, web_port
        if time.monotonic() >= deadline:
            break
        time.sleep(0.2)
    _log(
        f"web server did not accept on 127.0.0.1:{web_port} within "
        f"{WEB_BOOT_TIMEOUT_SECONDS:.0f}s; continuing without the web server"
    )
    node.terminate()
    try:
        node.wait(timeout=5)
    except subprocess.TimeoutExpired:
        node.kill()
    return None, None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("stub", "app"))
    parser.add_argument("--grandchild", action="store_true", help="stub: spawn a test descendant")
    parser.add_argument("--api-root", help="app: path to apps/api")
    parser.add_argument("--user-data", help="app: user data directory (data/storage/uploads live here)")
    parser.add_argument("--fake-channel", action="store_true", help="app: install the fake model channel")
    parser.add_argument(
        "--web-origin",
        default="http://tauri.localhost",
        help="app: WebView origin allowed by API CORS",
    )
    parser.add_argument(
        "--web-dist",
        help="app: Next standalone bundle dir (server.js); omit to run without a web server",
    )
    args = parser.parse_args()

    token, journal = _read_context()
    record = {
        "version": 1,
        "token": token,
        "role": args.mode,
        "state": "created",
        "started_at": int(time.time()),
    }

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    if sys.platform != "win32" and os.getpid() != os.getpgid(0):
        # Own session so the shell can signal the whole tree via -pgid.
        # Already a group leader (e.g. spawned with start_new_session) is fine.
        os.setsid()

    try:
        if args.mode == "stub":
            return _run_stub(journal, record, args.grandchild)
        return _run_app(args, journal, record)
    except SystemExit as exit_request:
        return int(exit_request.code or 0)
    except BaseException as error:  # noqa: BLE001 - last-resort failure journal
        record.update(state="failed", error=type(error).__name__)
        try:
            _write_journal(journal, record)
        except OSError:
            pass
        _log(f"helper failed: {error!r}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
