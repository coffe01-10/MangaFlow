"""Journal permission hardening (#870).

POSIX: ``main()`` sets umask 077 and ``_write_journal`` fchmods the
pending handle to 0600 before fsync. Windows ACL tightening is NOT RUN
(no ``fchmod``); the structural pins still execute on every host.
"""

from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sidecar"))

import mangaflow_desktop_helper as helper  # noqa: E402

TOKEN = "0123456789abcdef0123456789abcdef"


def _journal(tmp_path: Path) -> Path:
    runtime = tmp_path / "runtime" / f"mangaflow-desktop-{TOKEN}"
    runtime.mkdir(parents=True)
    return runtime / "owner.json"


def test_write_journal_fchmods_pending_to_0600(tmp_path, monkeypatch):
    source = inspect.getsource(helper._write_journal)
    assert "fchmod" in source and "0o600" in source, source

    if not hasattr(os, "fchmod"):
        return

    journal = _journal(tmp_path)
    calls: list[int] = []
    real = os.fchmod

    def spy(fd, mode):
        calls.append(mode)
        return real(fd, mode)

    monkeypatch.setattr(helper.os, "fchmod", spy)
    helper._write_journal(
        journal, {"version": 1, "token": TOKEN, "role": "app", "state": "ready"}
    )
    assert calls == [0o600], calls
    assert (os.stat(journal).st_mode & 0o777) == 0o600


def test_main_sets_umask_077_on_posix():
    source = inspect.getsource(helper.main)
    assert "os.umask(0o077)" in source, source
    assert 'sys.platform != "win32"' in source, source
