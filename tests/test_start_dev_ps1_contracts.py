"""Static contract pins for scripts/start-dev.ps1 (no PowerShell needed).

The dev launcher's .env read must pin UTF-8 (#413, fixed in #450):
Windows PowerShell 5.1's Get-Content defaults to ANSI, so a non-ASCII
REDIS_URL or proxy credential round-tripped as mojibake and was silently
promoted into the environment — where python-dotenv would not override it.
Run-verification of the script itself is Windows-side; this pin keeps the
encoding contract from regressing unnoticed on POSIX check runs.
"""

from __future__ import annotations

import re
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "start-dev.ps1"


def test_dotenv_read_pins_utf8_encoding():
    source = SCRIPT.read_text(encoding="utf-8")
    assert re.search(r'Get-Content -LiteralPath "\.env" -Encoding UTF8', source), (
        "the .env read must pin -Encoding UTF8 (PS 5.1 defaults to ANSI)"
    )
