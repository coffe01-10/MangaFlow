"""Owned browser acceptance runtime; only its Job Object controller may delete it."""

from __future__ import annotations

import ctypes
import json
import os
import re
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

from owned_processes import (
    OwnedProcessTree,
    _checked,
    _controller_alive,
    _job_name,
    _kernel,
    _validate_directory,
)


@dataclass
class BrowserRuntime:
    run_id: str
    path: Path
    tree: OwnedProcessTree | None = None
    cleaned: bool = False

    def cleanup(self) -> None:
        if self.tree is None:
            raise RuntimeError("Only the process controller may clean the runtime")
        self.tree.cleanup()
        self.cleaned = True


def new_runtime(parent: Path) -> BrowserRuntime:
    tree = OwnedProcessTree(parent)
    try:
        runtime = tree.payload / "runtime"
        runtime.mkdir()
        with (runtime / "runtime-owner.json").open("x", encoding="utf-8") as file:
            json.dump({"version": 1, "run_id": tree.token}, file)
            # #874 durability parity: assigned_runtime re-reads this marker as
            # the runtime's ownership proof; a lost/torn write would fail every
            # later assignment. Payload flush+fsync, create-new without rename
            # (same shape as backup_restore's write_owner_marker).
            file.flush()
            os.fsync(file.fileno())
        (runtime / "storage").mkdir()
        (runtime / "uploads").mkdir()
        return BrowserRuntime(tree.token, runtime, tree)
    except BaseException:
        tree.cleanup()
        raise


def assigned_runtime(*, require_job: bool = True) -> BrowserRuntime:
    token = os.environ.get("MANGAFLOW_E2E_RUN_ID", "")
    raw = os.environ.get("MANGAFLOW_E2E_RUNTIME", "")
    if re.fullmatch(r"[0-9a-f]{32}", token) is None or not raw:
        raise RuntimeError("Runtime does not belong to an acceptance controller")
    path = Path(raw)
    if not path.is_absolute() or path.absolute() != path.resolve(strict=True):
        raise RuntimeError("Runtime path must be absolute, canonical and not a link")
    directory, record = _validate_directory(path.parent.parent, token)
    if path != directory / "payload" / "runtime":
        raise RuntimeError("Runtime path does not belong to this run")
    marker = path / "runtime-owner.json"
    if marker.is_symlink() or marker.is_junction():
        raise RuntimeError("Runtime owner must not be a link")
    owner = json.loads(marker.read_text(encoding="utf-8"))
    if owner != {"version": 1, "run_id": token}:
        raise RuntimeError("Runtime ownership marker changed")
    api = _kernel()
    if not _controller_alive(api, record):
        raise RuntimeError("Acceptance controller is no longer active")
    if require_job:
        if record["state"] != "running":
            raise RuntimeError("Acceptance process tree is not running")
        job = _checked(api.OpenJobObjectW(0x0004, False, _job_name(token)))
        try:
            member = wintypes.BOOL()
            _checked(api.IsProcessInJob(api.GetCurrentProcess(), job, ctypes.byref(member)))
            if not member.value:
                raise RuntimeError("Process is not in the acceptance controller's job")
        finally:
            _checked(api.CloseHandle(job))
    return BrowserRuntime(token, path)


def _listener_pids(port: int) -> list[int]:
    """List PIDs owning LISTEN sockets on 127.0.0.1:<port> via the native TCP table.

    This used to shell out to `Get-NetTCPConnection`; every call spawned a
    fresh powershell.exe whose cold CIM/NetTCPIP module load exceeded the
    15s subprocess timeout on CI runners (subprocess.TimeoutExpired). The
    in-process iphlpapi query returns the same owner rows in milliseconds.
    """

    class _TcpRowOwnerPid(ctypes.Structure):
        _fields_ = [
            ("state", wintypes.DWORD),
            ("local_addr", wintypes.DWORD),
            ("local_port", wintypes.DWORD),
            ("remote_addr", wintypes.DWORD),
            ("remote_port", wintypes.DWORD),
            ("owning_pid", wintypes.DWORD),
        ]

    iphlpapi = ctypes.WinDLL("iphlpapi", use_last_error=True)
    # AF_INET (2) + TCP_TABLE_OWNER_PID_LISTENER (3): LISTEN rows with owning
    # PID. (Class 2 is TCP_TABLE_BASIC_ALL whose rows lack dwOwningPid.)
    size = wintypes.DWORD(0)
    table: ctypes.Array | None = None
    for _ in range(3):
        result = iphlpapi.GetExtendedTcpTable(table, ctypes.byref(size), False, 2, 3, 0)
        if result == 0:
            break
        if result != 122:  # ERROR_INSUFFICIENT_BUFFER
            raise ctypes.WinError(result)
        table = (ctypes.c_byte * size.value)()
    else:
        raise RuntimeError("TCP listener table kept growing; giving up")
    assert table is not None
    count = ctypes.cast(table, ctypes.POINTER(wintypes.DWORD))[0]
    if 4 + count * ctypes.sizeof(_TcpRowOwnerPid) > size.value:
        raise RuntimeError("TCP listener table row count exceeds its buffer")
    rows = ctypes.cast(
        ctypes.byref(table, ctypes.sizeof(wintypes.DWORD)), ctypes.POINTER(_TcpRowOwnerPid)
    )
    loopback = 0x0100007F  # 127.0.0.1 in network byte order.
    pids: list[int] = []
    for index in range(count):
        row = rows[index]
        # dwLocalPort is network byte order in a DWORD: ntohs equivalent.
        local_port = ((row.local_port >> 8) & 0xFF) | ((row.local_port & 0xFF) << 8)
        if row.local_addr == loopback and local_port == port:
            pids.append(row.owning_pid)
    return pids


def verify_owned_listener(runtime: BrowserRuntime, port: int) -> list[int]:
    """Read loopback listeners, then verify each real process HANDLE is in our job."""
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Invalid listener port")
    pids = _listener_pids(port)
    if not pids:
        raise RuntimeError("No owned loopback listener")
    api = _kernel()
    job = _checked(api.OpenJobObjectW(0x0004, False, _job_name(runtime.run_id)))
    try:
        for pid in pids:
            if type(pid) is not int or pid <= 0:
                raise RuntimeError("Invalid listener identity")
            handle = _checked(api.OpenProcess(0x1000 | 0x100000, False, pid))
            try:
                member = wintypes.BOOL()
                _checked(api.IsProcessInJob(handle, job, ctypes.byref(member)))
                if not member.value:
                    raise RuntimeError("Listener does not belong to this acceptance job")
                if api.WaitForSingleObject(handle, 0) != 258:
                    raise RuntimeError("Listener process already exited")
            finally:
                _checked(api.CloseHandle(handle))
    finally:
        _checked(api.CloseHandle(job))
    return pids
