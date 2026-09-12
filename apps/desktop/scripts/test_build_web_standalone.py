"""Contract tests for build-web-standalone's destructive tail (#461).

The historical defect: the strict ``shutil.rmtree(DESKTOP_DIST)`` aborted
mid-delete on one locked file (AV/indexer/running app), leaving a truncated
bundle whose PREVIOUS build-info.json survived — and on a rebuild from the
same commit the e2e freshness gate then passed over the truncated tree.
The swap must clear best-effort and refuse (never merge) when a remnant
survives, and build-info.json must land via a temp name + os.replace after
the move so the stamp always describes the payload it arrived with.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).with_name("build-web-standalone.py")
_spec = importlib.util.spec_from_file_location("build_web_standalone", _SCRIPT)
bw = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("build_web_standalone", bw)
_spec.loader.exec_module(bw)


def _standalone_at(tmp_path: Path) -> Path:
    standalone = tmp_path / "standalone" / "apps" / "web"
    (standalone / ".next").mkdir(parents=True)
    (standalone / "server.js").write_text("module.exports = 1;", encoding="utf-8")
    return standalone


def _stale_dist_at(tmp_path: Path) -> Path:
    dist = tmp_path / "web-standalone"
    dist.mkdir()
    (dist / "server.js").write_text("stale", encoding="utf-8")
    (dist / "build-info.json").write_text(
        json.dumps({"source_commit": "stale"}), encoding="utf-8"
    )
    return dist


def test_replace_dist_refuses_when_a_remnant_survives(tmp_path, monkeypatch):
    """A locked file (rmtree fails even best-effort) must abort the swap
    loudly — never move the fresh bundle INTO the half-deleted remnant
    with its stale build-info.json still inside (#461's exact trap)."""

    standalone = _standalone_at(tmp_path)
    dist = _stale_dist_at(tmp_path)

    def locked_rmtree(path, ignore_errors=False):
        raise OSError("file locked by another process")

    monkeypatch.setattr(bw.shutil, "rmtree", locked_rmtree)
    with pytest.raises(SystemExit) as refusal:
        bw._replace_dist(standalone, dist)
    assert "could not fully clear" in str(refusal.value)
    # The fresh bundle was NOT merged into the truncated remnant.
    assert (dist / "server.js").read_text(encoding="utf-8") == "stale"
    assert (standalone / "server.js").is_file(), "standalone stays in place"


def test_replace_dist_swaps_and_stamps_atomically(tmp_path, monkeypatch):
    """Happy path: remnant clears, bundle moves, build-info lands via a
    temp name + os.replace (no .tmp residue, parsed content matches)."""

    standalone = _standalone_at(tmp_path)
    dist = _stale_dist_at(tmp_path)
    monkeypatch.setattr(bw, "_git", lambda *args: "abc123")

    bw._replace_dist(standalone, dist)

    assert (dist / "server.js").read_text(encoding="utf-8") == "module.exports = 1;"
    assert not standalone.exists(), "the source tree was moved, not copied"
    stamped = json.loads((dist / "build-info.json").read_text(encoding="utf-8"))
    assert stamped == {
        "source_commit": "abc123",
        "apps_web_tree": "abc123",
        "relay_origin": bw.RELAY_ORIGIN,
    }
    assert not (dist / ".build-info.json.tmp").exists(), "temp stamp must not linger"
    assert not (dist / "stale").exists()


def test_best_effort_clear_is_noop_for_missing_path(tmp_path):
    target = tmp_path / "never-existed"
    bw._best_effort_clear(target, "the target")  # must not raise
    assert not target.exists()
