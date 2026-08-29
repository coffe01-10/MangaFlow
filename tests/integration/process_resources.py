"""Windows process ownership for acceptance helpers, not a substitute for RQ tests.

Every Python launcher is created suspended and assigned to a named Job Object
before its first instruction (including the Windows venv redirector). Descendants
inherit the job (no breakaway); losing the controller kills
the tree. No PID-based taskkill, inherited application environment or services.
See https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
import time
# Ruff lints tests/ outside apps/api's py312 target; import the 3.11+ builtins explicitly.
from builtins import BaseExceptionGroup
from ctypes import wintypes
from pathlib import Path
from uuid import uuid4

# CPython starts without inherited Python configuration. Site loads after the
# suspended process is assigned and resumed, so .pth cannot escape the job.
_BOOTSTRAP = """
import site, sys
site.main()
code, *args = sys.argv[1:]
sys.argv = ["-c", *args]
exec(compile(code, "<acceptance-child>", "exec"), {"__name__": "__main__"})
"""


class _Limits(ctypes.Structure):
    _fields_ = [
        ("per_process", ctypes.c_int64),
        ("per_job", ctypes.c_int64),
        ("flags", wintypes.DWORD),
        ("min_working_set", ctypes.c_size_t),
        ("max_working_set", ctypes.c_size_t),
        ("active_limit", wintypes.DWORD),
        ("affinity", ctypes.c_size_t),
        ("priority", wintypes.DWORD),
        ("scheduling", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_uint64)
        for name in (
            "read_ops",
            "write_ops",
            "other_ops",
            "read_bytes",
            "write_bytes",
            "other_bytes",
        )
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("basic", _Limits),
        ("io", _IoCounters),
        ("process_memory", ctypes.c_size_t),
        ("job_memory", ctypes.c_size_t),
        ("peak_process", ctypes.c_size_t),
        ("peak_job", ctypes.c_size_t),
    ]


class _Accounting(ctypes.Structure):
    _fields_ = [
        ("user", ctypes.c_int64),
        ("kernel", ctypes.c_int64),
        ("period_user", ctypes.c_int64),
        ("period_kernel", ctypes.c_int64),
        ("page_faults", wintypes.DWORD),
        ("total", wintypes.DWORD),
        ("active", wintypes.DWORD),
        ("terminated", wintypes.DWORD),
    ]


def _kernel():
    if os.name != "nt":
        raise RuntimeError("Windows process acceptance requires Windows Job Objects")
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "ResumeThread": ([wintypes.HANDLE], wintypes.DWORD),
        "TerminateProcess": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
        "GetExitCodeProcess": ([wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
        "IsProcessInJob": (
            [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)],
            wintypes.BOOL,
        ),
        "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
        "OpenJobObjectW": ([wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
        "SetInformationJobObject": (
            [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD],
            wintypes.BOOL,
        ),
        "QueryInformationJobObject": (
            [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p],
            wintypes.BOOL,
        ),
        "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
        "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
        "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        "GetCurrentProcess": ([], wintypes.HANDLE),
        "OpenProcess": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
        "WaitForSingleObject": ([wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
        "GetProcessTimes": (
            [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4,
            wintypes.BOOL,
        ),
    }
    for name, (args, result) in signatures.items():
        function = getattr(api, name)
        function.argtypes, function.restype = args, result
    return api


def _checked(value):
    if not value:
        raise ctypes.WinError(ctypes.get_last_error())
    return value


def _active(api, handle) -> int:
    counters = _Accounting()
    _checked(
        api.QueryInformationJobObject(
            handle, 1, ctypes.byref(counters), ctypes.sizeof(counters), None
        )
    )
    return counters.active


def _creation_time(api, handle) -> int:
    values = [wintypes.FILETIME() for _ in range(4)]
    _checked(api.GetProcessTimes(handle, *(ctypes.byref(value) for value in values)))
    return (values[0].dwHighDateTime << 32) | values[0].dwLowDateTime


def _controller_alive(api, record: dict) -> bool:
    pid, created = record["controller_pid"], record["controller_created"]
    if type(pid) is not int or pid <= 0 or type(created) is not int:
        raise RuntimeError("Invalid controller identity")
    handle = api.OpenProcess(0x1000 | 0x100000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 87:  # ERROR_INVALID_PARAMETER: no such PID.
            return False
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if _creation_time(api, handle) != created:
            return False  # Reused PID is not this controller; never signal it.
        state = api.WaitForSingleObject(handle, 0)
        if state not in (0, 258):
            raise ctypes.WinError(ctypes.get_last_error())
        return state == 258  # WAIT_TIMEOUT: process still alive.
    finally:
        _checked(api.CloseHandle(handle))


def _job_name(token: str) -> str:
    return "Local\\MangaFlowAcceptance_" + token


def _write_record(path: Path, record: dict) -> None:
    # Only status/identity fields, never commands, environment, URLs or passwords.
    pending = path.with_suffix(".pending")
    for target in (path, pending):
        if target.is_symlink() or target.is_junction():
            raise RuntimeError("Process journal must not be a link")
    with pending.open("w", encoding="utf-8") as file:
        json.dump(record, file, sort_keys=True)
        file.flush()
        os.fsync(file.fileno())
    pending.replace(path)


def _validate_directory(directory: Path, token: str) -> tuple[Path, dict]:
    if re.fullmatch(r"[0-9a-f]{32}", token) is None:
        raise ValueError("Invalid process ownership token")
    absolute = directory.absolute()
    resolved = directory.resolve(strict=True)
    if resolved != absolute or resolved.name != "mangaflow-process-" + token:
        raise RuntimeError("Process runtime path/ownership mismatch")
    record_file = resolved / "owner.json"
    if record_file.is_symlink() or record_file.is_junction():
        raise RuntimeError("Process ownership record must not be a link")
    record = json.loads(record_file.read_text(encoding="utf-8"))
    if record.get("token") != token or record.get("version") != 1:
        raise RuntimeError("Process ownership marker changed")
    return resolved, record


def _clean_stopped_directory(directory: Path, token: str) -> None:
    directory, record = _validate_directory(directory, token)
    if record.get("state") not in {"stopped", "cleanup_failed"}:
        raise RuntimeError("Process tree exit has not been verified")
    try:
        # Only payload is recursively removed. Owner survives any locked payload.
        payload = directory / "payload"
        if payload.is_symlink() or payload.is_junction():
            raise RuntimeError("Process payload must not be a link")
        if payload.exists():
            shutil.rmtree(payload)  # Propagate Windows locked-file failures.
        unexpected = {path.name for path in directory.iterdir()} - {"owner.json", "owner.pending"}
        if unexpected:
            raise RuntimeError("Unexpected files in process ownership directory")
        pending = directory / "owner.pending"
        if pending.exists():
            pending.unlink()
        (directory / "owner.json").unlink()
        directory.rmdir()
    except BaseException as exc:
        if directory.exists():
            record.update(state="cleanup_failed", error=type(exc).__name__)
            _write_record(directory / "owner.json", record)
        raise


class OwnedPythonProcess:
    """Keep the actual process HANDLE; all termination avoids PID lookup."""

    def __init__(self, api, handle, pid):
        self.api, self.handle, self.pid = api, handle, pid
        self.returncode = None
        self.assigned_to_job = False

    def poll(self):
        if self.returncode is not None:
            return self.returncode
        state = self.api.WaitForSingleObject(self.handle, 0)
        if state == 258:
            return None
        if state != 0:
            raise ctypes.WinError(ctypes.get_last_error())
        code = wintypes.DWORD()
        _checked(self.api.GetExitCodeProcess(self.handle, ctypes.byref(code)))
        self.returncode = code.value
        return self.returncode

    def wait(self, timeout=5):
        if self.returncode is not None:
            return self.returncode
        state = self.api.WaitForSingleObject(self.handle, max(0, int(timeout * 1000)))
        if state == 258:
            raise TimeoutError("Owned Python process has not exited")
        if state != 0:
            raise ctypes.WinError(ctypes.get_last_error())
        return self.poll()

    def terminate(self):
        _checked(self.api.TerminateProcess(self.handle, 125))

    def close(self):
        if self.handle:
            if self.poll() is None:
                raise RuntimeError("Cannot release a running process handle")
            _checked(self.api.CloseHandle(self.handle))
            self.handle = None


class OwnedProcessTree:
    """Own only this controller's Python process tree; never adopt a live PID."""

    def __init__(self, parent: Path):
        self.api = _kernel()
        self.token = uuid4().hex
        parent = parent.resolve(strict=True)
        self.directory = parent / ("mangaflow-process-" + self.token)
        self.directory.mkdir()
        self.payload = self.directory / "payload"
        self.record = {
            "version": 1,
            "token": self.token,
            "state": "created",
            "processes": [],
            "controller_pid": os.getpid(),
            "controller_created": _creation_time(self.api, self.api.GetCurrentProcess()),
        }
        self.processes: list[OwnedPythonProcess] = []
        self.handle = None
        self.sealed = False
        self.cleaned = False
        try:
            _write_record(self.directory / "owner.json", self.record)
            self.payload.mkdir()
            ctypes.set_last_error(0)
            handle = _checked(self.api.CreateJobObjectW(None, _job_name(self.token)))
            if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
                _checked(self.api.CloseHandle(handle))
                raise RuntimeError("Refusing to adopt an existing process job")
            self.handle = handle
            limits = _ExtendedLimits()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            _checked(
                self.api.SetInformationJobObject(
                    handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
                )
            )
        except BaseException:
            if self.handle:
                _checked(self.api.CloseHandle(self.handle))
                self.handle = None
            self.record["state"] = "stopped"
            _write_record(self.directory / "owner.json", self.record)
            _clean_stopped_directory(self.directory, self.token)
            raise

    def _save(self) -> None:
        _write_record(self.directory / "owner.json", self.record)

    def start_python(
        self,
        label: str,
        code: str,
        arguments: list[str] | None = None,
        *,
        environment: dict[str, str] | None = None,
    ) -> OwnedPythonProcess:
        if self.sealed or not self.handle:
            raise RuntimeError("Process registration is closed")
        if re.fullmatch(r"[a-zA-Z0-9_-]{1,60}", label) is None:
            raise ValueError("Invalid process label")
        if any(item["label"] == label for item in self.record["processes"]):
            raise ValueError("Process label already registered")
        _validate_directory(self.directory, self.token)
        # Deliberately no environment copy; callers explicitly supply isolated values.
        env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR") if key in os.environ}
        env.update(TEMP=str(self.payload), TMP=str(self.payload), PYTHONDONTWRITEBYTECODE="1")
        for key, value in (environment or {}).items():
            if key.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PYTHONPATH", "PYTHONHOME"}:
                raise ValueError("Cannot override process bootstrap environment")
            env[key] = value
        import _winapi  # Same CPython Windows API used by subprocess.Popen.

        command = [sys.executable, "-I", "-B", "-S", "-c", _BOOTSTRAP, code, *(arguments or [])]
        handle, thread, pid, _tid = _winapi.CreateProcess(
            sys.executable,
            subprocess.list2cmdline(command),
            None,
            None,
            False,
            0x00000004 | subprocess.CREATE_NO_WINDOW,  # CREATE_SUSPENDED
            env,
            str(self.payload),
            subprocess.STARTUPINFO(),
        )
        child = OwnedPythonProcess(self.api, handle, pid)
        self.processes.append(child)
        try:
            _checked(self.api.AssignProcessToJobObject(self.handle, child.handle))
            child.assigned_to_job = True
            self.record["processes"].append({"label": label, "pid": child.pid})
            self.record["state"] = "running"
            self._save()  # Durable ownership before executing even the venv launcher.
            if self.api.ResumeThread(thread) == 0xFFFFFFFF:
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            child.terminate()
            child.wait(timeout=5)
            raise
        finally:
            _checked(self.api.CloseHandle(thread))
        return child

    def stop(self, timeout: float = 5) -> None:
        self.sealed = True
        if self.record["state"] == "stopped":
            return
        _validate_directory(self.directory, self.token)
        try:
            if self.handle:
                _checked(self.api.TerminateJobObject(self.handle, 125))
                deadline = time.monotonic() + timeout
                while _active(self.api, self.handle):
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Owned process tree did not exit")
                    time.sleep(0.02)
            for child in self.processes:
                if not child.assigned_to_job and child.poll() is None:
                    child.terminate()  # Only failed assignment leaves a suspended outsider.
                # Job active-count zero can precede HANDLE signaling. Wait for
                # assigned members; a second TerminateProcess races exit (ERROR_ACCESS_DENIED).
                child.wait(timeout=timeout)
            self.record["state"] = "stopped"
            for item in self.record["processes"]:
                child = next(process for process in self.processes if process.pid == item["pid"])
                item["exit_code"] = child.returncode
            self.record.pop("error", None)
            self._save()
            for child in self.processes:
                child.close()
            if self.handle:
                _checked(self.api.CloseHandle(self.handle))
                self.handle = None
        except BaseException as exc:
            self.record.update(state="stop_failed", error=type(exc).__name__)
            self._save()
            raise

    def cleanup(self) -> None:
        if self.cleaned:
            return
        self.stop()
        _clean_stopped_directory(self.directory, self.token)
        self.cleaned = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        try:
            self.cleanup()
        except BaseException as cleanup_error:
            if exc is not None:
                raise BaseExceptionGroup(
                    "Process body and cleanup failed", [exc, cleanup_error]
                ) from None
            raise


def recover_stopped_tree(directory: Path, token: str) -> None:
    """Recover an owned journal, never kill a PID or adopt an active controller."""
    directory, record = _validate_directory(directory, token)
    api = _kernel()
    if _controller_alive(api, record):
        raise RuntimeError("Process controller is still active; recovery refused")
    handle = api.OpenJobObjectW(0x0004, False, _job_name(token))  # JOB_OBJECT_QUERY
    if handle:
        try:
            if _active(api, handle):
                raise RuntimeError("Owned process job is still active; recovery refused")
        finally:
            _checked(api.CloseHandle(handle))
    elif ctypes.get_last_error() != 2:  # Only ERROR_FILE_NOT_FOUND is accepted.
        raise ctypes.WinError(ctypes.get_last_error())
    # A job is destroyed only after last handle close and all associated exits.
    record["state"] = "stopped"
    record.pop("error", None)
    _write_record(directory / "owner.json", record)
    _clean_stopped_directory(directory, token)
