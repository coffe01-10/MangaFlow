"""Offline structural guards for the Windows-only process boundary."""

import inspect
import os

import pytest

from app.services.cli_process_windows import WindowsJobCLIProcessRunner


def test_windows_runner_refuses_non_windows_without_spawning():
    if os.name == "nt":
        pytest.skip("non-Windows guard")
    with pytest.raises(RuntimeError, match="Windows Job Objects"):
        WindowsJobCLIProcessRunner().run(
            argv=("fake.exe",),
            cwd=None,
            environment={},
            timeout_seconds=1,
            cancel_requested=lambda: False,
        )


def test_windows_source_assigns_and_journals_before_resume_without_shell():
    source = inspect.getsource(__import__(
        "app.services.cli_process_windows", fromlist=["*"]
    ))
    assign = source.index("AssignProcessToJobObject(job_handle, process_handle)")
    journal = source.rindex("_record_suspended_process(cwd")
    resume = source.index("ResumeThread(thread_handle)")
    assert assign < journal < resume
    assert "shell=True" not in source
    assert "cmd /c" not in source.lower()
