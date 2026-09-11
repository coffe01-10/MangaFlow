"""Regression tests for recreate_junction in build-frontend-static.sh (#392/#396).

``recreate_junction`` rebuilds the link entries that ``clone_hardlink_tree``
skipped, re-anchoring every in-repo target at the throwaway worktree. The #392
fix made it resolve the raw ``readlink`` text to an absolute path first, so
in-tree relative targets (npm/pnpm ``.bin`` style) stop being misdiagnosed as
"points outside the repo"; the #396 round pinned two follow-ups: the fix had
no committed test, and a dangling ABSOLUTE target escaped with an empty
``target_kind`` into the mklink branch (raw cmd failure instead of this
script's diagnosis).

These tests drive the real function — extracted verbatim from the script —
against a scratch REPO_ROOT/WORKTREE pair. Creating native relative symlinks
needs a privilege this host may lack, so ``readlink`` is a PATH shim that
returns the canned target text (the only privilege-dependent step); everything
after that — resolution, the repo-prefix decision, ``cmd //c mklink //J`` and
``cp -l`` side effects — runs against the real filesystem.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "apps"
    / "desktop"
    / "scripts"
    / "build-frontend-static.sh"
)

DRIVER = """#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(cygpath -u "$REPO_ROOT_WIN")"
WORKTREE="$(cygpath -u "$WORKTREE_WIN")"
export REPO_ROOT WORKTREE
# bash.exe starts with /usr/bin ahead of the converted Windows PATH, so the
# shim directory is prepended HERE (msys form) for the function's lookups.
export PATH="$(cygpath -u "$SHIM_DIR_WIN"):$PATH"
source "$(cygpath -u "$FN_FILE_WIN")"
if recreate_junction "$TREE_REL" "$LINK_REL"; then
  printf 'RC=0\\n'
else
  printf 'RC=%s\\n' "$?"
fi
"""

# The shim answers by path suffix: the fixture only ever queries links under
# the one REPO_ROOT, so the tree-relative tail is unique per link. Targets
# that must be absolute are built from the exported REPO_ROOT at call time.
READLINK_SHIM = """#!/usr/bin/env bash
case "$1" in
  */node_modules/@scope/pkg) printf '%s\\n' "../../apps/web"; exit 0 ;;
  */node_modules/.bin/next) printf '%s\\n' "../../pkg/bin/next-file"; exit 0 ;;
  */node_modules/sub/deep/outside-link) printf '%s\\n' "../../../../outside"; exit 0 ;;
  */node_modules/dangling-rel) printf '%s\\n' "apps/web/missing"; exit 0 ;;
  */node_modules/dangling-abs) printf '%s\\n' "$REPO_ROOT/apps/web/gone"; exit 0 ;;
  */node_modules/abs-junction) printf '%s\\n' "$REPO_ROOT/apps/web"; exit 0 ;;
esac
exit 1
"""


def _bash() -> str:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash (Git for Windows) not available; cannot drive recreate_junction")
    return bash


def _extract_function() -> str:
    lines = SCRIPT.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("recreate_junction() {"))
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == "}")
    return "\n".join(lines[start : end + 1]) + "\n"


def _make_fixture(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    worktree = tmp_path / "wt"
    (repo / "apps" / "web").mkdir(parents=True)
    (repo / "apps" / "web" / "page.txt").write_bytes(b"web-src")
    (repo / "pkg" / "bin").mkdir(parents=True)
    (repo / "pkg" / "bin" / "next-file").write_bytes(b"next-bin-bytes")
    (repo / "node_modules" / ".bin").mkdir(parents=True)
    (repo / "node_modules" / "@scope").mkdir(parents=True)
    (repo / "node_modules" / "sub" / "deep").mkdir(parents=True)
    # The throwaway worktree is a checkout of the same tree: every target
    # path exists there too, so re-anchoring stays inside the worktree.
    (worktree / "apps" / "web").mkdir(parents=True)
    (worktree / "apps" / "web" / "page.txt").write_bytes(b"web-src")
    (worktree / "pkg" / "bin").mkdir(parents=True)
    (worktree / "pkg" / "bin" / "next-file").write_bytes(b"next-bin-bytes")
    (worktree / "node_modules").mkdir(parents=True)
    # Present only in the worktree: pins that a dangling absolute target is
    # refused at resolution instead of falling through to mklink (#396).
    (worktree / "apps" / "web" / "gone").write_bytes(b"checked-out-file")
    (tmp_path / "outside").mkdir()
    return repo, worktree


def _run(tmp_path: Path, link_rel: str, tree_rel: str = "node_modules") -> tuple[int, str, str]:
    bash = _bash()
    repo, worktree = _make_fixture(tmp_path)
    scratch = tmp_path / "harness"
    scratch.mkdir()
    fn_file = scratch / "function.sh"
    fn_file.write_text(_extract_function(), encoding="utf-8", newline="\n")
    shim_dir = scratch / "shim"
    shim_dir.mkdir()
    (shim_dir / "readlink").write_text(READLINK_SHIM, encoding="utf-8", newline="\n")
    driver = scratch / "drive.sh"
    driver.write_text(DRIVER, encoding="utf-8", newline="\n")
    env = os.environ.copy()
    proc = subprocess.run(
        [bash, driver.as_posix()],
        capture_output=True,
        text=True,
        env={
            **env,
            "REPO_ROOT_WIN": str(repo),
            "WORKTREE_WIN": str(worktree),
            "FN_FILE_WIN": str(fn_file),
            "SHIM_DIR_WIN": str(shim_dir),
            "TREE_REL": tree_rel,
            "LINK_REL": link_rel,
        },
        timeout=60,
    )
    rc_line = next((line for line in proc.stdout.splitlines() if line.startswith("RC=")), None)
    if rc_line is None:
        pytest.fail(f"driver did not report RC (stdout={proc.stdout!r} stderr={proc.stderr!r})")
    rc = int(rc_line[len("RC=") :])
    return rc, proc.stdout, proc.stderr


def _junction_target(path: Path) -> str:
    # os.readlink on a junction may return the target with the \\?\ prefix.
    target = os.readlink(path)
    return os.path.normcase(target[4:] if target.startswith("\\\\?\\") else target)


def test_relative_dir_link_rebuilds_as_worktree_junction(tmp_path):
    rc, out, err = _run(tmp_path, "@scope/pkg")
    assert rc == 0, err
    assert "outside" not in err
    link = tmp_path / "wt" / "node_modules" / "@scope" / "pkg"
    assert os.path.isjunction(link)
    assert _junction_target(link) == os.path.normcase(str(tmp_path / "wt" / "apps" / "web"))
    assert (link / "page.txt").read_bytes() == b"web-src"
    assert "recreated junction" in out


def test_relative_file_link_rebuilds_as_hardlink(tmp_path):
    rc, out, err = _run(tmp_path, ".bin/next")
    assert rc == 0, err
    link = tmp_path / "wt" / "node_modules" / ".bin" / "next"
    assert link.is_file()
    assert link.read_bytes() == b"next-bin-bytes"
    # cp -l, not a copy: source and rebuilt entry share an inode.
    assert os.stat(link).st_nlink == 2
    assert os.stat(tmp_path / "wt" / "pkg" / "bin" / "next-file").st_nlink == 2
    assert "recreated file link" in out


def test_outside_repo_link_is_refused(tmp_path):
    rc, out, err = _run(tmp_path, "sub/deep/outside-link")
    assert rc != 0
    assert "outside the repo" in err
    assert "cannot be resolved" not in err
    assert not (tmp_path / "wt" / "node_modules" / "sub" / "deep" / "outside-link").exists()


def test_dangling_relative_target_is_refused_with_resolution_error(tmp_path):
    rc, out, err = _run(tmp_path, "dangling-rel")
    assert rc != 0
    assert "does not exist or cannot be resolved" in err
    assert "outside the repo" not in err


def test_dangling_absolute_target_is_refused_before_mklink(tmp_path):
    # #396: a dangling absolute target used to keep target_abs set with an
    # empty target_kind and fall into the mklink branch (raw cmd failure).
    rc, out, err = _run(tmp_path, "dangling-abs")
    assert rc != 0
    assert "does not exist or cannot be resolved" in err
    assert "outside the repo" not in err
    assert "recreated" not in out
    assert not (tmp_path / "wt" / "node_modules" / "dangling-abs").exists()


def test_absolute_junction_rebuilds_against_worktree(tmp_path):
    # #385 legacy behavior: junction readlink returns an absolute msys path
    # that must keep resolving to the worktree's own apps/web.
    rc, out, err = _run(tmp_path, "abs-junction")
    assert rc == 0, err
    link = tmp_path / "wt" / "node_modules" / "abs-junction"
    assert os.path.isjunction(link)
    assert _junction_target(link) == os.path.normcase(str(tmp_path / "wt" / "apps" / "web"))
    assert "recreated junction" in out


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
