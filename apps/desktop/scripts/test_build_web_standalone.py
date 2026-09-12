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
import os
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


def test_replace_dist_refuses_when_the_stamp_write_fails(tmp_path, monkeypatch):
    """The stamp write (temp name + os.replace) is the last step, but it can
    still fail (read-only dist parent, disk full). The bundle is already in
    place at that point — the failure must propagate (a bundle without its
    provenance stamp is exactly what the e2e refuses) and no .tmp residue
    may linger next to a live bundle."""

    standalone = _standalone_at(tmp_path)
    dist = _stale_dist_at(tmp_path)
    monkeypatch.setattr(bw, "_git", lambda *args: "abc123")

    def failing_write_text(self, data, encoding=None):
        raise OSError("dist parent is read-only")

    monkeypatch.setattr(Path, "write_text", failing_write_text)
    with pytest.raises(OSError, match="read-only"):
        bw._replace_dist(standalone, dist)
    monkeypatch.undo()
    # The bundle moved in (the move precedes the stamp), but no stamp and
    # no temp residue may exist — the next run's e2e must refuse it, not
    # silently trust an unstamped tree.
    assert (dist / "server.js").read_text(encoding="utf-8") == "module.exports = 1;"
    assert not (dist / "build-info.json").exists()
    assert not (dist / ".build-info.json.tmp").exists()


def test_npm_shim_is_platform_resolved_and_actually_used(monkeypatch):
    """Adjacent-seam pin (npm shim/platform paths): the npm CLI shim is
    npm.cmd on Windows and npm everywhere else, and `main()` must invoke
    THAT constant — a hardcoded npm.cmd (the historical shim bug class)
    breaks Linux/CI, and a call site that bypasses the constant would
    break the other platform. The fake subprocess records the argv and
    env before stopping main, so the shape is pinned offline (no build)."""

    expected = "npm.cmd" if sys.platform == "win32" else "npm"
    assert bw.NPM == expected, (
        "the npm shim must resolve per platform, not be hardcoded"
    )

    recorded = {}

    def fake_run(argv, **kwargs):
        recorded["argv"] = argv
        recorded["kwargs"] = kwargs
        raise RuntimeError("stop-main-before-build")

    monkeypatch.setattr(bw.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="stop-main-before-build"):
        bw.main()

    assert recorded["argv"][0] == bw.NPM, recorded["argv"]
    assert recorded["argv"][1:4] == ["run", "build", "--workspace"]
    assert recorded["argv"][4] == "@mangaflow/web"
    # A silent build failure must stay impossible, and the build must run
    # from the repo root regardless of the caller's cwd.
    assert recorded["kwargs"]["check"] is True
    assert recorded["kwargs"]["cwd"] == bw.REPO
    # The desktop bundle bakes the helper's fixed relay port at build time.
    # Identity check: the env must be a FRESH dict with the override, not a
    # pass-through of os.environ — on a host that happens to export the
    # value, a dropped override would otherwise slip past the value compare.
    env = recorded["kwargs"]["env"]
    assert env is not os.environ
    assert env["MANGAFLOW_API_ORIGIN"] == "http://127.0.0.1:39443"


def test_relay_origin_is_single_sourced_with_the_helper():
    """Cross-module pin: the build script's RELAY_ORIGIN (baked into the
    bundle's rewrites and verified against the compiled manifest) and the
    helper's WEB_RELAY_PORT (the runtime relay owner) are independent
    literals in two modules. If they drift, NOTHING fails at build or
    startup: the bundle bakes a port nobody relays, the dashboard's API
    calls are refused, and plan-B is silently broken in production. The
    in-file leg (main()'s env literal vs RELAY_ORIGIN) is incidentally
    guarded by the manifest verification; the cross-module leg had no pin
    at all until this one."""

    import importlib.util

    helper_spec = importlib.util.spec_from_file_location(
        "mangaflow_desktop_helper_relay",
        str(_SCRIPT.parent.parent / "sidecar" / "mangaflow_desktop_helper.py"),
    )
    helper = importlib.util.module_from_spec(helper_spec)
    helper_spec.loader.exec_module(helper)

    assert bw.RELAY_ORIGIN == f"http://127.0.0.1:{helper.WEB_RELAY_PORT}", (
        "the baked rewrite origin and the runtime relay port must stay the "
        "same constant (39443) across build-web-standalone and the helper"
    )
