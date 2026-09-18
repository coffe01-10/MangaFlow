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

import shutil
import sys
import subprocess
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
def test_shell_core_lib_pins_pass():
    result = subprocess.run(
        ["cargo", "test", "--lib"],
        cwd=CRATE,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    # Guard against a vacuous pass: the bridge must have actually run the
    # suite, not collected zero tests (a filter typo fails silently).
    last = [line for line in result.stdout.splitlines() if line.startswith("test result")]
    assert last and "0 passed" not in last[-1], result.stdout[-2000:]
