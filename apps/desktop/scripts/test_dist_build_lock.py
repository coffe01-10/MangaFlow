"""Concurrency self-test for the shared apps/desktop/dist/ build lock (#350).

Three destructive writers share dist/ with no cross-process serialization
(build-frontend-static.sh's rm -rf + repopulate, build-web-standalone.py's
rmtree + move, and the e2e runner's rebuild of the same bundle); the lock
ships in two implementations — python (build_web_standalone._dist_build_lock)
and bash (dist-build-lock.sh) — that must agree on the mechanism per
platform (flock where it exists, an O_EXCL lock file with retry + timeout
otherwise). That agreement assumes bash and python share one environment
source (#383); the cross-language test probes both sides and skips mixed
hosts where the two mechanisms cannot see each other.

Each test launches two SUBPROCESSES that contend for the same lock file and
asserts their critical sections never overlap in wall-clock time, including
the cross-language pair that is the actual production race (a static-export
build running while a standalone build relocates its bundle). The contenders
also record a pre-acquisition launch timestamp so the test can prove the
loser was genuinely waiting (launched while the winner still held), not
merely scheduled late.

Collected by run-sidecar-e2e.sh next to the other desktop suites; like them
it is excluded from the default pytest collection (pytest.ini norecursedirs)
so a bare `npm run test` never spawns it.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
STANDALONE_PY = SCRIPTS / "build-web-standalone.py"
LOCK_SH = SCRIPTS / "dist-build-lock.sh"

HOLD_SECONDS = 1.5
CONTEND_TIMEOUT = 60

_PYTHON_CONTENDER = (
    "import importlib.util, sys, time\n"
    "from pathlib import Path\n"
    "module_path, lock_path, start_file, end_file = sys.argv[1:5]\n"
    "spec = importlib.util.spec_from_file_location('bws_lock_probe', module_path)\n"
    "module = importlib.util.module_from_spec(spec)\n"
    "spec.loader.exec_module(module)\n"
    "with module._dist_build_lock(Path(lock_path), timeout=30):\n"
    "    Path(start_file).write_text(str(time.time_ns()), encoding='utf-8')\n"
    f"    time.sleep({HOLD_SECONDS})\n"
    "    Path(end_file).write_text(str(time.time_ns()), encoding='utf-8')\n"
)

_BASH_CONTENDER = (
    "set -e\n"
    'source "{helper}"\n'
    'acquire_dist_build_lock "{lock}" 30\n'
    'date +%s%N > "{start}"\n'
    f"sleep {HOLD_SECONDS}\n"
    'date +%s%N > "{end}"\n'
    'release_dist_build_lock "{lock}"\n'
)


def _load_standalone_module():
    """Import build-web-standalone.py without running the build (its work
    lives in main(); import only defines the lock and constants)."""

    spec = importlib.util.spec_from_file_location("build_web_standalone_locktest", STANDALONE_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_contenders(tmp_path: Path, *, first: str, second: str) -> dict:
    """Launch both contenders concurrently and return their intervals.

    ``first``/``second`` select "python" or "bash". Returns
    ``{name: (launch, start, end)}`` in wall-clock nanoseconds, where
    ``launch`` is recorded by the parent just before spawning (so it
    precedes every acquire attempt the child can make).
    """

    lock = tmp_path / "dist" / ".build.lock"
    plans = {}
    for index, name in enumerate((first, second)):
        key = f"contender-{index}-{name}"
        base = tmp_path / key
        base.mkdir()
        if name == "python":
            command = [
                sys.executable, "-c", _PYTHON_CONTENDER,
                str(STANDALONE_PY), str(lock),
                str(base / "start"), str(base / "end"),
            ]
        else:
            script = _BASH_CONTENDER.format(
                helper=LOCK_SH, lock=lock, start=base / "start", end=base / "end"
            )
            command = ["bash", "-c", script]
        plans[key] = (command, base)

    processes = {}
    for key, (command, _base) in plans.items():
        launch = time.time_ns()
        processes[key] = (subprocess.Popen(command), launch)

    intervals = {}
    failures = []
    for key, (process, launch) in processes.items():
        stdout, stderr = process.communicate(timeout=CONTEND_TIMEOUT)
        if process.returncode != 0:
            failures.append(f"{key} contender failed ({process.returncode}): {stderr}")
            continue
        base = plans[key][1]
        intervals[key] = (
            launch,
            int((base / "start").read_text(encoding="utf-8")),
            int((base / "end").read_text(encoding="utf-8")),
        )
    assert not failures, "; ".join(failures)
    return intervals


def _assert_mutually_exclusive(intervals: dict) -> None:
    (name_a, interval_a), (name_b, interval_b) = sorted(
        intervals.items(), key=lambda item: item[1][1]
    )
    launch_a, start_a, end_a = interval_a
    launch_b, start_b, end_b = interval_b
    # The critical sections must never overlap.
    assert end_a <= start_b, (
        f"{name_a}'s critical section [{start_a}, {end_a}] overlaps "
        f"{name_b}'s [{start_b}, {end_b}] — the lock did not serialize them"
    )
    # And the overlap-free finish must come from waiting, not from being
    # launched late: the second contender was already trying to acquire
    # while the first one still held the lock.
    assert launch_b <= end_a, (
        f"{name_b} only started after {name_a} had finished — contention "
        "was never exercised (test is vacuous this run)"
    )


def _have_bash() -> bool:
    return shutil.which("bash") is not None


def _interlock_mechanisms_match() -> tuple[bool, str]:
    """#383: the bash (flock) and python (fcntl) lock forms exclude each
    other only when both sides resolve to the SAME mechanism, which holds
    when bash and python come from one environment source (this repo's
    main path: git bash + Windows-native python — neither has flock/fcntl,
    both take the lock-file branch). Probe both sides at runtime so a
    mixed host (MSYS2 bash WITH flock + Windows-native python WITHOUT
    fcntl, or the mirror case) skips with the reason instead of failing
    on a race the lock never promised to prevent."""

    try:
        import fcntl  # noqa: F401

        python_has_flock = True
    except ImportError:
        python_has_flock = False
    probe = subprocess.run(
        ["bash", "-c", "command -v flock >/dev/null 2>&1"], capture_output=True
    )
    bash_has_flock = probe.returncode == 0
    if bash_has_flock != python_has_flock:
        return False, (
            f"bash flock available={bash_has_flock} but python fcntl "
            f"available={python_has_flock}: mixed-environment host where the "
            "two lock mechanisms cannot see each other (#383); the "
            "cross-language exclusion assertion would be a false failure"
        )
    return True, ""


def test_python_lock_excludes_concurrent_python_holders(tmp_path: Path):
    intervals = _run_contenders(tmp_path, first="python", second="python")
    _assert_mutually_exclusive(intervals)


def test_bash_lock_excludes_concurrent_bash_holders(tmp_path: Path):
    if not _have_bash():
        pytest.skip("no bash available for the dist-build-lock.sh branch")
    intervals = _run_contenders(tmp_path, first="bash", second="bash")
    _assert_mutually_exclusive(intervals)


def test_python_and_bash_writers_interlock(tmp_path: Path):
    """The production race: build-web-standalone.py's rmtree+move against
    build-frontend-static.sh's rm -rf, one in each language."""

    if not _have_bash():
        pytest.skip("no bash available for the dist-build-lock.sh branch")
    mechanisms_match, reason = _interlock_mechanisms_match()
    if not mechanisms_match:
        pytest.skip(reason)
    intervals = _run_contenders(tmp_path, first="python", second="bash")
    _assert_mutually_exclusive(intervals)


def test_fallback_release_cleans_the_lock_file(tmp_path: Path):
    """On flock-less hosts (Windows git bash) the lock FILE is the mutex, so
    a correct release must remove it — otherwise every later build waits
    out its whole timeout. On flock hosts the file legitimately persists
    (the open file description is the mutex); both outcomes are pinned so
    a regression on either platform is caught."""

    module = _load_standalone_module()
    lock = tmp_path / "dist" / ".build.lock"
    try:
        import fcntl  # noqa: F401

        with module._dist_build_lock(lock, timeout=5):
            assert lock.exists(), "flock form must still create the stable lock file"
        assert lock.exists(), "flock form keeps the (harmless) lock file around"
    except ImportError:
        with module._dist_build_lock(lock, timeout=5):
            assert lock.exists(), "fallback form must hold the lock file while locked"
        assert not lock.exists(), "fallback release must delete the lock file"


def test_importing_the_build_script_has_no_side_effects():
    """The lock test imports build-web-standalone.py; keep it honest by
    pinning that an import alone never starts a build (all work lives in
    main(), exactly like the helper's import-safe shape)."""

    module = _load_standalone_module()
    assert callable(module.main)
    assert callable(module._dist_build_lock)
