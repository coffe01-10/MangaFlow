"""Regression tests for the atomic web-resource swap (Issue #347).

``apps/desktop/scripts/assemble-web-resources.py`` used to rmtree the
shipped tree (``src-tauri/web/``) and rebuild it in place: a mid-copy
failure (disk full, locked file, Ctrl-C) left a truncated bundle that the
next ``tauri build`` bundled silently. These tests pin the replacement
contract: assemble into a sibling staging directory, swap with same-volume
renames, and keep the previous tree byte-identical on failure.
"""

from __future__ import annotations

import importlib.util
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


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
