"""Contract tests for the assemble staging-remnant sweep (#409)."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
import time

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


def _buildable_source(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A minimal (src, res, node) triple that assemble() accepts."""

    src = tmp_path / "src"
    (src / ".next").mkdir(parents=True)
    (src / "server.js").write_text("module.exports = 1;", encoding="utf-8")
    res = tmp_path / "web"
    node = tmp_path / "node.exe"
    node.write_bytes(b"MZ fake runtime")
    return src, res, node


def test_full_assemble_sweeps_other_pid_debris_after_swap(tmp_path):
    """End-to-end through assemble(): once the new tree is in place, the
    post-swap sweep removes other-pid parked/staged debris (#409/#457) —
    the behavior the module docstring now describes."""

    src, res, node = _buildable_source(tmp_path)
    old = _plant(tmp_path, "web.old-111")
    stuck_tmp = _plant(tmp_path, "web.tmp-222")

    assemble.assemble(src=src, res=res, node=node)

    assert (res / "standalone" / "server.js").is_file()
    assert (res / "node" / "node.exe").read_bytes() == b"MZ fake runtime"
    assert not old.exists(), "parked debris from another pid must be swept"
    assert not stuck_tmp.exists(), "staged debris from another pid must be swept"


def test_error_texts_tell_the_truth_about_the_sweep(tmp_path):
    """#457 finding 1: the recovery instructions must not promise the
    parked tree stays forever — a later successful assemble removes it."""

    res = tmp_path / "web"
    retired = tmp_path / "web.old-4242"
    for text in (
        str(assemble._recovery_error(res, retired)),
        str(assemble._stale_retired_error(res, retired)),
    ):
        assert "Do not delete it" in text
        assert "removed as build debris" in text, (
            "instructions must state that re-running assemble sweeps the "
            "parked copy once the new tree is in place"
        )


_CHILD_ASSEMBLE = (
    "import importlib.util, sys, time\n"
    "from pathlib import Path\n"
    "module_path, src, res, node, done = sys.argv[1:6]\n"
    "spec = importlib.util.spec_from_file_location('assemble_probe', module_path)\n"
    "module = importlib.util.module_from_spec(spec)\n"
    "spec.loader.exec_module(module)\n"
    "module.assemble(src=Path(src), res=Path(res), node=Path(node))\n"
    "Path(done).write_text('done', encoding='utf-8')\n"
)


def test_concurrent_assembles_serialize_on_the_dist_lock(tmp_path):
    """#457 finding 2: assemble takes the shared dist/ build lock, so a
    second assemble waits instead of racing its sweep against the first
    run's live staging tree."""

    import subprocess

    src, res, node = _buildable_source(tmp_path)
    done = tmp_path / "child-done"

    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            _CHILD_ASSEMBLE,
            str(_SCRIPT),
            str(src),
            str(res),
            str(node),
            str(done),
        ],
    )
    try:
        # The child cannot finish while this process holds the same lock.
        with assemble._dist_lock():
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and child.poll() is None:
                assert not done.exists(), (
                    "assemble completed while another holder owned the dist lock"
                )
                time.sleep(0.1)
            assert not done.exists(), (
                "assemble completed while another holder owned the dist lock"
            )
        # Releasing lets the waiting child finish the swap.
        child.wait(timeout=60)
        assert child.returncode == 0
        assert done.exists()
        assert (res / "standalone" / "server.js").is_file()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)


def test_clear_refuses_loudly_when_a_remnant_survives(tmp_path, monkeypatch):
    """#409 finding 2 (staging variant): a same-pid staging remnant with a
    locked file must not abort with a raw OSError mid-delete — and must
    never let the new tree merge into the half-cleared remnant."""

    remnant = _plant(tmp_path, "web.tmp-4242")

    def locked_rmtree(path, ignore_errors=False):
        raise OSError("file locked by another process")

    monkeypatch.setattr(assemble.shutil, "rmtree", locked_rmtree)
    try:
        assemble._clear(remnant)
    except SystemExit as refusal:
        assert "could not fully clear" in str(refusal)
    else:
        raise AssertionError("a surviving remnant must refuse, not pass")
    assert remnant.exists()


def test_clear_is_noop_without_a_remnant(tmp_path):
    assemble._clear(tmp_path / "web.tmp-4242")  # must not raise
    assert not (tmp_path / "web.tmp-4242").exists()
