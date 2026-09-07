"""Offline structural guards for the Windows-only process boundary."""

import inspect
import json
import logging
import os

import pytest
from sqlalchemy.exc import OperationalError

import app.services.cli_process_windows as cli_process_windows
from app.config import Settings


def test_windows_runner_refuses_non_windows_without_spawning():
    if os.name == "nt":
        pytest.skip("non-Windows guard")
    with pytest.raises(RuntimeError, match="Windows Job Objects"):
        cli_process_windows.WindowsJobCLIProcessRunner().run(
            argv=("fake.exe",),
            cwd=None,
            environment={},
            timeout_seconds=1,
            cancel_requested=lambda: False,
        )


def test_windows_source_assigns_and_journals_before_resume_without_shell():
    source = inspect.getsource(
        __import__("app.services.cli_process_windows", fromlist=["*"])
    )
    assign = source.index("AssignProcessToJobObject(job_handle, process_handle)")
    journal = source.rindex("_record_suspended_process(cwd")
    resume = source.index("ResumeThread(thread_handle)")
    assert assign < journal < resume
    assert "shell=True" not in source
    assert "cmd /c" not in source.lower()


class _FakeKernelAPI:
    """Kernel32 stand-in: int handles, every checked call succeeds.

    ``QueryInformationJobObject`` is reached only through
    ``_active_processes``, which tests monkeypatch separately, and
    ``GetExitCodeProcess``/``GetProcessTimes`` leave their zero-initialized
    ctypes outputs untouched (exit code 0 / creation time 0).
    """

    def CreateJobObjectW(self, *_args):
        return 1001

    def SetInformationJobObject(self, *_args):
        return 1

    def AssignProcessToJobObject(self, *_args):
        return 1

    def ResumeThread(self, _thread):
        return 1  # anything but 0xFFFFFFFF

    def CloseHandle(self, _handle):
        return 1

    def TerminateJobObject(self, *_args):
        return 1

    def WaitForSingleObject(self, _process, _timeout_ms):
        return cli_process_windows._WAIT_OBJECT_0

    def GetExitCodeProcess(self, _process, _code):
        return 1

    def GetCurrentProcess(self):
        return 4200

    def GetProcessTimes(self, _handle, *_times):
        return 1


def _probe_that_never_reaches_the_database():
    """A real adapter cancel probe whose DB session is unavailable.

    Before issue #140 the probe exception propagated out of the supervision
    loop, terminated the Job Object mid-generation and was classified CRASH;
    the fix makes the probe log and report "not cancelled" instead.
    """

    from app.model_adapters.codex_cli import CodexCLIImageAdapter, CodexCLIRuntime
    from app.model_adapters.codex_cli import _InvocationContext

    def broken_session_factory():
        raise OperationalError("SELECT 1", {}, RuntimeError("stale pooled connection"))

    adapter = CodexCLIImageAdapter(
        CodexCLIRuntime(
            settings=Settings(),
            connection_id="conn",
            catalog_model_id="model",
            provider_model_id="model",
            session_factory=broken_session_factory,
        )
    )
    calls = []

    def probe() -> bool:
        calls.append(1)
        return adapter._cancel_requested(
            _InvocationContext(
                job_id="job-1", model_call_attempt_id="attempt-1", lease_owner=None
            )
        )

    return probe, calls


def test_windows_runner_survives_cancel_probe_failure_until_child_completes(
    tmp_path, monkeypatch, caplog
):
    """A failing cancel probe must not kill the supervised child (#140).

    The real ``_run_windows`` loop runs against faked process primitives:
    the fake Job Object stays active for two supervision polls while the
    (guarded, real-adapter) cancel probe fails on every call, then the child
    completes and the outcome is adopted normally — no exception, no
    cancellation flag.
    """

    if os.name != "nt":
        pytest.skip("Windows Job Object runtime path")

    probe, probe_calls = _probe_that_never_reaches_the_database()
    executable = tmp_path / "fake-cli.exe"
    executable.write_bytes(b"MZ fake executable")
    run_directory = tmp_path / "run"
    workspace = run_directory / "workspace"
    workspace.mkdir(parents=True)
    (run_directory / "journal.json").write_text(
        json.dumps({"state": "RUNNING", "token": "tok"}), encoding="utf-8"
    )

    monkeypatch.setattr(cli_process_windows, "_kernel", lambda: _FakeKernelAPI())
    monkeypatch.setattr(
        "_winapi.CreateProcess", lambda *_args, **_kwargs: (4242, 5252, 9999, 3333)
    )
    # Active for two supervision iterations (0.5 s poll each), then gone.
    active = {"count": 2}

    def fake_active_processes(_api, _job):
        value = active["count"]
        active["count"] = max(0, value - 1)
        return value

    monkeypatch.setattr(cli_process_windows, "_active_processes", fake_active_processes)

    with caplog.at_level(logging.ERROR):
        outcome = cli_process_windows.WindowsJobCLIProcessRunner().run(
            argv=(str(executable), "--generate"),
            cwd=workspace,
            environment={"PATH": os.environ.get("PATH", "")},
            timeout_seconds=30,
            cancel_requested=probe,
        )

    assert outcome.exit_code == 0
    assert outcome.cancelled is False and outcome.timed_out is False
    assert len(probe_calls) == 2  # the loop polled through both failures
    assert any(
        record.message == "Cancel probe failed; treating job as not cancelled"
        for record in caplog.records
    )
