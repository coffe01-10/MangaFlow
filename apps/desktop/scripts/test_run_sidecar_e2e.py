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

_SCRIPT = Path(__file__).with_name("run-sidecar-e2e.sh").resolve()
_REQ = Path(__file__).resolve().parents[2] / "api" / "requirements.txt"
_REQ_DEV = Path(__file__).resolve().parents[2] / "api" / "requirements-dev.txt"


def _requirements_hash() -> str:
    hashed = subprocess.run(
        ["bash", "-c", f"cat {_REQ} {_REQ_DEV} | md5sum | cut -d' ' -f1"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return hashed


def _run_harness_in(workdir: Path, setup: str, fake_pip_rc: int = 0,
                    env: dict[str, str] | None = None) -> tuple[int, str]:
    harness = f"""
set -euo pipefail
cd {workdir}
source {_SCRIPT}
VENV="$1"
CNT="pip.calls.$$"
install_e2e_requirements() {{
  echo $(( $(cat "$CNT" 2>/dev/null || echo 0) + 1 )) > "$CNT"
  return {fake_pip_rc}
}}
{setup}
if ensure_e2e_venv "$VENV" {_REQ} {_REQ_DEV}; then
  echo "ENSURE_RC=0"
else
  echo "ENSURE_RC=$?"
fi
echo "PIP_CALLS=$(cat "$CNT" 2>/dev/null || echo 0)"
echo "STAMP=$(cat "$VENV/.mangaflow-bootstrap" 2>/dev/null || echo MISSING)"
"""
    done = subprocess.run(
        ["bash", "-c", harness, "harness", str(workdir / "venv")],
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
    )
    return done.returncode, done.stdout + done.stderr


def test_fresh_venv_installs_and_stamps(tmp_path):
    rc, out = _run_harness_in(tmp_path, "true")
    assert rc == 0, out
    assert "ENSURE_RC=0" in out, out
    assert "PIP_CALLS=1" in out, out
    assert f"STAMP={_requirements_hash()}" in out, out
    assert (tmp_path / "venv" / "bin" / "python").exists(), out


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
            "bash", "-c",
            "source {script}\n"
            "install_e2e_requirements {venv} {a} {b}\n".format(
                script=_SCRIPT, venv=stub_venv.parent, a=req_a, b=req_b
            ),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "install -q -r" in done.stdout, done.stdout
    assert f"-r {req_a} -r {req_b}" in done.stdout, done.stdout


def test_failed_venv_creation_propagates_before_the_stamp(tmp_path):
    # The venv-creation step carries the same explicit propagation as the
    # install step: a missing venv creator must fail the call (even from an
    # if-condition context, where set -e is suppressed) without writing a
    # stamp, so the next run retries.
    rc, out = _run_harness_in(
        tmp_path,
        # A failing venv creator earlier in PATH (coreutils stay reachable
        # so the harness itself still runs): `python3 -m venv` exits 3.
        'mkdir -p shim && printf "#!/bin/sh\\nexit 3\\n" > shim/python3 '
        '&& chmod +x shim/python3 && PATH="$PWD/shim:$PATH"',
        fake_pip_rc=0,
    )
    assert "STAMP=MISSING" in out, out
    assert "ENSURE_RC=3" in out, out


def test_sourcing_the_runner_runs_nothing(tmp_path):
    # The source-guard: sourcing (as these tests do) must not run the main
    # body — a guard regression would exec pytest (this harness never
    # returns from that) or compute the venv path from $0. The old script
    # ran its whole body on source, which is exactly what made it
    # untestable.
    done = subprocess.run(
        ["bash", "-c", f"source {_SCRIPT} && echo SOURCED_OK"],
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
cd {tmp_path}
source {_SCRIPT}
install_e2e_requirements() {{
  sleep 2
  echo $(( $(cat {cnt} 2>/dev/null || echo 0) + 1 )) > {cnt}
}}
if ensure_e2e_venv {venv} {_REQ} {_REQ_DEV}; then
  echo "ENSURE_RC=0"
else
  echo "ENSURE_RC=$?"
fi
"""
    first = subprocess.Popen(
        ["bash", "-c", harness], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    import time

    time.sleep(0.5)  # let the first runner take the lock and start installing
    second = subprocess.Popen(
        ["bash", "-c", harness], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
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

    venv = tmp_path / "venv"
    lock = tmp_path / "venv.bootstrap-lock"
    lock.mkdir(parents=True)
    subprocess.run(["touch", "-d", "31 minutes ago", str(lock)], check=True)

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

    venv = tmp_path / "venv"
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
