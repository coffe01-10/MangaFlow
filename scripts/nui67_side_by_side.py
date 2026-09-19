"""NUI-6/7 side-by-side acceptance environment orchestrator.

Starts the web+API dev stack against an ISOLATED scratch database (never the
user's storage/mangaflow.db), seeds an identical fixed dataset into both the
web database+storage and the WPF client's user-data database+storage, and
launches the WPF native client. Children are owned through NAMED Windows Job
Objects (no kill-on-close: the `start` invocation must exit while the stack
stays up; `stop` reopens the jobs by name, terminates them, and verifies the
ports and process table came clean — any leftover is REPORTED, never killed
blind, because a pid or image name alone does not prove ownership).

Commands:
  start  [--run-dir NAME] [--no-seed]
  stop   [--clean]
  status

State file: output/nui67-acceptance/session.json (absolute paths recorded).
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wintypes
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib import request as urlrequest
from urllib.error import URLError

REPO = Path(__file__).resolve().parents[1]
STATE = REPO / "output" / "nui67-acceptance" / "session.json"
EVIDENCE = REPO / "output" / "nui67-acceptance"

WEB_PORT = 3000
API_PORT = 8000
NATIVE_EXE = REPO / "apps" / "desktop" / "native" / "bin" / "Release" / "net8.0-windows" / "MangaFlow.Native.exe"

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)

_JOB_ALL_ACCESS = 0x1F0019
_PROCESS_ALL_ACCESS = 0x1F0FFF
_SYNCHRONIZE = 0x00100000
_CREATE_SUSPENDED = 0x00000004
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_INFINITE = 0xFFFFFFFF
_WM_CLOSE = 0x0010
_STILL_ACTIVE = 259


class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.c_void_p),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


class OwnedProcess:
    """A suspended-created child assigned to a named job before its first
    instruction; kept across orchestrator invocations via the job name."""

    def __init__(self, job_name: str, command_line: str, env: dict[str, str], log_path: Path) -> None:
        self.job_name = job_name
        self.job = kernel32.CreateJobObjectW(None, job_name)
        if not self.job:
            raise OSError(f"CreateJobObjectW({job_name}) failed: {ctypes.get_last_error()}")
        log_handle = os.open(log_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT)
        nul_handle = os.open("NUL", os.O_RDONLY)

        si = _STARTUPINFOW()
        si.cb = ctypes.sizeof(si)
        si.dwFlags = 0x00000100  # STARTF_USESTDHANDLES
        si.hStdInput = wintypes.HANDLE(nul_handle)
        si.hStdOutput = wintypes.HANDLE(log_handle)
        si.hStdError = wintypes.HANDLE(log_handle)
        pi = _PROCESS_INFORMATION()
        env_block = "\0".join(f"{key}={value}" for key, value in env.items()) + "\0"
        flags = _CREATE_SUSPENDED | _CREATE_NEW_PROCESS_GROUP | 0x00000400  # CREATE_UNICODE_ENVIRONMENT
        ok = kernel32.CreateProcessW(
            None, command_line, None, None, True, flags, ctypes.c_wchar_p(env_block), str(REPO),
            ctypes.byref(si), ctypes.byref(pi),
        )
        os.close(log_handle)
        os.close(nul_handle)
        if not ok:
            kernel32.CloseHandle(self.job)
            raise OSError(f"CreateProcessW({command_line!r}) failed: {ctypes.get_last_error()}")
        if not kernel32.AssignProcessToJobObject(self.job, pi.hProcess):
            kernel32.TerminateProcess(pi.hProcess, 1)
            raise OSError(f"AssignProcessToJobObject failed: {ctypes.get_last_error()}")
        if kernel32.ResumeThread(pi.hThread) == 0xFFFFFFFF:
            kernel32.TerminateProcess(pi.hProcess, 1)
            raise OSError("ResumeThread failed")
        self.pid = pi.dwProcessId
        self.process_handle = pi.hProcess
        kernel32.CloseHandle(pi.hThread)

    def terminate_tree(self) -> None:
        kernel32.TerminateJobObject(self.job, 1)

    def __del__(self) -> None:
        if getattr(self, "job", None):
            kernel32.CloseHandle(self.job)


def open_named_job(job_name: str) -> wintypes.HANDLE:
    return kernel32.OpenJobObjectW(_JOB_ALL_ACCESS, False, job_name)


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def poll_health(url: str, timeout_seconds: int) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urlrequest.urlopen(url, timeout=3) as response:
                if response.status == 200:
                    return True
        except (URLError, OSError):
            pass
        time.sleep(1.5)
    return False


def seed_database(db_url: str, storage_root: Path, upload_root: Path | None = None) -> dict[str, str]:
    sys.path.insert(0, str(REPO / "apps" / "api"))
    sys.path.insert(0, str(REPO / "scripts"))
    os.environ["MANGAFLOW_DISABLE_DOTENV"] = "1"
    from nui67_seed import seed_fixed_dataset

    return seed_fixed_dataset(db_url, storage_root, upload_root)


def run_migrations(db_url: str) -> None:
    env = dict(os.environ)
    env["DATABASE_URL"] = db_url
    env["MANGAFLOW_DISABLE_DOTENV"] = "1"
    done = subprocess.run(
        [str(REPO / ".venv" / "Scripts" / "python.exe"), "-m", "alembic", "-c", "apps/api/alembic.ini", "upgrade", "head"],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    if done.returncode != 0:
        raise RuntimeError(f"alembic upgrade failed:\n{done.stdout}\n{done.stderr}")


def cmd_start(args: argparse.Namespace) -> int:
    if STATE.exists():
        raise SystemExit(f"session already recorded: {STATE} — run `stop` first (refusing double stack)")
    for port in (WEB_PORT, API_PORT):
        if not port_free(port):
            raise SystemExit(
                f"port {port} is occupied — identify the owner and free it before starting (never kill blindly)"
            )
    for required in (
        NATIVE_EXE,
        REPO / ".venv" / "Scripts" / "python.exe",
        REPO / ".venv-desktop" / "Scripts" / "python.exe",
    ):
        if not required.exists():
            raise SystemExit(f"missing: {required} (build via apps/desktop/scripts/start-native.ps1)")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = EVIDENCE / (args.run_dir or f"run-{stamp}")
    logs = run_dir / "logs"
    web_data = run_dir / "web-data"
    web_storage = run_dir / "web-storage"
    web_uploads = run_dir / "web-uploads"
    native_data = run_dir / "native-data"
    for directory in (logs, web_data, web_storage, web_uploads, native_data / "data", native_data / "storage", native_data / "uploads"):
        directory.mkdir(parents=True, exist_ok=True)

    web_db_url = f"sqlite:///{(web_data / 'mangaflow.db').as_posix()}"
    native_db_url = f"sqlite:///{(native_data / 'data' / 'mangaflow.db').as_posix()}"

    run_migrations(web_db_url)
    if not args.no_seed:
        seed_database(web_db_url, web_storage, web_uploads)
    run_migrations(native_db_url)
    if not args.no_seed:
        seed_database(native_db_url, native_data / "storage", native_data / "uploads")

    env = dict(os.environ)
    env.update(
        DATABASE_URL=web_db_url,
        STORAGE_ROOT=str(web_storage),
        UPLOAD_ROOT=str(web_uploads),
        ENVIRONMENT="development",
        QUEUE_ENABLED="false",
    )
    dev = OwnedProcess(
        f"mangaflow-nui67-dev-{stamp}", "cmd.exe /c npm run dev", env, logs / "dev-stack.log"
    )
    api_ok = poll_health(f"http://127.0.0.1:{API_PORT}/api/v1/health", 120)
    web_ok = poll_health(f"http://127.0.0.1:{WEB_PORT}", 240)
    if not (api_ok and web_ok):
        dev.terminate_tree()
        kernel32.CloseHandle(dev.job)
        raise SystemExit(
            f"health check failed (api={api_ok}, web={web_ok}); dev stack terminated, logs: {logs / 'dev-stack.log'}"
        )

    wpf_env = dict(os.environ)
    wpf_env["MANGAFLOW_NATIVE_REPO"] = str(REPO)
    wpf_env["MANGAFLOW_DESKTOP_USER_DATA"] = str(native_data)
    wpf = OwnedProcess(
        f"mangaflow-nui67-wpf-{stamp}", f'"{NATIVE_EXE}"', wpf_env, logs / "wpf-client.log"
    )

    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(
        json.dumps(
            {
                "run_dir": str(run_dir),
                "started_at": datetime.now().isoformat(),
                "web_url": f"http://127.0.0.1:{WEB_PORT}",
                "api_url": f"http://127.0.0.1:{API_PORT}",
                "web_db": web_db_url,
                "web_storage": str(web_storage),
                "native_data": str(native_data),
                "native_db": native_db_url,
                "dev_job": dev.job_name,
                "dev_pid": dev.pid,
                "wpf_job": wpf.job_name,
                "wpf_pid": wpf.pid,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"session started: web={WEB_PORT} api={API_PORT} dev_pid={dev.pid} wpf_pid={wpf.pid}")
    print(f"native user data: {native_data}")
    return 0


def _main_windows_of(pid: int) -> list[int]:
    result: list[int] = []

    def callback(hwnd, _lparam):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            result.append(hwnd)
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(WNDENUMPROC(callback), 0)
    return result


def _wait_pid_exit(pid: int, timeout_ms: int) -> bool:
    handle = kernel32.OpenProcess(_SYNCHRONIZE, False, pid)
    if not handle:
        return True  # already gone
    try:
        code = kernel32.WaitForSingleObject(handle, timeout_ms)
        return code == 0  # WAIT_OBJECT_0
    finally:
        kernel32.CloseHandle(handle)


def process_image_name(pid: int) -> str | None:
    handle = kernel32.OpenProcess(0x00000400 | 0x00000010, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        buf = ctypes.create_unicode_buffer(260)
        size = wintypes.DWORD(260)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return Path(buf.value).name
        return None
    finally:
        kernel32.CloseHandle(handle)


def cmd_stop(args: argparse.Namespace) -> int:
    if not STATE.exists():
        raise SystemExit("no session recorded")
    session = json.loads(STATE.read_text(encoding="utf-8"))
    problems: list[str] = []

    wpf_pid = session["wpf_pid"]
    if process_image_name(wpf_pid) == "MangaFlow.Native.exe":
        for hwnd in _main_windows_of(wpf_pid):
            user32.PostMessageW(wintypes.HWND(hwnd), _WM_CLOSE, 0, 0)
        if not _wait_pid_exit(wpf_pid, 45000):
            problems.append(f"WPF pid {wpf_pid} ignored graceful close — terminating its job")
            job = open_named_job(session["wpf_job"])
            if job:
                kernel32.TerminateJobObject(job, 1)
                kernel32.CloseHandle(job)
            _wait_pid_exit(wpf_pid, 10000)
    elif process_image_name(wpf_pid) is not None:
        problems.append(f"pid {wpf_pid} is no longer MangaFlow.Native.exe (image reuse) — not touched")

    for key in ("dev_job", "wpf_job"):
        job = open_named_job(session[key])
        if job:
            kernel32.TerminateJobObject(job, 1)
            kernel32.CloseHandle(job)
        else:
            problems.append(f"job {session[key]} could not be reopened (already destroyed?)")

    time.sleep(2)
    leftovers = verify_clean()
    problems.extend(leftovers)
    print(f"session stopped; issues: {problems or 'none'}")
    if args.clean:
        import shutil

        shutil.rmtree(session["run_dir"], ignore_errors=False)
    STATE.unlink()
    return 0


def verify_clean() -> list[str]:
    """Report — never kill — process names that must be gone if they are ours."""
    problems: list[str] = []

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    candidates: list[tuple[str, int]] = []
    if kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
        while True:
            if entry.szExeFile in {"MangaFlow.Native.exe", "native-host.exe", "mangaflow_desktop_helper.exe", "node.exe"}:
                candidates.append((entry.szExeFile, entry.th32ProcessID))
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
    kernel32.CloseHandle(snapshot)
    # node.exe runs for unrelated tooling; only tracked pids count as ours.
    tracked: dict[str, str] = {}
    if STATE.exists():
        session = json.loads(STATE.read_text(encoding="utf-8"))
        tracked[str(session["dev_pid"])] = "dev"
        tracked[str(session["wpf_pid"])] = "wpf"
    for name, pid in candidates:
        if name in {"MangaFlow.Native.exe", "native-host.exe"}:
            # These names only exist for our client tree on this machine; still
            # report rather than kill — the lead decides.
            problems.append(f"leftover process: {name}:{pid}")
        elif str(pid) in tracked:
            problems.append(f"leftover tracked process: {name}:{pid} ({tracked[str(pid)]})")
    for port in (WEB_PORT, API_PORT):
        if not port_free(port):
            problems.append(f"port {port} still occupied after stop")
    return problems


def cmd_status(_: argparse.Namespace) -> int:
    if not STATE.exists():
        print("no session recorded")
        return 0
    session = json.loads(STATE.read_text(encoding="utf-8"))
    print(json.dumps(session, indent=2))
    print("verify_clean leftovers:", verify_clean() or "none")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start")
    start.add_argument("--run-dir", default=None)
    start.add_argument("--no-seed", action="store_true", help="schema-only databases (no dataset)")
    sub.add_parser("stop").add_argument("--clean", action="store_true", help="delete the run directory")
    sub.add_parser("status")
    args = parser.parse_args()
    return {"start": cmd_start, "stop": cmd_stop, "status": cmd_status}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
