"""Contract tests for the pre-bundle frontend-dist guard (#444).

`tauri build` bundles whatever sits at apps/desktop/dist/frontend
(frontendDist in tauri.conf.json). The tracked index.html is a PLACEHOLDER
whose `_next/static/chunks/*` references are all gitignored, so building
from a clean clone would ship a white-screen installer. The guard wired as
tauri's beforeBuildCommand must refuse dangling references and pass only a
complete export.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent
GUARD = _SCRIPTS / "guard-frontend-dist.mjs"
CONF = _SCRIPTS.parent / "src-tauri" / "tauri.conf.json"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="the guard itself is a node script"
)


def _run_guard(dist: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["node", str(GUARD), str(dist)],
        capture_output=True,
        text=True,
    )


def _complete_export(root: Path) -> Path:
    dist = root / "frontend"
    (dist / "_next" / "static" / "chunks").mkdir(parents=True)
    (dist / "_next" / "static" / "chunks" / "app.js").write_text(
        "// chunk", encoding="utf-8"
    )
    (dist / "_next" / "static" / "chunks" / "app.css").write_text(
        "/* chunk */", encoding="utf-8"
    )
    (dist / "index.html").write_text(
        '<html><head><link rel="stylesheet" href="/_next/static/chunks/app.css"/>'
        '<script src="/_next/static/chunks/app.js"></script></head></html>',
        encoding="utf-8",
    )
    return dist


def test_guard_refuses_the_dangling_reference_placeholder(tmp_path):
    """The exact clean-clone shape: an index.html whose referenced chunks
    are absent must refuse with the build-frontend-static.sh remedy."""

    dist = tmp_path / "frontend"
    dist.mkdir()
    (dist / "index.html").write_text(
        '<html><head><script src="/_next/static/chunks/missing.js" async="">'
        "</script></head></html>",
        encoding="utf-8",
    )
    result = _run_guard(dist)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "build-frontend-static.sh" in result.stderr


def test_guard_passes_a_complete_export(tmp_path):
    result = _run_guard(_complete_export(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "all present" in result.stdout


def test_guard_refuses_a_missing_frontend_dir(tmp_path):
    result = _run_guard(tmp_path / "frontend")
    assert result.returncode == 1
    assert "missing" in result.stderr


def test_tauri_conf_wires_the_guard_as_before_build_command():
    """The guard must actually run in `tauri build`: the object form pins
    cwd to the desktop root (tauri runs hooks from src-tauri by default),
    independent of where the CLI was invoked."""

    conf = json.loads(CONF.read_text(encoding="utf-8"))
    hook = conf["build"]["beforeBuildCommand"]
    assert hook == {
        "script": "node scripts/guard-frontend-dist.mjs",
        "cwd": "..",
    }
    assert conf["build"]["frontendDist"] == "../dist/frontend"
