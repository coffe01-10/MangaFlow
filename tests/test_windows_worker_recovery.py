"""Regression tests for the Windows spawn-worker horse and timeout recovery.

The spawn worker's horse is a plain ``python -c`` child (see
``app.rq_windows``), so its import surface and its failure modes differ from
the POSIX fork worker. These tests are platform-neutral: they exercise the
same environment construction and parent-side recovery code paths that run on
Windows without requiring a Windows host.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

import app.rq_windows as rq_windows
from app.rq_windows import horse_environment

REPO_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = str(Path(rq_windows.__file__).resolve().parents[1])


def _resolve(entry: str) -> Path:
    return Path(entry).resolve()


def test_horse_environment_prepends_api_root_and_keeps_existing_pythonpath():
    env = horse_environment({"PYTHONPATH": os.pathsep.join(["/custom-a", "/custom-b"])})

    entries = env["PYTHONPATH"].split(os.pathsep)
    assert _resolve(entries[0]) == Path(API_ROOT).resolve()
    assert entries[1:] == ["/custom-a", "/custom-b"]

    fresh = horse_environment({"PYTHONPATH": ""})
    assert _resolve(fresh["PYTHONPATH"]) == Path(API_ROOT).resolve()

    absent = horse_environment({})
    assert _resolve(absent["PYTHONPATH"]) == Path(API_ROOT).resolve()


def test_horse_subprocess_imports_app_from_repo_root():
    """The spawn horse must import ``app`` with only its child environment.

    Reproduces the shipped dev path: the horse is spawned as ``python -c``
    from the repo root, where ``sys.path[0]`` is the cwd and rq's ``--path``
    never reaches the child. The fixed environment must let the horse reach
    ``app.worker_tasks`` (where ``execute_job`` lives) or every RQ job burns
    its retry budget before running a single statement.
    """

    proc = subprocess.run(
        [sys.executable, "-c", "import app, app.worker_tasks; print('ok')"],
        cwd=str(REPO_ROOT),
        env=horse_environment(),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert "ok" in proc.stdout


def test_horse_import_fails_without_the_pythonpath_fix():
    """Negative control: the same spawn without the API root cannot import app.

    Skipped instead of failing when some ambient mechanism (a .pth file, a
    preset PYTHONPATH) already provides the ``app`` package from the repo
    root, which would make the control meaningless on that host.
    """

    env = horse_environment()
    stripped = os.pathsep.join(
        entry
        for entry in env.get("PYTHONPATH", "").split(os.pathsep)
        if entry and _resolve(entry) != Path(API_ROOT).resolve()
    )
    if stripped:
        env["PYTHONPATH"] = stripped
    else:
        env.pop("PYTHONPATH", None)

    proc = subprocess.run(
        [sys.executable, "-c", "import app; print('ok')"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode == 0:
        pytest.skip("ambient path already provides app")
    assert "ok" not in proc.stdout
