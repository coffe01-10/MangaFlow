"""Regression tests for the atomic web-resource swap (Issues #347 and #382).

``apps/desktop/scripts/assemble-web-resources.py`` used to rmtree the
shipped tree (``src-tauri/web/``) and rebuild it in place: a mid-copy
failure (disk full, locked file, Ctrl-C) left a truncated bundle that the
next ``tauri build`` bundled silently. These tests pin the replacement
contract: assemble into a sibling staging directory, swap with same-volume
renames, and keep the previous tree byte-identical on failure. The #382
cases pin the two windows that contract left open: an async exception
landing between the two renames, and a rollback that fails while the old
tree is still parked in the retired directory. The #393 case pins the
operator re-run in that end state: with the resource root missing and a
same-pid retired tree parked as the only copy, assemble must refuse
instead of deleting it during startup cleanup.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "apps"
    / "desktop"
    / "scripts"
    / "assemble-web-resources.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("assemble_web_resources", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _tree(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _make_source(tmp_path: Path) -> Path:
    src = tmp_path / "web-standalone"
    (src / "nested").mkdir(parents=True)
    (src / "server.js").write_bytes(b"server-entry")
    (src / "nested" / "asset.txt").write_bytes(b"asset-bytes")
    return src


def _make_previous(res: Path) -> None:
    (res / "node").mkdir(parents=True)
    (res / "node" / "node.exe").write_bytes(b"old-node")
    (res / "standalone").mkdir()
    (res / "standalone" / "server.js").write_bytes(b"old-standalone")


def test_failed_copy_leaves_previous_tree_byte_identical(tmp_path, monkeypatch):
    module = _load_module()
    src = _make_source(tmp_path)
    res = tmp_path / "src-tauri" / "web"
    _make_previous(res)
    previous = _tree(res)
    node = tmp_path / "node.exe"
    node.write_bytes(b"node-runtime")

    real_copytree = shutil.copytree
    copied = []

    def flaky_copy_file(src_file, dst_file):
        copied.append(Path(src_file).name)
        if len(copied) == 2:
            # Second file: a disk-full-like failure in the middle of the
            # ~85 MB copytree — the exact window the old in-place rebuild
            # destroyed the shipped tree in.
            raise OSError("simulated disk full mid-copy")
        return shutil.copy2(src_file, dst_file)

    def flaky_copytree(src, dst, symlinks=False, ignore=None, copy_function=None,
                       ignore_dangling_symlinks=False, dirs_exist_ok=False):
        # Full signature: shutil's internal directory recursion calls
        # copytree with positional arguments; whichever copy_function the
        # caller picked is replaced by the flaky per-file copier.
        return real_copytree(
            src, dst, symlinks=symlinks, ignore=ignore,
            copy_function=flaky_copy_file,
            ignore_dangling_symlinks=ignore_dangling_symlinks,
            dirs_exist_ok=dirs_exist_ok,
        )

    monkeypatch.setattr(shutil, "copytree", flaky_copytree)

    with pytest.raises(OSError, match="simulated disk full mid-copy"):
        module.assemble(src=src, res=res, node=node)

    # The old tree is byte-identical (content, names, and nothing extra).
    assert _tree(res) == previous
    # The half-built staging tree and any retired remnant are cleaned up;
    # only the untouched resource root remains beside its parent.
    assert [p.name for p in res.parent.iterdir()] == [res.name]
    # The failure really happened mid-copy, not before any file landed.
    assert len(copied) == 2


def test_successful_assemble_swaps_in_the_new_tree(tmp_path, capsys):
    module = _load_module()
    src = _make_source(tmp_path)
    res = tmp_path / "src-tauri" / "web"
    _make_previous(res)
    node = tmp_path / "node.exe"
    node.write_bytes(b"node-runtime")

    module.assemble(src=src, res=res, node=node)

    assert _tree(res) == {
        "node/node.exe": b"node-runtime",
        "standalone/server.js": b"server-entry",
        "standalone/nested/asset.txt": b"asset-bytes",
    }
    # No staging/retired leftovers: the swap completed and the old tree was
    # deleted only after the new one was in place.
    assert [p.name for p in res.parent.iterdir()] == [res.name]
    out = capsys.readouterr().out
    assert f"WEB_RESOURCES_READY {res}" in out


def test_keyboardinterrupt_between_renames_rolls_back_old_tree(tmp_path, monkeypatch):
    module = _load_module()
    src = _make_source(tmp_path)
    res = tmp_path / "src-tauri" / "web"
    _make_previous(res)
    previous = _tree(res)
    node = tmp_path / "node.exe"
    node.write_bytes(b"node-runtime")

    real_rename = os.rename
    calls = []

    def windowed_rename(src_path, dst_path):
        calls.append((Path(src_path), Path(dst_path)))
        if len(calls) == 1:
            # Call #1 moves the old tree aside. It fully completes, and
            # then an async Ctrl-C lands after rename #1 but before
            # rename #2 — the exact #382 window whose exception used to
            # skip any guard wrapped around the second rename alone.
            real_rename(src_path, dst_path)
            raise KeyboardInterrupt("simulated Ctrl-C between the two renames")
        return real_rename(src_path, dst_path)

    monkeypatch.setattr(os, "rename", windowed_rename)

    with pytest.raises(KeyboardInterrupt, match="simulated Ctrl-C"):
        module.assemble(src=src, res=res, node=node)

    # The swap really reached "rename #1 done", and the rollback rename
    # (#2) restored the old tree before the exception propagated.
    assert len(calls) == 2
    assert calls[0][1].name == f"{res.name}.old-{os.getpid()}"
    assert calls[1][0].name == f"{res.name}.old-{os.getpid()}"
    assert calls[1][1] == res
    # The old tree is back, byte-identical, as the module docstring
    # promises for mid-swap failures.
    assert _tree(res) == previous
    # No staging or retired remnants beside the resource root.
    assert [p.name for p in res.parent.iterdir()] == [res.name]


def test_failed_rollback_keeps_retired_tree_with_recovery_guide(tmp_path, monkeypatch):
    module = _load_module()
    src = _make_source(tmp_path)
    res = tmp_path / "src-tauri" / "web"
    _make_previous(res)
    previous = _tree(res)
    node = tmp_path / "node.exe"
    node.write_bytes(b"node-runtime")
    retired = res.parent / f"{res.name}.old-{os.getpid()}"

    real_rename = os.rename
    calls = []

    def swing_and_rollback_failure(src_path, dst_path):
        calls.append((Path(src_path), Path(dst_path)))
        if len(calls) == 1:
            return real_rename(src_path, dst_path)
        if len(calls) == 2:
            raise OSError("simulated swap rename failure")
        raise OSError("simulated rollback rename failure")

    monkeypatch.setattr(os, "rename", swing_and_rollback_failure)

    with pytest.raises(RuntimeError) as excinfo:
        module.assemble(src=src, res=res, node=node)

    # rename #1 (old tree aside), rename #2 (swap, failed), rollback
    # (failed too).
    assert len(calls) == 3
    # The resource root is gone and the retired tree is the only copy of
    # the old tree: the cleanup must NOT have deleted it (#382).
    assert not res.exists()
    assert retired.exists()
    assert _tree(retired) == previous
    # The staged new tree is still cleaned up; only the retired tree is
    # deliberately preserved.
    assert sorted(p.name for p in res.parent.iterdir()) == [retired.name]
    # The error tells the operator where the old tree is parked and how to
    # restore it manually.
    msg = str(excinfo.value)
    assert str(retired) in msg
    assert str(res) in msg
    assert "Do not delete" in msg
    assert "Move-Item" in msg


def test_rerun_with_stale_retired_tree_refuses_and_keeps_copy(tmp_path, monkeypatch):
    module = _load_module()
    src = _make_source(tmp_path)
    res = tmp_path / "src-tauri" / "web"
    node = tmp_path / "node.exe"
    node.write_bytes(b"node-runtime")
    # The #382 rollback-failure end state: res is gone and the retired
    # tree sits under a pid this new run reuses, so its startup cleanup
    # would rmtree the only copy of the old tree before staging anything.
    monkeypatch.setattr(os, "getpid", lambda: 424242)
    retired = res.parent / f"{res.name}.old-{os.getpid()}"
    _make_previous(retired)
    parked = _tree(retired)

    with pytest.raises(RuntimeError) as excinfo:
        module.assemble(src=src, res=res, node=node)

    # Refused before anything was staged or deleted: the parked tree is
    # byte-identical and still the only entry beside the resource root.
    assert not res.exists()
    assert retired.exists()
    assert _tree(retired) == parked
    assert sorted(p.name for p in res.parent.iterdir()) == [retired.name]
    # The error repeats the manual-recovery guidance instead of failing
    # silently or suggesting a plain re-run would be safe.
    msg = str(excinfo.value)
    assert str(retired) in msg
    assert str(res) in msg
    assert "Do not delete" in msg
    assert "Move-Item" in msg


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
