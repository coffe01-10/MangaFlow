"""Windows Job Object runner for external CLI process trees."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from contextlib import suppress
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from app.model_adapters.base import ProviderAdapterError
from app.services.cli_executor import CLIProcessOutcome

# Retention geometry for one diagnostic stream (#241-1): the first
# _CAPTURE_LIMIT bytes stay in memory (the historical cap), anything past
# that spills to an OS temp file so a successful run with more than 64 KiB of
# stdout is not silently destroyed, and only past _SPILL_LIMIT are bytes
# dropped with the outcome's truncation flags set.
_CAPTURE_LIMIT = 64 * 1024
_SPILL_LIMIT = 8 * 1024 * 1024
_CREATE_SUSPENDED = 0x00000004
_CREATE_NO_WINDOW = 0x08000000
_KILL_ON_JOB_CLOSE = 0x00002000
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 258


@dataclass(frozen=True)
class WindowsCLIProcessOutcome(CLIProcessOutcome):
    """``CLIProcessOutcome`` plus the capture layer's truncation flags (#241-1).

    The flags live on this runner-owned subclass so the provider-neutral
    ``CLIProcessOutcome`` contract in ``cli_executor`` stays untouched;
    ``False`` means the retained stream bytes are the complete stream, while
    ``True`` means bytes past the spill cap were dropped (the digest still
    covers the whole stream). Adapters read them defensively via
    ``getattr`` because delegate runners may return the plain base class.
    """

    stdout_truncated: bool = False
    stderr_truncated: bool = False


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
    """Drain one diagnostic pipe into a bounded, spill-backed buffer.

    Before #241-1 only the first ``_CAPTURE_LIMIT`` bytes were buffered while
    the digest ran over the whole stream: a successful grok run with more
    than 64 KiB of streaming-JSON stdout was silently truncated and then
    destroyed downstream as INVALID_OUTPUT/UNKNOWN_RESULT. The first
    ``_CAPTURE_LIMIT`` bytes still stay in memory (a ``SpooledTemporaryFile``
    holds them until it rolls), overflow spills to an OS temp file that
    ``finish`` reads back, and only past ``spill_limit`` are bytes dropped —
    flipping ``truncated`` so callers can classify the capture as lossy
    instead of trusting partial content.
    """

    def __init__(self, descriptor: int, *, spill_limit: int | None = None) -> None:
        self.descriptor = descriptor
        self._spill_limit = _SPILL_LIMIT if spill_limit is None else spill_limit
        self._retained = 0
        # The sink deliberately outlives this frame: finish() reads it back
        # and discard() closes it (also on the runner's error path), so a
        # `with` block would close it before the captured bytes are read.
        self._sink = tempfile.SpooledTemporaryFile(max_size=_CAPTURE_LIMIT)  # noqa: SIM115
        self.truncated = False
        self.digest = hashlib.sha256()
        self.error: BaseException | None = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def _run(self) -> None:
        try:
            with os.fdopen(self.descriptor, "rb", closefd=True) as stream:
                while chunk := stream.read(8192):
                    self.digest.update(chunk)
                    room = self._spill_limit - self._retained
                    if room > 0:
                        kept = chunk if len(chunk) <= room else chunk[:room]
                        self._sink.write(kept)
                        self._retained += len(kept)
                    if len(chunk) > max(room, 0):
                        self.truncated = True
        except BaseException as error:
            self.error = error

    def finish(self) -> tuple[bytes, str]:
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise TimeoutError("CLI diagnostic pipe did not close")
        if self.error:
            raise self.error
        try:
            self._sink.seek(0)
            return self._sink.read(), self.digest.hexdigest()
        finally:
            self.discard()

    def discard(self) -> None:
        """Release the rolled spill file (a no-op while everything is in memory)."""

        with suppress(BaseException):
            self._sink.close()


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
        try:
            if candidate.is_absolute():
                resolved = candidate.resolve(strict=True)
                if not resolved.is_file():
                    raise ProviderAdapterError("UNAVAILABLE", "CLI 可执行文件不存在")
                return str(resolved)
            discovered = shutil.which(value, path=environment.get("PATH"))
            if not discovered:
                raise ProviderAdapterError("UNAVAILABLE", "CLI 可执行文件不存在")
            return str(Path(discovered).resolve(strict=True))
        except OSError as error:
            # The executable vanished between resolution and launch (update,
            # AV quarantine): an UNAVAILABLE provider failure, not a bare
            # OSError that would surface as a controller CRASH.
            raise ProviderAdapterError("UNAVAILABLE", "CLI 可执行文件不存在") from error

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
            # #241-3: bInheritHandles=TRUE on its own leaks EVERY currently
            # inheritable handle of this process into the CLI child (another
            # run's pipe write end is the classic one), which can keep that
            # pipe from ever reaching EOF and turn a completed run into a
            # phantom TIMEOUT. PROC_THREAD_ATTRIBUTE_HANDLE_LIST restricts
            # inheritance to exactly the three std handles: _winapi.
            # CreateProcess builds the STARTUPINFOEX attribute list (and adds
            # EXTENDED_STARTUPINFO_PRESENT itself) from this dict. The three
            # handles are distinct pipe/file ends, so the list can never hold
            # the duplicate handles Windows rejects with ERROR_INVALID_PARAMETER.
            startup.lpAttributeList["handle_list"] = [
                startup.hStdInput,
                startup.hStdOutput,
                startup.hStdError,
            ]
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
            return WindowsCLIProcessOutcome(
                code.value,
                stdout,
                stderr,
                stdout_checksum,
                stderr_checksum,
                timed_out,
                cancelled,
                stdout_truncated=stdout_drain.truncated,
                stderr_truncated=stderr_drain.truncated,
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
            # Release any rolled spill files deterministically; after a
            # successful finish() this is a no-op (GC would also clean up,
            # but the error path should not depend on it).
            stdout_drain.discard()
            stderr_drain.discard()
