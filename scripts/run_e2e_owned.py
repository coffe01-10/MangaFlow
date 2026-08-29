"""Run Playwright/performance inside one owned Windows process tree.

No services are reused. All descendants exit before runtime deletion. A failed
cleanup retains its journal and returns nonzero; --recover only handles dead
controllers and verified stopped Job Objects, never a raw PID.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_runtime import assigned_runtime, new_runtime, verify_owned_listener  # noqa: E402
from owned_processes import recover_stopped_tree  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
# The wrapper and its Node/API/browser descendants all inherit the owned job.
CHILD = """
import json, pathlib, subprocess, sys
commands, cwd, log, result = json.loads(sys.argv[1])
with open(log, "wb") as stream:
    for command in commands:
        code = subprocess.call(command, cwd=cwd, stdout=stream, stderr=stream)
        if code:
            break
pathlib.Path(result).write_text(json.dumps({"exit_code": code}), encoding="utf-8")
raise SystemExit(code)
"""


def assert_ports_free() -> None:
    for port in (3000, 8000):
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                probe.bind(("127.0.0.1", port))
            except OSError as exc:
                raise RuntimeError(f"Port {port} is unavailable; refusing unknown service") from exc



def forward_log_chunk(chunk: bytes, *, stdout=None) -> None:
    """Forward mixed-encoding child output without a lossy text round-trip."""
    target = stdout or sys.stdout
    target.buffer.write(chunk)
    target.buffer.flush()


def child_environment(runtime, *, node: str) -> dict[str, str]:
    # Do not inherit application secrets, proxies, Python/Node options or dotenv.
    allowed = (
        "PATH",
        "PATHEXT",
        "USERPROFILE",
        "LOCALAPPDATA",
        "APPDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PROGRAMW6432",
        "COMSPEC",
    )
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    env.update(
        MANGAFLOW_E2E_RUN_ID=runtime.run_id,
        MANGAFLOW_E2E_RUNTIME=str(runtime.path),
        MANGAFLOW_PYTHON=sys.executable,
        MANGAFLOW_NODE=node,
        MANGAFLOW_DISABLE_DOTENV="1",
        MANGAFLOW_E2E_SEED="1",
        DATABASE_URL="sqlite:///" + (runtime.path / "offline" / "app.sqlite").as_posix(),
        STORAGE_ROOT=str(runtime.path / "offline" / "storage"),
        UPLOAD_ROOT=str(runtime.path / "offline" / "uploads"),
        # Queue isolation is per-service (serve_e2e_api.py); a controller-wide
        # QUEUE_ENABLED leaks into `npm run check` pytest and fakes queue faults.
        PYTHONDONTWRITEBYTECODE="1",
        MANGAFLOW_SHA=os.environ.get("MANGAFLOW_SHA", ""),
        NEXT_TELEMETRY_DISABLED="1",
        NODE_OPTIONS='--require "' + (ROOT / "scripts/e2e_node_bootstrap.cjs").as_posix() + '"',
    )
    return env


def finish(runtime, summary: dict, report: Path, *, log: Path | None = None) -> int:
    # A stop failure must never fall through to directory deletion.
    try:
        runtime.tree.stop()
        summary["process_tree_stopped"] = True
        if log is not None and log.exists():
            shutil.copyfile(log, report.with_suffix(".log"))
        runtime.cleanup()
        summary["runtime_removed"] = True
    except BaseException as exc:
        summary["errors"].append(f"cleanup: {type(exc).__name__}: {exc}")
        summary["runtime_removed"] = False
    summary["finished_at"] = time.time()
    report.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return 1 if summary["errors"] else 0


def commands_for(mode: str, node: str) -> list[list[str]]:
    if mode in {"playwright", "full"}:
        playwright = [
            node,
            str(ROOT / "node_modules" / "@playwright" / "test" / "cli.js"),
            "test",
        ]
        if mode == "full":
            npm = Path(node).parent / "node_modules" / "npm" / "bin" / "npm-cli.js"
            if not npm.is_file():
                raise RuntimeError("Installed npm-cli.js required next to Node")
            commands = [[node, str(npm), "run", "check"], playwright]
        else:
            commands = [
                [node, str(ROOT / "node_modules/next/dist/bin/next"), "build", "apps/web"],
                playwright,
            ]
    else:
        commands = [[node, str(ROOT / "scripts" / "run_phase2_performance.mjs")]]
    return commands


def run(mode: str, timeout: float = 2400) -> int:
    assert_ports_free()
    node = shutil.which(os.environ.get("MANGAFLOW_NODE", "node"))
    if not node:
        raise RuntimeError("Existing Node executable required; no automatic installation")
    runtime = new_runtime(Path(tempfile.gettempdir()))
    report_dir = ROOT / "output" / "playwright" / ("owned-" + runtime.run_id)
    log = runtime.tree.payload / "runner.log"
    result = runtime.tree.payload / "result.json"
    summary = {
        "run_id": runtime.run_id,
        "mode": mode,
        "started_at": time.time(),
        "directory": str(runtime.tree.directory),
        "errors": [],
        "process_tree_stopped": False,
        "runtime_removed": False,
    }
    report = report_dir / "summary.json"
    try:
        report_dir.mkdir(parents=True, exist_ok=False)
    except BaseException:
        runtime.cleanup()
        raise
    try:
        (runtime.path / "offline").mkdir(exist_ok=False)
        commands = commands_for(mode, node)
        child = runtime.tree.start_python(
            "browser-acceptance",
            CHILD,
            [json.dumps([commands, str(ROOT), str(log), str(result)])],
            environment=child_environment(runtime, node=node),
        )
        offset = 0
        deadline = time.monotonic() + timeout
        while child.poll() is None:
            if time.monotonic() >= deadline:
                raise TimeoutError("Browser acceptance exceeded controller timeout")
            if log.exists():
                with log.open("rb") as stream:
                    stream.seek(offset)
                    chunk = stream.read()
                    offset = stream.tell()
                if chunk:
                    # The child log can contain output encoded with the active
                    # Windows code page as well as UTF-8 from Node. Decoding it
                    # here and re-encoding through a different parent console
                    # can raise UnicodeEncodeError on non-ASCII repo paths.
                    # Forward bytes unchanged; the outer terminal owns display.
                    forward_log_chunk(chunk)
            time.sleep(0.1)
        code = child.wait()
        if not result.exists():
            raise RuntimeError(f"Acceptance runner exited {code} without its result")
        summary["exit_code"] = json.loads(result.read_text(encoding="utf-8"))["exit_code"]
        if code != summary["exit_code"] or code != 0:
            summary["errors"].append(f"Acceptance process failed: {code}")
    except BaseException as exc:
        summary["errors"].append(f"run: {type(exc).__name__}: {exc}")
    finally:
        code = finish(runtime, summary, report, log=log)
    print(f"OWNED_ACCEPTANCE_REPORT={report}", flush=True)
    return code


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("playwright", "full", "performance", "verify", "recover"))
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--token")
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    if args.mode == "verify":
        runtime = assigned_runtime()
        info = {"runId": runtime.run_id, "runtime": str(runtime.path)}
        if args.port is not None:
            info["listener_pids"] = verify_owned_listener(runtime, args.port)
        print(json.dumps(info))
        return 0
    if args.mode == "recover":
        if not args.directory or not args.token:
            parser.error("recover requires the exact owned directory and token")
        recover_stopped_tree(args.directory, args.token)
        return 0
    return run(args.mode)


if __name__ == "__main__":
    raise SystemExit(main())
