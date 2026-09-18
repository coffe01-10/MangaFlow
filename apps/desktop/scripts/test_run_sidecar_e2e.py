"""Contract tests for the e2e runner's resumable venv bootstrap (#343 family).

`run-sidecar-e2e.sh` creates `.venv-desktop` on first use. Its old bootstrap
only checked that `bin/python` existed — a pip install interrupted midway
(network blip) left a PARTIAL venv that every later run accepted and then
died at import time with confusing errors, and nothing ever healed it. The
bootstrap now stamps the venv with the requirements hash of the last
COMPLETED install; a missing or mismatched stamp re-runs the install.

The script is sourceable (main body runs only when executed directly), so
these tests source it and override `install_e2e_requirements` — a test must
never touch the network.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from _bash_resolve import resolve_posix_bash

_SCRIPT = Path(__file__).with_name("run-sidecar-e2e.sh").resolve()
_REQ = Path(__file__).resolve().parents[2] / "api" / "requirements.txt"

# Every pin here shells a bash script; a PATH-order `bash` that is the WSL
# stub (no distro installed) fails every one of them for an environment
# reason — run them only through a probe-verified POSIX bash (#839).
BASH = resolve_posix_bash()
pytestmark = pytest.mark.skipif(
    BASH is None,
    reason="no POSIX-capable bash on this host (the PATH-order bash may be "
    "the WSL stub); the runner-script pins need a real bash",
)
_REQ_DEV = Path(__file__).resolve().parents[2] / "api" / "requirements-dev.txt"


def _bash(p: Path) -> str:
    """Quoted forward-slash spelling for paths embedded in a bash -c body:
    on a Windows-hosted venv the bare str(path) hands bash backslash paths
    whose escapes get eaten before cd/source resolve them (the same pitfall
    as test_build_frontend_static_gate._run_gate; POSIX is unchanged)."""
    return f'"{p.as_posix()}"'


def _requirements_hash() -> str:
    hashed = subprocess.run(
        [BASH, "-c",f"cat {_bash(_REQ)} {_bash(_REQ_DEV)} | md5sum | cut -d' ' -f1"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return hashed


def _run_harness_in(workdir: Path, setup: str, fake_pip_rc: int = 0,
                    env: dict[str, str] | None = None) -> tuple[int, str]:
    harness = f"""
set -euo pipefail
cd {_bash(workdir)}
source {_bash(_SCRIPT)}
VENV="$1"
CNT="pip.calls.$$"
install_e2e_requirements() {{
  echo $(( $(cat "$CNT" 2>/dev/null || echo 0) + 1 )) > "$CNT"
  return {fake_pip_rc}
}}
{setup}
if ensure_e2e_venv "$VENV" {_bash(_REQ)} {_bash(_REQ_DEV)}; then
  echo "ENSURE_RC=0"
else
  echo "ENSURE_RC=$?"
fi
echo "PIP_CALLS=$(cat "$CNT" 2>/dev/null || echo 0)"
echo "STAMP=$(cat "$VENV/.mangaflow-bootstrap" 2>/dev/null || echo MISSING)"
"""
    done = subprocess.run(
        [BASH, "-c",harness, "harness", (workdir / "venv").as_posix()],
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
    )
    return done.returncode, done.stdout + done.stderr


def test_fresh_venv_installs_and_stamps(tmp_path):
    rc, out = _run_harness_in(tmp_path, "true")
    venv = tmp_path / "venv"
    assert rc == 0, out
    assert "ENSURE_RC=0" in out, out
    assert "PIP_CALLS=1" in out, out
    assert f"STAMP={_requirements_hash()}" in out
    # Layout-agnostic like resolve_venv_python: POSIX creators build
    # bin/python, Windows creators build Scripts/python.exe.
    assert (venv / "bin" / "python").exists() or (
        venv / "Scripts" / "python.exe"
    ).exists(), out


def test_stamped_venv_skips_the_install(tmp_path):
    # First call completes the bootstrap; the second must not re-install.
    rc, out = _run_harness_in(tmp_path, "true")
    assert rc == 0, out
    rc, out2 = _run_harness_in(tmp_path, "true")
    assert rc == 0, out2
    assert "ENSURE_RC=0" in out2, out2
    assert "PIP_CALLS=0" in out2, (
        "a stamped, requirements-matching venv must not re-install"
    )
    assert f"STAMP={_requirements_hash()}" in out2


def test_partial_venv_self_heals(tmp_path):
    # The exact poisoned state the old bootstrap accepted forever: bin/python
    # exists, but the install never completed (no stamp) — the install must
    # be (re)run and the stamp written.
    rc, out = _run_harness_in(
        tmp_path,
        'mkdir -p "$VENV/bin" && printf "#!/bin/sh\\n" > "$VENV/bin/python" '
        '&& chmod +x "$VENV/bin/python"',
    )
    assert rc == 0, out
    assert "ENSURE_RC=0" in out, out
    assert "PIP_CALLS=1" in out, "a missing stamp must trigger the install"
    assert f"STAMP={_requirements_hash()}" in out


def test_failed_install_leaves_no_stamp_and_retries(tmp_path):
    rc, out = _run_harness_in(tmp_path, "true", fake_pip_rc=1)
    assert "ENSURE_RC=1" in out, out
    assert "STAMP=MISSING" in out, out
    # The retry must actually re-run the install rather than trust the venv.
    rc, out2 = _run_harness_in(tmp_path, "true")
    assert rc == 0, out2
    assert "ENSURE_RC=0" in out2, out2
    assert "PIP_CALLS=1" in out2, out2


def test_changed_requirements_invalidate_the_stamp(tmp_path):
    rc, _ = _run_harness_in(tmp_path, "true")
    assert rc == 0
    # Simulate a requirements edit: the stamp no longer matches the hash.
    stamp = tmp_path / "venv" / ".mangaflow-bootstrap"
    stamp.write_text("0" * 32 + "\n", encoding="utf-8")
    rc, out = _run_harness_in(tmp_path, "true")
    assert rc == 0, out
    assert "ENSURE_RC=0" in out, out
    assert "PIP_CALLS=1" in out, "a stale stamp must trigger reinstall"
    assert f"STAMP={_requirements_hash()}" in out


def test_install_builds_one_r_flag_per_requirements_file(tmp_path):
    """The real (unoverridden) install step must pass `-r f1 -r f2`:
    `pip install -r f1 f2` parses the second path as a requirement string
    and fails. A stub `python` echoes its args, so the shape is pinned
    offline."""

    stub_venv = tmp_path / "stubvenv" / "bin"
    stub_venv.mkdir(parents=True)
    stub = stub_venv / "python"
    stub.write_text('#!/bin/sh\necho "ARGS=$@"\n', encoding="utf-8")
    stub.chmod(0o755)
    req_a = tmp_path / "req-a.txt"
    req_b = tmp_path / "req-b.txt"
    req_a.write_text("a==1\n", encoding="utf-8")
    req_b.write_text("b==2\n", encoding="utf-8")
    done = subprocess.run(
        [
            BASH, "-c",
            "source {script}\n"
            "install_e2e_requirements {venv} {a} {b}\n".format(
                script=_bash(_SCRIPT), venv=_bash(stub_venv.parent),
                a=_bash(req_a), b=_bash(req_b)
            ),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "install -q -r" in done.stdout, done.stdout
    assert f"-r {req_a.as_posix()} -r {req_b.as_posix()}" in done.stdout, done.stdout


def test_failed_venv_creation_propagates_before_the_stamp(tmp_path):
    # The venv-creation step carries the same explicit propagation as the
    # install step: a missing venv creator must fail the call (even from an
    # if-condition context, where set -e is suppressed) without writing a
    # stamp, so the next run retries.
    rc, out = _run_harness_in(
        tmp_path,
        # A failing venv creator earlier in PATH (coreutils stay reachable
        # so the harness itself still runs): it must pass the `-c ''`
        # capability probe like a real interpreter, then fail the actual
        # `python3 -m venv` with exit 3.
        'mkdir -p shim && '
        "printf '#!/bin/sh\\n[ \"$1\" = -m ] && exit 3\\nexit 0\\n' > shim/python3 "
        '&& chmod +x shim/python3 && PATH="$PWD/shim:$PATH"',
        fake_pip_rc=0,
    )
    assert "STAMP=MISSING" in out, out
    assert "ENSURE_RC=3" in out, out


def test_ensure_e2e_venv_refuses_zero_requirement_files(tmp_path):
    # The misuse guard: with no requirement files the stamp computation's
    # `cat "$@"` would read STDIN and hang the harness/CI forever. The
    # guard must return a DISTINCT rc=2 with a named diagnosis instead.
    done = subprocess.run(
        [BASH, "-c",
         f"source {_bash(_SCRIPT)} && "
         "if ensure_e2e_venv venv; then echo ENSURE_RC=0; "
         "else echo ENSURE_RC=$?; fi"],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
    )
    assert "ENSURE_RC=2" in done.stdout, done.stdout + done.stderr
    assert "no requirement files given" in done.stderr, done.stderr


def test_sourcing_the_runner_runs_nothing(tmp_path):
    # The source-guard: sourcing (as these tests do) must not run the main
    # body — a guard regression would exec pytest (this harness never
    # returns from that) or compute the venv path from $0. The old script
    # ran its whole body on source, which is exactly what made it
    # untestable.
    done = subprocess.run(
        [BASH, "-c",f"source {_bash(_SCRIPT)} && echo SOURCED_OK"],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
    )
    assert done.returncode == 0, done.stderr
    assert "SOURCED_OK" in done.stdout
    assert "PIP_CALLS" not in done.stdout


def test_scripts_layout_venv_is_recognized_without_recreation(tmp_path):
    """A Windows-hosted venv (Scripts/python.exe — the layout start-desktop.cmd
    requires) must resolve to its interpreter WITHOUT triggering a re-create
    or a re-install: the old bin-only predicate re-ran `python3 -m venv` on
    every invocation and the final exec failed, making the runner unusable
    on the platform whose layout this venv layout comes from."""

    rc, out = _run_harness_in(
        tmp_path,
        'mkdir -p "$VENV/Scripts" && printf "#!/bin/sh\\n" > "$VENV/Scripts/python.exe" '
        '&& chmod +x "$VENV/Scripts/python.exe"',
    )
    assert rc == 0, out
    assert "ENSURE_RC=0" in out, out
    assert "PIP_CALLS=1" in out, "no stamp yet: the install must run once"
    assert f"STAMP={_requirements_hash()}" in out
    # The second run must neither re-create nor re-install (the stamp now
    # matches through the Scripts-layout resolution).
    rc, out2 = _run_harness_in(tmp_path, "true")
    assert rc == 0, out2
    assert "PIP_CALLS=0" in out2, out2


def test_partial_scripts_layout_venv_self_heals(tmp_path):
    """Scripts/ present but no stamp (interrupted install on Windows) —
    same self-heal contract as the bin layout."""

    rc, out = _run_harness_in(
        tmp_path,
        'mkdir -p "$VENV/Scripts" && printf "#!/bin/sh\\n" > "$VENV/Scripts/python.exe" '
        '&& chmod +x "$VENV/Scripts/python.exe"',
        fake_pip_rc=1,
    )
    assert "ENSURE_RC=1" in out, out
    assert "STAMP=MISSING" in out, out
    rc, out2 = _run_harness_in(tmp_path, "true")
    assert rc == 0, out2
    assert "PIP_CALLS=1" in out2, "a retry must re-run the install"
    assert f"STAMP={_requirements_hash()}" in out2


def test_concurrent_bootstraps_install_exactly_once(tmp_path):
    """#586: two runners racing the bootstrap on a fresh checkout must
    serialize on the bootstrap lock — exactly ONE pip install into the
    shared venv, both callers succeed, one matching stamp. The winner's
    install is slowed so the loser arrives while the lock is held and
    exercises the wait-and-recheck path instead of a second install."""

    venv = tmp_path / "venv"
    cnt = tmp_path / "pip.calls"
    harness = f"""
set -euo pipefail
cd {_bash(tmp_path)}
source {_bash(_SCRIPT)}
install_e2e_requirements() {{
  sleep 2
  echo $(( $(cat {_bash(cnt)} 2>/dev/null || echo 0) + 1 )) > {_bash(cnt)}
}}
if ensure_e2e_venv {_bash(venv)} {_bash(_REQ)} {_bash(_REQ_DEV)}; then
  echo "ENSURE_RC=0"
else
  echo "ENSURE_RC=$?"
fi
"""
    first = subprocess.Popen(
        [BASH, "-c",harness], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    import time

    time.sleep(0.5)  # let the first runner take the lock and start installing
    second = subprocess.Popen(
        [BASH, "-c",harness], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    o1, e1 = first.communicate()
    o2, e2 = second.communicate()
    out1, out2 = o1 + e1, o2 + e2
    assert first.returncode == 0 and second.returncode == 0, (out1, out2)
    assert out1.count("ENSURE_RC=0") == 1 and out2.count("ENSURE_RC=0") == 1, (out1, out2)
    assert cnt.exists() and cnt.read_text().strip() == "1", (
        f"two concurrent bootstraps must pip-install exactly once, saw {cnt.read_text() if cnt.exists() else 'nothing'}"
    )
    assert (venv / ".mangaflow-bootstrap").read_text().strip() == _requirements_hash()
    assert not (tmp_path / "venv.bootstrap-lock").exists(), "the lock must be released"


def test_stale_bootstrap_lock_is_broken_and_install_proceeds(tmp_path):
    """A lock orphaned by a killed run (no live holder, silent for over 30
    minutes) must be broken instead of dead-locking every future run —
    otherwise the crash-safety fix becomes its own denial of service."""

    lock = tmp_path / "venv.bootstrap-lock"
    lock.mkdir(parents=True)
    # No touch.exe on Windows: the mtime probe rides the same bash as the
    # harness (and the lock path gets the forward-slash spelling).
    subprocess.run(
        [BASH, "-c",f"touch -d '31 minutes ago' {_bash(lock)}"], check=True
    )

    rc, out = _run_harness_in(tmp_path, "true")
    assert rc == 0, out
    assert "ENSURE_RC=0" in out, out
    assert "PIP_CALLS=1" in out, out
    assert not lock.exists(), "the stale lock must not survive the run"


def test_fresh_bootstrap_lock_is_never_stolen(tmp_path):
    """A FRESH lock (a live holder looks identical) must never be broken:
    the waiter gives up with a named diagnosis, leaves the lock in place,
    and installs nothing — a user removing the lock by hand stays the only
    way past a genuinely wedged bootstrap (the knob only shortens the
    budget for this test; the default is 900s)."""

    lock = tmp_path / "venv.bootstrap-lock"
    lock.mkdir(parents=True)

    rc, out = _run_harness_in(
        tmp_path, "true", env={"MANGAFLOW_E2E_BOOTSTRAP_MAX_WAIT": "1"}
    )
    assert rc == 0, out
    assert "ENSURE_RC=1" in out, out
    assert "bootstrap lock" in out and "remove it" in out, out
    assert "PIP_CALLS=0" in out, out
    assert lock.exists(), "a live-looking lock must not be removed behind its holder"


def test_runner_tees_the_last_run_log():
    """A load flake that only shows up once must keep its failure
    identity: the runner tees the full pytest output to a persistent
    dist/ log and propagates the exit code through the pipe (static
    contract — executing the main body would run the whole suite)."""

    script = _SCRIPT.read_text(encoding="utf-8")
    assert 'E2E_LOG_PATH="$DESKTOP_ROOT/dist/e2e-last-run.log"' in script
    assert '2>&1 | tee "$E2E_LOG_PATH" || pytest_exit=$?' in script
    assert 'exit "$pytest_exit"' in script
    assert "\nexec " not in script, (
        "the exec form would bypass the tee and lose the log on failure"
    )


def test_venv_creation_falls_back_when_python3_is_unusable(tmp_path):
    # Windows python.org installs ship python.exe without a python3 alias,
    # and the WindowsApps python3 stub is worse: it EXISTS on PATH but
    # exits 49 on every invocation. Either way the creator selection must
    # reject python3 and fall back to python — a probe-failing python3
    # shim plus a marker python shim proves the fallback ran and the flow
    # continued past creation. (Prepending shims beats a restricted PATH:
    # a git-bash `ln -s` copies the exe, and the copy cannot load
    # msys-2.0.dll, so symlinked coreutils exec with 127 there.)
    rc, out = _run_harness_in(
        tmp_path,
        'mkdir -p shim && '
        "printf '#!/bin/sh\\nexit 49\\n' > shim/python3 && chmod +x shim/python3 && "
        # The python shim emulates the two shapes the bootstrap calls it
        # in: the `-c ''` capability probe (must exit 0 like any real
        # interpreter) and `python -m venv <dir>` (argv: -m venv dir),
        # which must actually create the directory or the post-create
        # stamp write fails for an unrelated reason. The printf format is
        # shell-single-quoted so $3/$PWD reach the shim literally — the
        # harness itself runs set -u and would expand (or reject) them
        # first.
        "printf '#!/bin/sh\\nif [ \"$1\" = -m ]; then mkdir -p \"$3\"; : > \"$PWD/python.used\"; fi\\nexit 0\\n' > shim/python && "
        'chmod +x shim/python && PATH="$PWD/shim:$PATH"',
        fake_pip_rc=0,
    )
    assert rc == 0, out
    assert "ENSURE_RC=0" in out, out
    assert (tmp_path / "python.used").exists(), (
        f"the python fallback must have been the creator: {out}"
    )
    assert "PIP_CALLS=1" in out, (
        f"the bootstrap must continue into the install after the fallback: {out}"
    )


def test_venv_creation_reports_127_when_neither_creator_is_usable(tmp_path):
    # No usable creator on PATH — absent, or present-but-broken like the
    # WindowsApps stubs (exit 49 on every call): the documented loud
    # failure is a distinct 127, the remedy on stderr, and no stamp write
    # (the next run must retry, not accept a half state).
    rc, out = _run_harness_in(
        tmp_path,
        'mkdir -p shim && '
        "printf '#!/bin/sh\\nexit 49\\n' > shim/python3 && chmod +x shim/python3 && "
        "printf '#!/bin/sh\\nexit 49\\n' > shim/python && chmod +x shim/python && "
        'PATH="$PWD/shim:$PATH"',
        fake_pip_rc=0,
    )
    assert "ENSURE_RC=127" in out, out
    assert "no python3/python on PATH" in out, out
    assert "STAMP=MISSING" in out, out
    assert rc == 0  # the harness itself reports via ENSURE_RC, exits 0


def test_runner_rejects_pytest_selection_flags():
    """The trailing "$@" on the pytest invocation must never carry
    selection flags: `-k relay` would run a subset of the pinned contract
    files, pipefail+tee would preserve pytest's 0, and the last-run log
    would record a green full-contract run that never happened. Both the
    spaced and the ATTACHED spellings pytest accepts must be covered."""
    script = _SCRIPT.read_text(encoding="utf-8")
    for flag in ("-k", "-m", "--deselect", "--ignore", "-p",
                 "--collect-only", "-x", "--maxfail", "--lf", "--ff"):
        assert flag in script, f"the selection-flag guard must refuse {flag!r}"
    # argparse accepts `--opt=value`; a bare-token case lets those through.
    for attached in ("-k*", "-m*", "-p*", "--deselect=*", "--ignore=*", "--maxfail=*"):
        assert attached in script, (
            f"the guard must also refuse the attached form {attached!r}"
        )
    assert "exit 2" in script, "the guard must fail loudly, not filter silently"


def test_runner_rejects_a_selection_flag_end_to_end(tmp_path):
    """Behavioral form: invoking the runner body's arg check with each
    shape — spaced and attached — must exit 2 with the diagnosis before
    pytest is ever spawned."""
    probe = tmp_path / "argcheck.sh"
    # Extract the guard loop verbatim from the shipped script so the pin
    # tracks the real implementation instead of a copy.
    lines = _SCRIPT.read_text(encoding="utf-8").splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("for arg in"))
    end = next(i for i in range(start, len(lines)) if lines[i] == "done")
    probe.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        + "\n".join(lines[start : end + 1])
        + '\necho PYREACHED\n',
        encoding="utf-8",
        newline="\n",
    )
    refused = [
        ["-k", "relay"],
        ["--ignore=apps/desktop/scripts/test_sidecar_e2e.py"],
        ["-kexpr"],
        ["--deselect=tests::test_x"],
        ["--maxfail=1"],
        ["--lf"],
    ]
    for argv in refused:
        done = subprocess.run(
            [BASH, str(probe), *argv],
            capture_output=True,
            text=True,
        )
        assert done.returncode == 2, f"{argv}: {done.stdout + done.stderr}"
        assert "would silently shrink the contract run" in done.stderr, (
            f"{argv}: {done.stderr}"
        )
        assert "PYREACHED" not in done.stdout, f"{argv}: {done.stdout}"
    # A non-selection argument passes the guard untouched.
    done = subprocess.run(
        [BASH, str(probe), "--verbose-ok"],
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0 and "PYREACHED" in done.stdout, done.stdout + done.stderr
