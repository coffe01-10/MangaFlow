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


def verify_owned_listener(runtime: BrowserRuntime, port: int) -> list[int]:
    """Read loopback listeners, then verify each real process HANDLE is in our job."""
    import subprocess

    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Invalid listener port")
    powershell = Path(os.environ["SYSTEMROOT"]) / ("System32/WindowsPowerShell/v1.0/powershell.exe")
    # This is a fixed read-only query with a validated integer, never a PID kill.
    command = (
        "$ErrorActionPreference='Stop'; "
        f"@(Get-NetTCPConnection -State Listen -LocalAddress 127.0.0.1 -LocalPort {port}) "
        "| Select-Object -ExpandProperty OwningProcess | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        [str(powershell), "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    pids = json.loads(result.stdout)
    if type(pids) is int:
        pids = [pids]
    if not isinstance(pids, list) or not pids:
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
