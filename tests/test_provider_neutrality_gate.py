"""Regression tests for the provider-neutrality grep gate (Issue #41, audit M13).

The gate is a PowerShell 5.1 script; these tests exercise its documented
0/1/2 exit-code contract: pass on the current repo, fail closed on allowlisted
violations, environment errors on missing allowlist, and the one-shot
``-UpdateAllowlist`` bootstrap that only accepts an empty allowlist. All runs
are local; no credentials and no provider calls are involved.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_SCRIPT = REPO_ROOT / "scripts" / "check-provider-neutrality.ps1"
EMPTY_ALLOWLIST = (
    "# Generated baseline; remove a path when its final allowed hit is removed.\n"
)

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="provider-neutrality gate is Windows PowerShell only"
)


def run_gate(script: Path, *args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            *args,
        ],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )


def test_gate_passes_on_current_repo():
    result = run_gate(REPO_SCRIPT, cwd=REPO_ROOT)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


def make_sandbox_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "apps" / "demo").mkdir(parents=True)
    (root / "scripts").mkdir()
    shutil.copy(REPO_SCRIPT, root / "scripts" / "check-provider-neutrality.ps1")
    (root / "scripts" / "provider-neutrality-allowlist.txt").write_text(
        EMPTY_ALLOWLIST, encoding="utf-8"
    )
    (root / "apps" / "demo" / "native.py").write_text(
        'PROTOCOL = "VERTEX_NATIVE"\n', encoding="utf-8"
    )
    (root / "apps" / "demo" / "other.py").write_text(
        'PROVIDER = "vertex-ai"\n', encoding="utf-8"
    )
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=True, capture_output=True)
    subprocess.run(["git", "add", "apps"], cwd=str(root), check=True, capture_output=True)
    return root


def test_gate_fails_and_prints_violations_outside_allowlist(tmp_path):
    root = make_sandbox_repo(tmp_path)
    outside = tmp_path / "outside-cwd"
    outside.mkdir()
    result = run_gate(root / "scripts" / "check-provider-neutrality.ps1", cwd=outside)
    assert result.returncode == 1, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "apps/demo/native.py:1" in result.stdout
    assert "apps/demo/other.py:1" in result.stdout


def test_gate_allows_listed_paths(tmp_path):
    root = make_sandbox_repo(tmp_path)
    allowlist = root / "scripts" / "provider-neutrality-allowlist.txt"
    allowlist.write_text(
        "# comment line\napps/demo/native.py\napps/demo/other.py\n", encoding="utf-8"
    )
    result = run_gate(root / "scripts" / "check-provider-neutrality.ps1", cwd=root)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


def test_gate_missing_allowlist_exits_two(tmp_path):
    root = make_sandbox_repo(tmp_path)
    (root / "scripts" / "provider-neutrality-allowlist.txt").unlink()
    result = run_gate(root / "scripts" / "check-provider-neutrality.ps1", cwd=root)
    assert result.returncode == 2
    assert "allowlist missing" in result.stderr


def test_update_allowlist_rejects_non_empty_allowlist(tmp_path):
    root = make_sandbox_repo(tmp_path)
    allowlist = root / "scripts" / "provider-neutrality-allowlist.txt"
    allowlist.write_text("apps/demo/native.py\n", encoding="utf-8")
    result = run_gate(
        root / "scripts" / "check-provider-neutrality.ps1", "-UpdateAllowlist", cwd=root
    )
    assert result.returncode == 2
    assert "-UpdateAllowlist requires an empty allowlist" in result.stderr


def test_update_allowlist_generates_sorted_unique_utf8_without_bom(tmp_path):
    root = make_sandbox_repo(tmp_path)
    # A third distinct file plus a repeated hit inside one file exercises
    # sorting and deduplication of the generated path set.
    (root / "apps" / "demo" / "zzz.py").write_text('X = "vertex_configured"\n', encoding="utf-8")
    (root / "apps" / "demo" / "native.py").write_text(
        'PROTOCOL = "VERTEX_NATIVE"\nALIAS = "VERTEX_NATIVE"\n', encoding="utf-8"
    )
    subprocess.run(["git", "add", "apps"], cwd=str(root), check=True, capture_output=True)
    outside = tmp_path / "outside-cwd"
    outside.mkdir()
    result = run_gate(
        root / "scripts" / "check-provider-neutrality.ps1", "-UpdateAllowlist", cwd=outside
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"

    raw = (root / "scripts" / "provider-neutrality-allowlist.txt").read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "allowlist must be UTF-8 without BOM"
    lines = raw.decode("utf-8").splitlines()
    paths = [line for line in lines if line and not line.startswith("#")]
    assert paths == [
        "apps/demo/native.py",
        "apps/demo/other.py",
        "apps/demo/zzz.py",
    ]

    follow_up = run_gate(root / "scripts" / "check-provider-neutrality.ps1", cwd=outside)
    assert follow_up.returncode == 0, follow_up.stdout
