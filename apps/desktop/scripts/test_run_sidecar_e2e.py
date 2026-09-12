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


def _run_harness_in(workdir: Path, setup: str, fake_pip_rc: int = 0) -> tuple[int, str]:
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
