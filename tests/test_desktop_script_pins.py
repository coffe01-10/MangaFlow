"""Bridge the apps/desktop/scripts contract pins into the default gate.

pytest.ini excludes apps/desktop from collection and the only other invoker
is the Windows-side run-sidecar-e2e.sh, so these behavioral pins (bind
policy, fake_channel shadow forms, assemble sweep, verify-static-origin)
were invisible to every automated gate (#819). They are platform-
independent; drive them as a subprocess suite from the normal gate.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_desktop_script_contract_pins_pass():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "apps/desktop/scripts",
            "-q",
            "--noconftest",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
