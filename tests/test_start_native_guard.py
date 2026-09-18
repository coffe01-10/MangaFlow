"""#876: the start-native.ps1 overlap guard must compare CANONICAL paths.

The old guard compared lexical GetFullPath spellings — a junction or 8.3
alias of the shell data directory compared unequal and slipped past, giving
the exact #410 double-writer (second API server on one SQLite DB) plus the
#600 cross-client sweep. The guard now canonicalizes both sides through
GetFinalPathNameByHandle (P/Invoke — the .NET ResolveLinkTarget is .NET 6+
while the launcher documents Windows PowerShell 5.1) before the
both-directions nesting compare.

The behavioral test extracts the guard region verbatim (marker → the build
section) and drives it through a real junction — the same extract-function
pattern as tests/test_build_frontend_static_junction.py.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
LAUNCHER = REPO / "apps/desktop/scripts/start-native.ps1"

_REGION_START = "# #876"
_REGION_END = "Push-Location"


def _guard_region() -> str:
    text = LAUNCHER.read_text(encoding="utf-8-sig")
    start = text.index(_REGION_START)
    end = text.index(_REGION_END, start)
    return text[start:end]


def test_guard_canonicalizes_both_sides_before_comparing():
    """Content pin: the canonicalization call must feed the nesting compare —
    a revert to bare GetFullPath spelling comparisons is what #876 caught."""
    region = _guard_region()
    assert "Get-CanonicalPath" in region, (
        "the overlap guard must canonicalize before comparing (#876)"
    )
    assert "GetFinalPathNameByHandleW" in region, (
        "canonicalization must resolve reparse points/8.3 — GetFullPath does not (#876)"
    )
    assert "Refusing -UserData" in region, "the refusal itself must stay"


@pytest.mark.skipif(
    __import__("os").name != "nt",
    reason="the junction fixture and PowerShell guard are Windows-only",
)
def test_guard_refuses_a_junction_alias_of_the_shell_data(tmp_path: Path):
    """Behavioral: a junction pointing AT the shell data directory (the alias
    the lexical compare missed) must be refused, naming the canonical path —
    and a disjoint path must still pass (the guard must not over-fire)."""

    shell_data = Path.home() / "AppData/Local/com.mangaflow.desktop"
    shell_data.mkdir(parents=True, exist_ok=True)
    junction = tmp_path / "alias-to-shell"
    probe = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            f"New-Item -ItemType Junction -Path '{junction}' -Target '{shell_data}' | Out-Null",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert probe.returncode == 0, probe.stderr
    try:
        def run_guard(user_data: Path) -> str:
            body = (
                f"$UserData = '{user_data}'\n"
                + _guard_region()
                + "\nWrite-Output 'GUARD-PASSED'"
            )
            script = tmp_path / "guard-body.ps1"
            script.write_text(body, encoding="utf-8-sig")
            # Bytes + ASCII markers: the refusal message is localized (GBK on
            # a zh host, cp1252 on the runner) — any single decode would
            # mis-read one of them.
            done = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script),
                ],
                capture_output=True,
                timeout=120,
            )
            combined = done.stdout + done.stderr
            assert (
                b"GUARD-PASSED" in combined or b"Refusing -UserData" in combined
            ), combined
            return "PASSED" if b"GUARD-PASSED" in combined else "REFUSED"

        assert run_guard(junction) == "REFUSED", (
            "a junction alias of the shell data must be refused after "
            "canonicalization (#876)"
        )
        assert run_guard(junction / "nested") == "REFUSED", (
            "a path nested inside the alias must be refused too (#876)"
        )
        assert run_guard(tmp_path / "clean-user-data") == "PASSED", (
            "a disjoint path must still pass the guard (#876)"
        )
    finally:
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"Remove-Item -LiteralPath '{junction}' -Force -ErrorAction SilentlyContinue",
            ],
            capture_output=True,
            timeout=60,
        )
