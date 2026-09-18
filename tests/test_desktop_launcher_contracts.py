"""Content pins for the desktop launcher scripts (#895, #896).

These scripts (.cmd launcher, PowerShell measurement harness) have no
behavioral test surface in this repo (no Pester; a .cmd behavioral harness
would need a launchable stub shell). The precedent (test_start_dev_ps1_contracts.py)
is pinning the load-bearing lines of the script text so the guarantees the
issues closed cannot silently regress.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CMD = REPO / "apps/desktop/scripts/start-desktop.cmd"
PS1 = REPO / "apps/desktop/scripts/measure-native-startup.ps1"
LOCK_SH = REPO / "apps/desktop/scripts/dist-build-lock.sh"


def test_start_desktop_clears_inherited_web_dist_before_the_bundle_check():
    """#895: setlocal copies the parent env — without an explicit clear, a
    MANGAFLOW_DESKTOP_WEB_DIST set once survives every later launch whose
    repo bundle is absent, and the shell silently serves a stale/foreign
    tree. The clear must precede the `if exist` conditional."""
    lines = CMD.read_text(encoding="utf-8").splitlines()
    clear = next(
        (i for i, line in enumerate(lines) if 'set "MANGAFLOW_DESKTOP_WEB_DIST="' in line),
        -1,
    )
    assert clear >= 0, "launcher must clear an inherited MANGAFLOW_DESKTOP_WEB_DIST (#895)"
    conditional = next(
        (i for i, line in enumerate(lines) if "dist\\web-standalone\\server.js" in line),
        -1,
    )
    assert conditional > clear, (
        "the clear must precede the repo-bundle conditional — otherwise an "
        "absent bundle leaves the inherited value live (#895)"
    )


def test_measure_startup_kills_through_the_held_process_object():
    """#896: the identity of the killed tree is pinned by the Process handle
    held since Start(); the old name-check-then-taskkill re-resolved the pid
    a second time, and a pid recycled in that window was tree-killed (/T /F)
    with no check left."""
    text = PS1.read_text(encoding="utf-8")
    assert "[System.Diagnostics.Process]$Sample" in text, (
        "Stop-SampleTree must take the held Process object, not a pid (#896)"
    )
    assert "[int]$SamplePid" not in text, "no pid-parameterized kill path may remain (#896)"
    assert "taskkill /PID $SamplePid" not in text, (
        "the unchecked pid-reuse-prone taskkill form must not return (#896)"
    )
    assert "$Sample.Kill($true)" in text, "PS7+ must tree-kill handle-pinned (#896)"


def test_measure_startup_keeps_a_windows_powershell_51_fallback():
    """Kill(entireProcessTree) exists only on .NET Core 3+; this repo still
    runs Windows PowerShell 5.1 for its launchers (test_start_dev_ps1_contracts
    documents 5.1 semantics). A bare Kill($true) would break the default
    powershell.exe path — the version branch and the fallback must both stay."""
    text = PS1.read_text(encoding="utf-8")
    assert "$PSVersionTable.PSVersion.Major -ge 7" in text, (
        "the Kill($true) arm must be gated to PS7+ (#896)"
    )
    assert "Get-DescendantPids -RootPid $Sample.Id" in text, (
        "the 5.1 fallback must snapshot descendants from the held object (#896)"
    )


def test_measure_startup_finally_passes_the_held_object_without_a_pid_relookup():
    """The finally's belt-and-braces kill used to re-resolve by pid
    (Get-Process -Id $proc.Id) — the second pid-reuse window. It must pass
    the held object straight through."""
    text = PS1.read_text(encoding="utf-8")
    assert "Stop-SampleTree $proc" in text, "call sites must pass the held object (#896)"
    assert "Get-Process -Id $proc.Id" not in text, (
        "no pid re-lookup before the kill — HasExited on the held object covers it (#896)"
    )


def test_dist_lock_sh_refuses_links_and_opens_without_truncation():
    """#891: the flock arm must refuse a symlinked lock path before any open
    and open with `<>` (O_RDWR|O_CREAT — the python twin's flags), never a
    truncating `>` redirect."""
    text = LOCK_SH.read_text(encoding="utf-8")
    assert 'if [ -L "$lock_path" ]; then' in text, (
        "the link refusal must precede the arm split (#891)"
    )
    assert 'exec 9<>"$lock_path"' in text, (
        "the flock arm must open without truncation (#891)"
    )
    assert 'exec 9>"$lock_path"' not in text, (
        "the truncating redirect must not return (#891)"
    )
