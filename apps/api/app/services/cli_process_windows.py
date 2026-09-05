"""Windows Job Object runner for external CLI process trees."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
from contextlib import suppress
from ctypes import wintypes
from pathlib import Path
from uuid import uuid4

from app.model_adapters.base import ProviderAdapterError
from app.services.cli_executor import CLIProcessOutcome

_CAPTURE_LIMIT = 64 * 1024
_CREATE_SUSPENDED = 0x00000004
_CREATE_NO_WINDOW = 0x08000000
_KILL_ON_JOB_CLOSE = 0x00002000
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 258


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
        raise RuntimeError("CLI execution requires Windows Job Objects")
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "ResumeThread": ([wintypes.HANDLE], wintypes.DWORD),
        "TerminateProcess": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
        "GetExitCodeProcess": (
            [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)],
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
        "AssignProcessToJobObject": (
            [wintypes.HANDLE, wintypes.HANDLE],
            wintypes.BOOL,
        ),
        "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
        "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        "GetCurrentProcess": ([], wintypes.HANDLE),
        "OpenProcess": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
        "GetProcessTimes": (
            [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4,
            wintypes.BOOL,
        ),
        "WaitForSingleObject": ([wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
    }
    for name, (args, result) in signatures.items():
        function = getattr(api, name)
        function.argtypes, function.restype = args, result
    return api


def _checked(value):
    if not value:
        raise ctypes.WinError(ctypes.get_last_error())
    return value


def _active_processes(api, job_handle) -> int:
    accounting = _Accounting()
    _checked(
        api.QueryInformationJobObject(
            job_handle, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None
        )
    )
    return accounting.active


def _creation_time(api, process_handle) -> int:
    values = [wintypes.FILETIME() for _ in range(4)]
    _checked(api.GetProcessTimes(process_handle, *(ctypes.byref(value) for value in values)))
    return (values[0].dwHighDateTime << 32) | values[0].dwLowDateTime


def windows_controller_is_active(_row, journal: dict) -> bool:
    """Verify PID creation time and named Job Object before recovery."""

    api = _kernel()
    pid, created = journal.get("controller_pid"), journal.get("controller_created")
    if type(pid) is not int or pid <= 0 or type(created) is not int:
        raise RuntimeError("CLI controller identity is incomplete")
    handle = api.OpenProcess(0x1000 | 0x100000, False, pid)
    if handle:
        try:
            if _creation_time(api, handle) == created:
                state = api.WaitForSingleObject(handle, 0)
                if state == _WAIT_TIMEOUT:
                    return True
                if state != _WAIT_OBJECT_0:
                    raise ctypes.WinError(ctypes.get_last_error())
        finally:
            _checked(api.CloseHandle(handle))
    elif ctypes.get_last_error() != 87:
        raise ctypes.WinError(ctypes.get_last_error())
    job_name = journal.get("job_name")
    if not isinstance(job_name, str) or not job_name.startswith("Local\\MangaFlowCLI_"):
        raise RuntimeError("CLI Job Object identity is incomplete")
    job_handle = api.OpenJobObjectW(0x0004, False, job_name)
    if job_handle:
        try:
            if _active_processes(api, job_handle):
                return True
        finally:
            _checked(api.CloseHandle(job_handle))
    elif ctypes.get_last_error() != 2:
        raise ctypes.WinError(ctypes.get_last_error())
    return False


def _record_suspended_process(cwd: Path, *, pid: int, job_name: str) -> None:
    journal_path, pending = cwd.parent / "journal.json", cwd.parent / "journal.pending"
    if any(path.is_symlink() or path.is_junction() for path in (journal_path, pending)):
        raise RuntimeError("CLI process journal must not be a link")
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    if journal.get("state") != "RUNNING" or not journal.get("token"):
        raise RuntimeError("CLI process journal ownership changed")
    journal.update(job_name=job_name, processes=[{"label": "cli", "pid": pid}])
    with pending.open("w", encoding="utf-8") as file:
        json.dump(journal, file, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        file.flush()
        os.fsync(file.fileno())
    pending.replace(journal_path)


class _OutputDrain:
    def __init__(self, descriptor: int) -> None:
        self.descriptor = descriptor
        self.buffer, self.digest = bytearray(), hashlib.sha256()
        self.error: BaseException | None = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def _run(self) -> None:
        try:
            with os.fdopen(self.descriptor, "rb", closefd=True) as stream:
                while chunk := stream.read(8192):
                    self.digest.update(chunk)
                    remaining = _CAPTURE_LIMIT - len(self.buffer)
                    if remaining > 0:
                        self.buffer.extend(chunk[:remaining])
        except BaseException as error:
            self.error = error

    def finish(self) -> tuple[bytes, str]:
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise TimeoutError("CLI diagnostic pipe did not close")
        if self.error:
            raise self.error
        return bytes(self.buffer), self.digest.hexdigest()


class WindowsJobCLIProcessRunner:
    """Launch argv suspended, assign it to a kill-on-close Job, then resume."""

    def __init__(self, *, timeout_grace_seconds: int = 5) -> None:
        self.timeout_grace_seconds = timeout_grace_seconds

    def controller_identity(self) -> dict[str, int]:
        api = _kernel()
        return {"pid": os.getpid(), "created": _creation_time(api, api.GetCurrentProcess())}

    def run(
        self,
        *,
        argv: tuple[str, ...],
        cwd: Path,
        environment: dict[str, str],
        timeout_seconds: int,
        cancel_requested,
    ) -> CLIProcessOutcome:
        if os.name != "nt":
            raise RuntimeError("CLI execution requires Windows Job Objects")
        executable = self._resolve(argv[0], environment)
        return self._run_windows(
            executable, argv, cwd, environment, timeout_seconds, cancel_requested
        )

    @staticmethod
    def _resolve(value: str, environment: dict[str, str]) -> str:
        candidate = Path(value)
        if candidate.is_absolute():
            resolved = candidate.resolve(strict=True)
            if not resolved.is_file():
                raise ProviderAdapterError("UNAVAILABLE", "CLI 可执行文件不存在")
            return str(resolved)
        discovered = shutil.which(value, path=environment.get("PATH"))
        if not discovered:
            raise ProviderAdapterError("UNAVAILABLE", "CLI 可执行文件不存在")
        return str(Path(discovered).resolve(strict=True))

    def _run_windows(self, executable, argv, cwd, environment, timeout_seconds, cancel_requested):
        import _winapi
        import msvcrt

        api = _kernel()
        job_handle = process_handle = thread_handle = None
        stdout_read, stdout_write = os.pipe()
        stderr_read, stderr_write = os.pipe()
        stdin_file = os.fdopen(os.open(os.devnull, os.O_RDONLY), "rb")
        stdout_drain, stderr_drain = _OutputDrain(stdout_read), _OutputDrain(stderr_read)
        for descriptor in (stdout_write, stderr_write, stdin_file.fileno()):
            os.set_inheritable(descriptor, True)
        stdout_drain.start()
        stderr_drain.start()
        assigned = False
        try:
            job_name = "Local\\MangaFlowCLI_" + uuid4().hex
            job_handle = _checked(api.CreateJobObjectW(None, job_name))
            limits = _ExtendedLimits()
            limits.basic.flags = _KILL_ON_JOB_CLOSE
            _checked(
                api.SetInformationJobObject(
                    job_handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
                )
            )
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESTDHANDLES
            startup.hStdInput = msvcrt.get_osfhandle(stdin_file.fileno())
            startup.hStdOutput = msvcrt.get_osfhandle(stdout_write)
            startup.hStdError = msvcrt.get_osfhandle(stderr_write)
            process_handle, thread_handle, pid, _ = _winapi.CreateProcess(
                executable,
                subprocess.list2cmdline(list(argv)),
                None,
                None,
                True,
                _CREATE_SUSPENDED | _CREATE_NO_WINDOW,
                environment,
                str(cwd),
                startup,
            )
            _checked(api.AssignProcessToJobObject(job_handle, process_handle))
            assigned = True
            _record_suspended_process(cwd, pid=pid, job_name=job_name)
            os.close(stdout_write)
            stdout_write = -1
            os.close(stderr_write)
            stderr_write = -1
            if api.ResumeThread(thread_handle) == 0xFFFFFFFF:
                raise ctypes.WinError(ctypes.get_last_error())
            _checked(api.CloseHandle(thread_handle))
            thread_handle = None
            timed_out = cancelled = False
            deadline = time.monotonic() + timeout_seconds
            while _active_processes(api, job_handle):
                if cancel_requested():
                    cancelled = True
                    _checked(api.TerminateJobObject(job_handle, 125))
                    break
                if time.monotonic() >= deadline:
                    timed_out = True
                    _checked(api.TerminateJobObject(job_handle, 125))
                    break
                # Supervision poll: cancellation and the timeout deadline only
                # need ~1s granularity, while each poll opens a fresh DB
                # session (the cancel probe) — a 10x slower cadence cuts the
                # transient-DB-failure exposure of a paid run without
                # measurable cost. Diagnostic-pipe EOF is detected by the
                # _OutputDrain threads, not by this loop, so it is unaffected.
                time.sleep(0.5)
            stop_deadline = time.monotonic() + self.timeout_grace_seconds
            while _active_processes(api, job_handle):
                if time.monotonic() >= stop_deadline:
                    raise TimeoutError("CLI Job Object did not terminate")
                time.sleep(0.02)
            if api.WaitForSingleObject(process_handle, 5000) != _WAIT_OBJECT_0:
                raise TimeoutError("CLI launcher handle did not signal")
            code = wintypes.DWORD()
            _checked(api.GetExitCodeProcess(process_handle, ctypes.byref(code)))
            _checked(api.CloseHandle(process_handle))
            process_handle = None
            _checked(api.CloseHandle(job_handle))
            job_handle = None
            stdin_file.close()
            stdout, stdout_checksum = stdout_drain.finish()
            stderr, stderr_checksum = stderr_drain.finish()
            return CLIProcessOutcome(
                code.value,
                stdout,
                stderr,
                stdout_checksum,
                stderr_checksum,
                timed_out,
                cancelled,
            )
        except BaseException:
            if job_handle:
                with suppress(BaseException):
                    api.TerminateJobObject(job_handle, 125)
            if process_handle and not assigned:
                with suppress(BaseException):
                    api.TerminateProcess(process_handle, 125)
            raise
        finally:
            for descriptor in (stdout_write, stderr_write):
                if descriptor >= 0:
                    os.close(descriptor)
            if not stdin_file.closed:
                stdin_file.close()
            for handle in (thread_handle, process_handle, job_handle):
                if handle:
                    api.CloseHandle(handle)
            stdout_drain.thread.join(timeout=1)
            stderr_drain.thread.join(timeout=1)
