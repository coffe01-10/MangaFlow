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
import errno
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import typing
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
    # Drop the handshake secrets from the helper's own environment: the
    # long-lived server (and every subprocess it spawns later - the CLI
    # channel's children inherit os.environ) must not carry the ownership
    # token or the journal path for its whole lifetime. The validated local
    # variables above are the single source from here on; the journal is
    # rewritten by path, not by re-reading the environment.
    os.environ.pop("MANGAFLOW_DESKTOP_TOKEN", None)
    os.environ.pop("MANGAFLOW_DESKTOP_JOURNAL", None)
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

    @property
    def max_connections(self) -> int:
        """The cap this limiter actually enforces (its construction
        snapshot), so logs can never disagree with enforcement."""
        return self._max


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
    log_cooldown = float("-inf")
    # Transient accept errors that must be retried instead of killing the
    # relay (accept(2): pending network errors surface here; EMFILE/ENOBUFS/
    # ENOMEM under fd/memory pressure). Terminal errors: EBADF/ENOTSOCK/
    # EINVAL - the listener was closed by a helper exit path.
    transient_accept_errors = {
        errno.ECONNABORTED,
        errno.EPROTO,
        errno.EMFILE,
        errno.ENFILE,
        errno.ENOBUFS,
        errno.ENOMEM,
        errno.ENETDOWN,
        errno.ENETUNREACH,
    }
    while True:
        try:
            client, _ = relay.accept()
        except socket.timeout:
            continue
        except OSError as error:
            if error.errno not in transient_accept_errors:
                # Terminal (the listener was closed by a helper exit
                # path, or a state nothing here can fix). Never die
                # silently: one line before exiting keeps the failure
                # diagnosable in the unified logs.
                _log(f"relay listener exiting on {error!r}")
                return
            continue
        if not limiter.try_acquire():
            # Rate-limited: the stderr log only rotates across sessions,
            # so one line per refused connection under a refuse-flood
            # would grow it for the rest of the session. Log the entry
            # into saturation once per cooldown instead of every refusal
            # (the enforced cap is the limiter's snapshot, not the
            # module global).
            now = time.monotonic()
            if now >= log_cooldown:
                _log(
                    f"relay connection limit {limiter.max_connections} "
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
            try:
                # Thread CONSTRUCTION can fail under the same host-wide
                # pressure as start() (MemoryError from the allocation), so
                # the whole lifecycle sits inside the guard.
                pumps = [
                    threading.Thread(
                        target=_pipe, args=(pair_client, pair_upstream), daemon=True
                    ),
                    threading.Thread(
                        target=_pipe, args=(pair_upstream, pair_client), daemon=True
                    ),
                ]
                try:
                    started = []
                    for pump in pumps:
                        try:
                            pump.start()
                            started.append(pump)
                        except (RuntimeError, MemoryError):
                            # A partial start leaves earlier pumps running
                            # against sockets the finally is about to
                            # close: SHUT_RDWR both now so the survivors'
                            # next recv/sendall fails and they exit
                            # deterministically instead of lingering on
                            # closed fds.
                            for sock in (pair_client, pair_upstream):
                                try:
                                    sock.shutdown(socket.SHUT_RDWR)
                                except OSError:
                                    pass
                            raise
                    for pump in started:
                        pump.join()
                except (RuntimeError, MemoryError):
                    pass
            except (RuntimeError, MemoryError):
                # Construction or start failed under host-wide pressure (OS
                # thread/memory limits - not our own counter, which the cap
                # bounds). Swallowed: the finally below closes the pair and
                # releases the slot, and the accept loop is untouched.
                pass
            finally:
                # Every path out of _pump - normal completion, start failure,
                # construction failure - closes the pair deterministically
                # (never refcount timing) and releases the slot. No path may
                # leave either behind.
                for sock in (pair_client, pair_upstream):
                    try:
                        sock.close()
                    except OSError:
                        pass
                limiter.release()

        try:
            spawn = threading.Thread(
                target=_pump,
                args=(client, upstream),
                name="mangaflow-web-relay-pipe",
                daemon=True,
            )
            spawn.start()
        except (RuntimeError, MemoryError):
            # Same host-wide failure, one level up (start() raises
            # RuntimeError; construction itself can raise MemoryError) -
            # without this guard the exception would kill the ACCEPT loop
            # while 39443 stays bound, leaving backlog clients hanging for
            # the rest of the session.
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


def _validate_api_root(api_root: Path) -> str | None:
    """Return a rejection reason for an unusable --api-root tree, else None.

    --api-root is unvalidated shell input that becomes sys.path[0]: a wrong
    or hostile tree can shadow the helper's own modules (fake_channel) and
    hijack alembic's env.py before the API ever imports (#314). Marker files
    are checked instead of trusting the path string.
    """
    if not (api_root / "alembic.ini").is_file():
        return "api-root/missing-alembic-ini"
    if not (api_root / "app" / "main.py").is_file():
        return "api-root/missing-app-main"
    # Platform matrix (round-1 review F2): NTFS `is_file()` matching is
    # itself case-insensitive, so the byte-exact probe already rejects case
    # variants on Windows; POSIX imports are case-sensitive by default
    # (PYTHONCASEOK relaxes it). The lowercase scan is strictness plus
    # coverage for case-sensitive volumes under relaxed matching.
    # Fail closed on enumeration failure: a traverse-only (0o111) root
    # passes the marker `is_file()` checks above but cannot be listed -
    # fall back to the byte-exact probe (stat works through +x) instead of
    # accepting (round-1 review F1: the bare `set()` fallback was
    # fail-OPEN, probe-verified).
    try:
        names = {entry.name.lower() for entry in api_root.iterdir()}
    except OSError:
        # Same set the scan would have produced, via byte-exact existence
        # probes (stat works through +x): a traverse-only root must fail
        # closed on ANY of the shadow names, not just fake_channel (R3
        # review).
        names = set()
        for probe in ("fake_channel.py", "fake_channel",
                      "alembic.py", "alembic", "uvicorn.py", "uvicorn"):
            if (api_root / probe).exists():
                names.add(probe)
    # A directory named `fake_channel` shadows the helper's module too:
    # Python 3 imports namespace packages without __init__.py, so the bare
    # directory form hijacks `from fake_channel import install` exactly like
    # the file form.
    if "fake_channel.py" in names or "fake_channel" in names:
        return "api-root/shadowing-fake-channel"
    # Same shadowing class for the modules imported AFTER sys.path.insert(0):
    # a root-level alembic.py/alembic/ or uvicorn.py/uvicorn/ would be
    # imported in place of the venv's real packages (alembic's command module
    # and uvicorn's server both drive this helper). The real apps/api tree
    # has none of these at its root (its migrations live in migrations/,
    # reached via alembic.ini), so the check is safe for it (#314).
    for shadow in ("alembic.py", "alembic", "uvicorn.py", "uvicorn"):
        if shadow in names:
            return f"api-root/shadowing-{shadow}"
    return None


def _apply_app_environment(user_data: Path, web_origin: str) -> None:
    # Force-set, not setdefault: an inherited MANGAFLOW_DISABLE_DOTENV=0 would
    # re-enable .env loading relative to the helper's CWD on every start
    # (#313) — the shell owns this environment, the surrounding machine does
    # not. Siblings below are already unconditional for the same reason.
    os.environ["MANGAFLOW_DISABLE_DOTENV"] = "1"
    os.environ["DATABASE_URL"] = f"sqlite:///{user_data / 'data' / 'mangaflow.db'}"
    os.environ["STORAGE_ROOT"] = str(user_data / "storage")
    os.environ["UPLOAD_ROOT"] = str(user_data / "uploads")
    os.environ["WEB_ORIGIN"] = web_origin


def set_sqlalchemy_url(config, url: str) -> None:
    """Set sqlalchemy.url through Alembic's ConfigParser-backed config.

    set_main_option routes the value through ConfigParser interpolation,
    where a bare ``%`` introduces a ``%(name)s`` substitution — a ``%`` in
    the user-data path (a ``100%`` username is a legal Windows name) made
    ``command.upgrade`` die with an interpolation error before the API ever
    started (#443). Alembic's documented escape is doubling it; reading the
    option back resolves to the original URL, so the escaped form never
    reaches SQLAlchemy.
    """

    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))


def _run_app(args: argparse.Namespace, journal: Path, record: dict) -> int:
    api_root = Path(args.api_root).resolve()
    rejection = _validate_api_root(api_root)
    if rejection is not None:
        # Fail closed through the journal before any import side effect: the
        # tree never reaches sys.path, alembic, or uvicorn.
        record.update(state="failed", error=rejection)
        _write_journal(journal, record)
        _log(f"api root rejected: {rejection}")
        return 1
    sys.path.insert(0, str(api_root))
    user_data = Path(args.user_data).resolve()
    # The shell supplies the user-data root; the helper lays out the
    # database directory under it (ADR §4.1 install-form discipline).
    (user_data / "data").mkdir(parents=True, exist_ok=True)
    _apply_app_environment(user_data, args.web_origin)

    sock = None
    web: WebServer | None = None
    web_shutdown = threading.Event()
    try:
        sock = _bind_loopback()
        port = sock.getsockname()[1]
        # The web server's compiled rewrites target this exact API origin, so
        # spawn it only after the API port is bound (plan B, W-15).
        web = _spawn_web_server(args, port)
        try:
            from alembic import command
            from alembic.config import Config as AlembicConfig

            alembic_config = AlembicConfig(str(api_root / "alembic.ini"))
            set_sqlalchemy_url(alembic_config, os.environ["DATABASE_URL"])
            command.upgrade(alembic_config, "head")
        except BaseException as error:  # noqa: BLE001 - journal the failure, then exit
            record.update(state="failed", error=f"alembic:{type(error).__name__}")
            _write_journal(journal, record)
            raise

        if args.fake_channel:
            from fake_channel import install  # provided next to this helper

            install()

        import uvicorn

        if web is not None:
            # Red team 2026-09-08/09: publishing web_origin requires a web
            # server that provably booted. The announced port is already
            # helper-owned (exclusive on Windows), so a boot failure can no
            # longer hand the announced origin to a hijacker — but a node
            # that never comes up must still fail the session closed to the
            # static-export form instead of serving a dead relay.
            if not _await_web_server_boot(web.process, web.node_port):
                web.close()
                web = None
            else:
                # Ownership of the announced origin now includes noticing
                # when it dies: arm the mid-session exit watch (log-only,
                # ADR §4.5 detection scope).
                _start_web_exit_watch(web.process, web_shutdown)

        record.update(
            state="ready",
            pid=os.getpid(),
            pid_starttime=_pid_starttime(),
            api_origin=f"http://127.0.0.1:{port}",
            port=port,
        )
        if web is not None:
            # Plan B (W-15): the web server is a helper child (Job member,
            # killed with the tree) serving the Next standalone bundle; the
            # shell builds the WebView against this loopback origin only.
            record.update(
                web_origin=f"http://127.0.0.1:{web.announced_port}",
                web_port=web.announced_port,
            )
        _write_journal(journal, record)
        ready_fields = ["token", "pid", "api_origin"]
        if web is not None:
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
        if web is not None:
            # Releases the child (terminate/wait/kill with a final reap) and
            # both session-owned sockets — the announced web port (helper
            # property since before node spawned) and the fixed relay port.
            web.close()
        if sock is not None:
            sock.close()


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


def _node_child_env() -> dict[str, str]:
    """Parent env for the node child, minus secrets and auto-load hooks.

    The full parent env used to ride along, including
    ``MANGAFLOW_DESKTOP_TOKEN``/``_JOURNAL`` (the handshake secret and
    journal path), the desktop orchestration names
    (``MANGAFLOW_DESKTOP_HELPER``/``_API_ROOT``/``_USER_DATA``/
    ``_FAKE_CHANNEL``/``_WEB_DIST``/``_PYTHON`` — the web-facing child has
    no use for any of them), and any ``NODE_OPTIONS``/``NODE_PATH`` (which
    node auto-applies). The child needs the parent env for PATH and
    friends, but not the handshake identity, the launcher's wiring, nor
    injectable hooks (red team 2026-09-09, #312). Callers add
    PORT/HOSTNAME/MANGAFLOW_API_ORIGIN/NODE_ENV on top.
    """

    env = dict(os.environ)
    for name in (
        "MANGAFLOW_DESKTOP_TOKEN",
        "MANGAFLOW_DESKTOP_JOURNAL",
        "MANGAFLOW_DESKTOP_HELPER",
        "MANGAFLOW_DESKTOP_API_ROOT",
        "MANGAFLOW_DESKTOP_USER_DATA",
        "MANGAFLOW_DESKTOP_FAKE_CHANNEL",
        "MANGAFLOW_DESKTOP_WEB_DIST",
        "MANGAFLOW_DESKTOP_PYTHON",
        "NODE_OPTIONS",
        "NODE_PATH",
    ):
        env.pop(name, None)
    return env


def _spawn_web_server(args: argparse.Namespace, api_port: int) -> WebServer | None:
    """Start the Next standalone server (plan B, W-15) as a helper child.

    --web-dist must point at the standalone bundle directory (containing
    server.js). The helper OWNS the announced web port: it binds a loopback
    port itself — Windows with ``SO_EXCLUSIVEADDRUSE``, POSIX with
    ``SO_REUSEADDR`` — before node is spawned, and relays it byte-for-byte
    to node's own ephemeral port. The WebView therefore only ever touches a
    socket the helper has held since before the spawn: neither the
    historical claim-close-spawn window nor a Windows co-bind (libuv sets
    no socket option on node's own bind, so a ``SO_REUSEADDR`` binder could
    share the port while node stays alive — red team 2026-09-09) can put
    foreign content behind the ANNOUNCED port itself.

    node's own ephemeral port keeps two residuals that #301 narrowed but
    cannot close from the helper side (see the threat model): the
    claim-close-spawn race can still kill node (fail-closed downgrade via
    :func:`_await_web_server_boot`), and on Windows a ``SO_REUSEADDR``
    binder can co-bind node's port while node lives — the boot check's
    final ``poll()`` narrows that window to milliseconds but a same-user
    co-binder that survives it mixes relayed traffic (netstat-
    discoverable; nothing navigates to node's port).

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
        return None
    dist_path = Path(web_dist).resolve()
    node = _find_node(dist_path)
    if node is None:
        _log("no node runtime found; starting without the web server")
        return None
    server_js = dist_path / "server.js"
    if not server_js.is_file():
        _log(f"web dist {server_js} has no server.js; starting without the web server")
        return None
    relay = _bind_relay(api_port)
    if relay is None:
        return None
    web_sock = _bind_web_port()
    if web_sock is None:
        # Guarded (E3-F2): a secondary close failure must not escalate a
        # downgrade into session death.
        try:
            relay.close()
        except OSError:
            pass
        return None
    announced_port = web_sock.getsockname()[1]
    try:
        claim = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            claim.bind(("127.0.0.1", 0))
            node_port = claim.getsockname()[1]
        finally:
            claim.close()
    except OSError as error:
        # A failed node-port claim (fd exhaustion, socket stash full) is a
        # downgrade, not a session death: every other spawn-stage failure
        # downgrades, and this one is equally recoverable (R2 review
        # 2026-09-09, N5). The sockets opened above are released here.
        _log(f"node port claim failed ({error!r}); starting without the web server")
        # Guarded closes (same shape as WebServer.close): a secondary close
        # failure must not skip the second socket or mask the downgrade.
        for sock in (web_sock, relay):
            try:
                sock.close()
            except OSError:
                pass
        return None
    env = _node_child_env()
    env.update(
        PORT=str(node_port),
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
        # Guarded (E3-F2): consistent with the claim-failure path above.
        for sock in (web_sock, relay):
            try:
                sock.close()
            except OSError:
                pass
        return None
    # Two byte-pipe relays, both loopback-only and helper-lifetime-bound:
    # announced web port -> node's ephemeral port (this redesign), and the
    # fixed relay port -> the API (build-time rewrite destination). A start
    # failure here (thread exhaustion) must not orphan a live node without
    # a handle: reap it and release both sockets (E3-F3).
    try:
        threading.Thread(
            target=_serve_relay,
            args=(web_sock, node_port),
            name="mangaflow-web-announced",
            daemon=True,
        ).start()
        threading.Thread(
            target=_serve_relay,
            args=(relay, api_port),
            name="mangaflow-web-relay",
            daemon=True,
        ).start()
    except Exception as error:  # noqa: BLE001 - downgrade, not session death
        _log(f"relay thread start failed ({error!r}); starting without the web server")
        node_process.terminate()
        try:
            node_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            node_process.kill()
            # Reap the killed child for symmetry with WebServer.close: a
            # lingering zombie holds the pid and reads as a phantom
            # "running" node (E4 review).
            try:
                node_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        for sock in (web_sock, relay):
            try:
                sock.close()
            except OSError:
                pass
        return None
    return WebServer(
        process=node_process,
        announced_port=announced_port,
        node_port=node_port,
        web_sock=web_sock,
        relay=relay,
    )


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
                # A deliberate stop sets the shutdown event BEFORE close()
                # reaps the child, so a code observed on the wrong side of
                # that race gets one watch interval to settle: a genuine
                # mid-session crash leaves the event unset (R2 review
                # 2026-09-09, N4 — the old check-then-act order logged a
                # spurious crash line on every deliberate stop).
                if shutdown.wait(WEB_EXIT_WATCH_INTERVAL_SECONDS):
                    return
                _log(
                    f"web server exited mid-session (code {code}); the plan-B "
                    "web origin is dead - restart the app to recover"
                )
                return
            time.sleep(WEB_EXIT_WATCH_INTERVAL_SECONDS)

    thread = threading.Thread(target=_watch, name="mangaflow-web-exit-watch", daemon=True)
    thread.start()
    return thread


class WebServer(typing.NamedTuple):
    """Session-owned plan-B web resources (all released by :meth:`close`).

    ``announced_port`` is the helper-owned socket the WebView loads — held
    by the helper since before node spawned, so no local process can put
    foreign content behind it. ``node_port`` is node's own ephemeral bind,
    reachable only through the helper's byte relay.
    """

    process: subprocess.Popen
    announced_port: int
    node_port: int
    web_sock: socket.socket
    relay: socket.socket

    def close(self) -> None:
        """Release every session-owned resource: the child, both sockets."""
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                # Reap the killed child promptly: a lingering zombie holds
                # the pid and reads as a phantom "running" node.
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        for sock in (self.web_sock, self.relay):
            try:
                sock.close()
            except OSError:
                pass


def _bind_web_port() -> socket.socket | None:
    """Bind the announced web port (dynamic, loopback) for the helper's
    exclusive session-long use; None when no port can be claimed.

    Windows: ``SO_EXCLUSIVEADDRUSE`` — libuv (node's runtime) sets no socket
    option on its own binds, so on that platform a ``SO_REUSEADDR`` binder
    can share a port with an option-less listener while the listener stays
    alive; the helper's own socket forecloses that for the one port the
    WebView actually loads (red team 2026-09-09). POSIX: ``SO_REUSEADDR``
    covers the session-relaunch TIME_WAIT case, same policy as the fixed
    relay port.
    """

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if sys.platform == "win32":
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    else:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("127.0.0.1", 0))
        sock.listen(128)
    except OSError as error:
        _log(f"web port bind failed ({error!r}); starting without the web server")
        sock.close()
        return None
    return sock


def _await_web_server_boot(node: subprocess.Popen, node_port: int) -> bool:
    """Verify the web server is alive AND accepting on its own port.

    The spawn is not proof of life: node can die at boot (its ephemeral
    port lost to a bind-close race winner, or any crash). With the
    helper-owned announced port the failure mode is availability only —
    the caller downgrades to the static export — never a hijack, because
    nothing navigates to node's port and the announced socket cannot leave
    the helper's hands. A final ``poll()`` after the connect keeps the
    verdict from riding a node that died while the probe was in flight.
    """

    deadline = time.monotonic() + WEB_BOOT_TIMEOUT_SECONDS
    while True:
        if node.poll() is not None:
            _log(
                f"web server exited during boot (code {node.returncode}); "
                "continuing without the web server"
            )
            return False
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.settimeout(1.0)
        try:
            answering = probe.connect_ex(("127.0.0.1", node_port)) == 0
        except OSError:
            answering = False
        finally:
            probe.close()
        if answering and node.poll() is None:
            return True
        if time.monotonic() >= deadline:
            break
        time.sleep(0.2)
    _log(
        f"web server did not accept on 127.0.0.1:{node_port} within "
        f"{WEB_BOOT_TIMEOUT_SECONDS:.0f}s; continuing without the web server"
    )
    return False


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
