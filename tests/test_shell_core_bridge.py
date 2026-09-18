"""Bridge the shell-core Rust pins into the default pytest gate.

The desktop-script contract pins got a subprocess bridge (#819/#835);
the Rust side never did: check.yml runs `cargo build` at most, so the
in-file `#[cfg(unix)]` sweep/export/journal pins never executed in any
automated gate — a regression there was visible only to someone who
happened to run cargo by hand.

POSIX-gated on purpose: the Windows-host cargo legs have never been
exercised (the cfg(unix) pins skip there, and the win32 behavior is
unverified), so this bridge keeps the Windows CI posture unchanged
while making every Linux run — dev and CI — execute the Rust suite.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

CRATE = Path(__file__).resolve().parents[1] / "apps" / "desktop" / "shell-core"


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="the Windows-host cargo legs are unverified; bridge is POSIX-gated",
)
@pytest.mark.skipif(
    shutil.which("cargo") is None,
    reason="cargo not installed",
)
def test_shell_core_all_target_pins_pass():
    # Full targets, not --lib: the integration suites (startup_protocol,
    # log_export, log_rotation, picker_policy, delivery_contract — ~70
    # tests) also carry pins and also ran in no gate before this bridge.
    result = subprocess.run(
        ["cargo", "test"],
        cwd=CRATE,
        capture_output=True,
        text=True,
        timeout=900,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    # Guard against a vacuous pass: full targets print one result line per
    # target (empty doc-test targets legitimately say "0 passed"), so the
    # check sums the passed counts and demands the suite's real magnitude.
    result_lines = [
        line for line in result.stdout.splitlines() if line.startswith("test result")
    ]
    assert result_lines, "cargo produced no test result lines"
    total = sum(
        int(match)
        for line in result_lines
        for match in re.findall(r"(\d+) passed", line)
    )
    assert total >= 100, f"suspiciously few tests ran: {result_lines}"
    assert not any("FAILED" in line for line in result_lines), result_lines
