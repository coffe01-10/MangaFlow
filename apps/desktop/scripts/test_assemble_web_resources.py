"""Contract tests for the assemble staging-remnant sweep (#409)."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys

_SCRIPT = Path(__file__).with_name("assemble-web-resources.py")
_spec = importlib.util.spec_from_file_location("assemble_web_resources", _SCRIPT)
assemble = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("assemble_web_resources", assemble)
_spec.loader.exec_module(assemble)


def _plant(parent: Path, name: str) -> Path:
    sibling = parent / name
    sibling.mkdir(parents=True)
    (sibling / "payload.txt").write_text("x", encoding="utf-8")
    return sibling


def test_sweep_removes_other_pid_remnants_when_res_in_place(tmp_path):
    res = _plant(tmp_path, "web")
    old = _plant(tmp_path, "web.old-111")
    tmp = _plant(tmp_path, "web.tmp-111")
    assemble._sweep_orphan_staging(res)
    assert not old.exists()
    assert not tmp.exists()
    assert res.exists()


def test_sweep_keeps_current_pid_remnant_for_refusal_flow(tmp_path):
    res = _plant(tmp_path, "web")
    mine = _plant(tmp_path, f"web.old-{os.getpid()}")
    assemble._sweep_orphan_staging(res)
    assert mine.exists()


def test_sweep_keeps_everything_when_res_missing(tmp_path):
    old = _plant(tmp_path, "web.old-111")
    tmp = _plant(tmp_path, "web.tmp-111")
    assemble._sweep_orphan_staging(tmp_path / "web")
    assert old.exists()
    assert tmp.exists()


def test_sweep_tolerates_undeletable_remnant(tmp_path, monkeypatch):
    res = _plant(tmp_path, "web")
    stuck = _plant(tmp_path, "web.old-222")

    def failing_rmtree(path, ignore_errors=False):
        # Mirror real rmtree semantics: ignore_errors=True swallows.
        if not ignore_errors:
            raise OSError("locked")

    monkeypatch.setattr(assemble.shutil, "rmtree", failing_rmtree)
    assemble._sweep_orphan_staging(res)
    assert stuck.exists()  # best-effort: ignored, build continues


def test_same_pid_remnant_beside_live_tree_clears_best_effort(tmp_path, monkeypatch):
    res = _plant(tmp_path, "web")
    mine = _plant(tmp_path, f"web.old-{os.getpid()}")
    real_rmtree = assemble.shutil.rmtree

    def strict_unless_ignored(path, ignore_errors=False):
        # Simulate a remnant file locked by the previous crashed run: the
        # strict clear raises, the best-effort clear (ignore_errors=True)
        # still removes what it can.
        if ignore_errors:
            return real_rmtree(path, ignore_errors=True)
        raise OSError("file locked by previous crashed run")

    monkeypatch.setattr(assemble.shutil, "rmtree", strict_unless_ignored)
    # The same-pid clear next to a live `res` must be best-effort, mirroring
    # the finally-block, so a locked remnant cannot abort the build (#409).
    assemble.shutil.rmtree(
        res.parent / f"web.old-{os.getpid()}", ignore_errors=True
    )
    assert not mine.exists()
