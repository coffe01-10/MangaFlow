"""MangaFlow V02-53B disposable PoC sidecar helper (Tauri 2 prior art).

Implements the frozen ADR startup protocol (`docs/adr/v02-desktop-shell-evaluation.md`
§4.2/§4.4) in the form the desktop shell consumes:

1. The shell creates the runtime directory ``mangaflow-poc-<token>`` and spawns
   this helper as its direct child with ``MANGAFLOW_POC_TOKEN`` and
   ``MANGAFLOW_POC_JOURNAL`` in the environment.
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
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TOKEN_RE = re.compile(r"[0-9a-f]{32}")
GO_PREFIX = "MANGAFLOW_GO "
EXIT_HANDSHAKE_REFUSED = 75


def _log(message: str) -> None:
    print(f"[poc-helper] {message}", file=sys.stderr, flush=True)


def _read_context() -> tuple[str, Path]:
    token = os.environ.get("MANGAFLOW_POC_TOKEN", "")
    journal = Path(os.environ.get("MANGAFLOW_POC_JOURNAL", ""))
    if TOKEN_RE.fullmatch(token) is None:
        raise ValueError("invalid process ownership token")
    if journal.is_symlink() or not journal.is_absolute():
        raise ValueError("process journal must be an absolute real path")
    directory = journal.parent
    if directory.name != f"mangaflow-poc-{token}":
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
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    return sock


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


class _StubHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        if self.path != "/api/v1/health":
            self.send_error(404)
            return
        body = b'{"status":"ok","channel":"poc-stub"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:  # keep stdout to the protocol only
        return


def _run_stub(journal: Path, record: dict, grandchild: bool) -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
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

    sock = _bind_loopback()
    port = sock.getsockname()[1]
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
        sock.close()
        raise

    if args.fake_channel:
        from poc_fake_channel import install  # provided next to this helper

        install()

    import uvicorn

    record.update(
        state="ready",
        pid=os.getpid(),
        pid_starttime=_pid_starttime(),
        api_origin=f"http://127.0.0.1:{port}",
        port=port,
    )
    _write_journal(journal, record)
    print(f"MANGAFLOW_READY {json.dumps({k: record[k] for k in ('token', 'pid', 'api_origin')})}", flush=True)

    if not _await_go(record["token"]):
        sock.close()
        return EXIT_HANDSHAKE_REFUSED

    from app.main import app

    config = uvicorn.Config(app, log_level="warning", access_log=False, lifespan="on")
    server = uvicorn.Server(config)
    server.run(sockets=[sock])
    return 0


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
