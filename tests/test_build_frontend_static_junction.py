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

The #878/#879 round extends the same extract-verbatim discipline to the
dependency clone as a whole: the clone is two-phase (every dependency tree
cloned before ANY link is re-anchored, so links between the two trees see
complete targets in both directions), and the POSIX ``ln -sfn`` re-anchor
arm (#717) is driven for real on Linux/Darwin hosts (real unprivileged
symlinks; ``os.path.islink`` + worktree-anchored ``readlink``) with an
MSYS-runnable dispatch pin so the arm's invocation contract also has
executable coverage on Windows hosts.
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
  */node_modules/abs-file-link) printf '%s\\n' "$REPO_ROOT/pkg/bin/next-file"; exit 0 ;;
esac
exit 1
"""

# POSIX-arm driver (#879): no cygpath, no shims — on a POSIX host `readlink`,
# `uname` and `ln` are real, so the verbatim two-phase entry runs exactly as
# the Linux build host runs it. The sentinel prints only when the whole
# clone survived `set -euo pipefail` (a failing recreate_junction aborts the
# driver process itself; the per-link assertions below carry the detail).
POSIX_DRIVER = """#!/usr/bin/env bash
set -euo pipefail
source "$FN_FILE"
clone_all_deps
printf 'RC=0\\n'
"""

# Dispatch-pin shims (the Windows suite's PATH-shim discipline, applied to
# the POSIX arm): uname answers Linux so the verbatim function's own
# dispatch takes the ln -sfn branch, and ln records its invocation with the
# worktree prefix normalized to @WT@ so assertions are free of this host's
# path spelling. A cp -l regression never reaches the ln shim — that is the
# red the pin exists to catch.
UNAME_LINUX_SHIM = """#!/usr/bin/env bash
printf 'Linux\\n'
"""

LN_SHIM = """#!/usr/bin/env bash
out=()
for arg in "$@"; do
  case "$arg" in
    "$WORKTREE"/*) arg="@WT@${arg#"$WORKTREE"}" ;;
  esac
  out+=("$arg")
done
printf '%s\\n' "${out[*]}" >> "$(cygpath -u "$LN_LOG_WIN")"
exit 0
"""


def _bash() -> str:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash (Git for Windows) not available; cannot drive recreate_junction")
    return bash


def _require_windows_driver() -> None:
    """The driver needs cygpath (Git for Windows) and cmd //c mklink //J.

    On non-MSYS hosts (Linux CI, WSL) cygpath is absent and every test in
    this module would hard-fail with a raw driver error instead of a clean
    skip - the same graceful-skip discipline the other host-shaped suites
    in this repo use.
    """
    if shutil.which("cygpath") is None:
        pytest.skip("cygpath not available (non-MSYS host); junction contract needs Git for Windows")
    if os.name != "nt" and shutil.which("cmd") is None:
        pytest.skip("cmd not available on this non-Windows host; mklink unavailable")


def _require_posix_driver() -> None:
    """The POSIX-arm tests need the script's ln -sfn branch to run for real.

    Windows/MSYS hosts cannot host this class: `uname -s` answers
    MINGW*/CYGWIN* there (the verbatim function's own dispatch takes the
    mklink arm, not ln -sfn), and creating the real unprivileged symlinks
    the assertions pin needs a privilege those hosts may lack. That side is
    covered by the junction suite above plus the dispatch pin below, so a
    clean skip here — not a raw driver error — is the contract.
    """
    if os.name == "nt" or "MSYSTEM" in os.environ:
        pytest.skip("Windows/MSYS host: the real ln -sfn arm needs Linux/Darwin")
    if shutil.which("bash") is None:
        pytest.skip("bash not available; cannot drive the POSIX re-anchor")


def _extract_bash_function(name: str) -> str:
    lines = SCRIPT.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(f"{name}() {{"))
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == "}")
    return "\n".join(lines[start : end + 1]) + "\n"


def _extract_function() -> str:
    return _extract_bash_function("recreate_junction")


def _extract_two_phase_block() -> str:
    # The #878 two-phase entry: phase 1 (clone every tree, accumulate
    # "<tree_rel>\t<link_rel>" pairs) through phase 2 (the re-anchor loop).
    lines = SCRIPT.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith('skipped_link_pairs=""'))
    end = next(
        i
        for i in range(start + 1, len(lines))
        if lines[i].startswith('done <<< "$skipped_link_pairs"')
    )
    return "\n".join(lines[start : end + 1]) + "\n"


def _posix_units() -> str:
    """Every unit the POSIX driver sources, verbatim from the script.

    The two-phase entry is top-level script text (it must run under the
    script's own `set -euo pipefail` semantics), so it is wrapped in a
    function — same discipline as the gate extraction: the block itself
    stays byte-for-byte the script's.
    """
    return (
        _extract_bash_function("clone_hardlink_tree")
        + _extract_bash_function("recreate_junction")
        + _extract_bash_function("clone_deps_tree")
        + "clone_all_deps() {\n"
        + _extract_two_phase_block()
        + "}\n"
    )


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


def _run(
    tmp_path: Path,
    link_rel: str,
    tree_rel: str = "node_modules",
    *,
    extra_shims: dict[str, str] | None = None,
    extra_env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    bash = _bash()
    repo, worktree = _make_fixture(tmp_path)
    scratch = tmp_path / "harness"
    scratch.mkdir()
    fn_file = scratch / "function.sh"
    fn_file.write_text(_extract_function(), encoding="utf-8", newline="\n")
    shim_dir = scratch / "shim"
    shim_dir.mkdir()
    (shim_dir / "readlink").write_text(READLINK_SHIM, encoding="utf-8", newline="\n")
    for name, body in (extra_shims or {}).items():
        (shim_dir / name).write_text(body, encoding="utf-8", newline="\n")
    driver = scratch / "drive.sh"
    driver.write_text(DRIVER, encoding="utf-8", newline="\n")
    env = os.environ.copy()
    proc = subprocess.run(
        [bash, driver.as_posix()],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={
            **env,
            "REPO_ROOT_WIN": str(repo),
            "WORKTREE_WIN": str(worktree),
            "FN_FILE_WIN": str(fn_file),
            "SHIM_DIR_WIN": str(shim_dir),
            "TREE_REL": tree_rel,
            "LINK_REL": link_rel,
            **(extra_env or {}),
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
    _require_windows_driver()
    rc, out, err = _run(tmp_path, "@scope/pkg")
    assert rc == 0, err
    assert "outside" not in err
    link = tmp_path / "wt" / "node_modules" / "@scope" / "pkg"
    assert os.path.isjunction(link)
    assert _junction_target(link) == os.path.normcase(str(tmp_path / "wt" / "apps" / "web"))
    assert (link / "page.txt").read_bytes() == b"web-src"
    assert "recreated junction" in out


def test_relative_file_link_rebuilds_as_hardlink(tmp_path):
    _require_windows_driver()
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
    _require_windows_driver()
    rc, out, err = _run(tmp_path, "sub/deep/outside-link")
    assert rc != 0
    assert "outside the repo" in err
    assert "cannot be resolved" not in err
    assert not (tmp_path / "wt" / "node_modules" / "sub" / "deep" / "outside-link").exists()


def test_dangling_relative_target_is_refused_with_resolution_error(tmp_path):
    _require_windows_driver()
    rc, out, err = _run(tmp_path, "dangling-rel")
    assert rc != 0
    assert "does not exist or cannot be resolved" in err
    assert "outside the repo" not in err


def test_dangling_absolute_target_is_refused_before_mklink(tmp_path):
    _require_windows_driver()
    # #396: a dangling absolute target used to keep target_abs set with an
    # empty target_kind and fall into the mklink branch (raw cmd failure).
    rc, out, err = _run(tmp_path, "dangling-abs")
    assert rc != 0
    assert "does not exist or cannot be resolved" in err
    assert "outside the repo" not in err
    assert "recreated" not in out
    assert not (tmp_path / "wt" / "node_modules" / "dangling-abs").exists()


def test_absolute_junction_rebuilds_against_worktree(tmp_path):
    _require_windows_driver()
    # #385 legacy behavior: junction readlink returns an absolute msys path
    # that must keep resolving to the worktree's own apps/web.
    rc, out, err = _run(tmp_path, "abs-junction")
    assert rc == 0, err
    link = tmp_path / "wt" / "node_modules" / "abs-junction"
    assert os.path.isjunction(link)
    assert _junction_target(link) == os.path.normcase(str(tmp_path / "wt" / "apps" / "web"))
    assert "recreated junction" in out


def test_absolute_file_target_rebuilds_as_hardlink(tmp_path):
    _require_windows_driver()
    # #398: the absolute -e branch (an existing absolute target that is a
    # file) is the one arm of the resolution table no earlier case pinned;
    # dropping its target_kind=file assignment must fail here, not pass.
    rc, out, err = _run(tmp_path, "abs-file-link")
    assert rc == 0, err
    link = tmp_path / "wt" / "node_modules" / "abs-file-link"
    assert link.is_file()
    assert link.read_bytes() == b"next-bin-bytes"
    assert os.stat(link).st_nlink == 2
    assert os.stat(tmp_path / "wt" / "pkg" / "bin" / "next-file").st_nlink == 2
    assert "recreated file link" in out


def _write_file(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _make_posix_fixture(tmp_path: Path) -> tuple[Path, Path]:
    """A non-hoisted (npm-link/pnpm shaped) dependency layout, real links.

    The repo carries REAL unprivileged symlinks — no readlink shim: on a
    POSIX host everything the verbatim two-phase entry touches is real.
    The worktree mirrors the script's throwaway git worktree: a checkout of
    the same tree, so everything OUTSIDE the cloned dependency trees
    (node_modules, apps/web/node_modules) is pre-seeded there; the clone
    itself populates the two trees and phase 2 re-anchors the links.
    """
    repo = tmp_path / "repo"
    worktree = tmp_path / "wt"
    # Real payloads phase 1 must hardlink-clone in both trees.
    _write_file(repo / "node_modules" / "realpkg" / "package.json", b"realpkg")
    _write_file(
        repo / "node_modules" / ".pnpm" / "next@15.0.0" / "node_modules" / "next" / "package.json",
        b"next-pkg",
    )
    _write_file(repo / "apps" / "web" / "node_modules" / "web-pkg" / "package.json", b"web-pkg")
    _write_file(repo / "pkg" / "bin" / "next-file", b"next-bin-bytes")
    _write_file(worktree / "pkg" / "bin" / "next-file", b"next-bin-bytes")
    # File-target link (npm .bin shape) and the #878 non-hoisted shapes: a
    # root-tree link into apps/web's tree, plus the mirror link back into
    # the root tree's .pnpm store (why a plain clone-order swap cannot fix
    # the layout — each direction needs the other tree complete first).
    os.symlink("../../pkg/bin/next-file", repo / "node_modules" / ".bin" / "next")
    os.symlink("../apps/web/node_modules/web-pkg", repo / "node_modules" / "web-scope")
    os.symlink(
        "../../../node_modules/.pnpm/next@15.0.0/node_modules/next",
        repo / "apps" / "web" / "node_modules" / "next",
    )
    return repo, worktree


def _run_posix_clone(tmp_path: Path) -> tuple[Path, Path, subprocess.CompletedProcess]:
    _require_posix_driver()
    bash = shutil.which("bash")
    assert bash is not None  # guaranteed by the driver gate
    repo, worktree = _make_posix_fixture(tmp_path)
    scratch = tmp_path / "posix-harness"
    scratch.mkdir()
    fn_file = scratch / "function.sh"
    fn_file.write_text(_posix_units(), encoding="utf-8", newline="\n")
    driver = scratch / "drive.sh"
    driver.write_text(POSIX_DRIVER, encoding="utf-8", newline="\n")
    env = os.environ.copy()
    proc = subprocess.run(
        [bash, driver.as_posix()],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={
            **env,
            "REPO_ROOT": repo.as_posix(),
            "WORKTREE": worktree.as_posix(),
            "FN_FILE": fn_file.as_posix(),
        },
        timeout=120,
    )
    return repo, worktree, proc


class TestPosixSymlinkReanchor:
    """Executable coverage for the POSIX ln -sfn re-anchor arm (#717/#879).

    The Windows suite above never reaches the POSIX branch — its hosts
    answer uname as MINGW*/CYGWIN*, so the verbatim dispatch takes mklink —
    which left the #717 regression class (ln -sfn replaced by cp -l, the
    pre-fix hardlink shape that breaks Linux node shims' relative requires)
    invisible to every gate on every host. These tests drive the real
    two-phase clone entry on hosts where unprivileged ln -s creates REAL
    symlinks, and pin the #878 cross-tree contract at the same time.
    """

    def test_dir_and_file_links_rebuild_as_real_worktree_symlinks(self, tmp_path):
        _, worktree, proc = _run_posix_clone(tmp_path)
        assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
        assert "RC=0" in proc.stdout
        # File-target link: a REAL symlink — the islink pin is exactly what
        # a cp -l regression (the #717 pre-fix shape) cannot satisfy.
        file_link = worktree / "node_modules" / ".bin" / "next"
        assert os.path.islink(file_link)
        assert (
            os.readlink(file_link) == (worktree / "pkg" / "bin" / "next-file").as_posix()
        )
        # Dir-target link: the same symlink contract for directories.
        dir_link = worktree / "node_modules" / "web-scope"
        assert os.path.islink(dir_link)
        assert (
            os.readlink(dir_link)
            == (worktree / "apps" / "web" / "node_modules" / "web-pkg").as_posix()
        )
        assert "recreated symlink" in proc.stdout
        assert "recreated junction" not in proc.stdout
        assert "recreated file link" not in proc.stdout

    def test_cross_tree_links_reanchor_only_after_every_tree_is_cloned(self, tmp_path):
        _, worktree, proc = _run_posix_clone(tmp_path)
        assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
        # #878 both directions in one pass: the root-tree link into
        # apps/web's tree AND the mirror link back into the root tree's
        # .pnpm store. The per-tree order (re-anchor node_modules before
        # apps/web's tree is cloned) fails the first link with "missing in
        # worktree"; a plain call swap fails the second for the mirrored
        # reason — only clone-everything-then-re-anchor satisfies both.
        mirror = worktree / "apps" / "web" / "node_modules" / "next"
        assert os.path.islink(mirror)
        assert (
            os.readlink(mirror)
            == (
                worktree / "node_modules" / ".pnpm" / "next@15.0.0" / "node_modules" / "next"
            ).as_posix()
        )
        # Phase 1 still hardlink-clones the real payloads: same inode on
        # both sides, links rebuilt ON TOP of the copy, not instead of it.
        cloned = worktree / "node_modules" / "realpkg" / "package.json"
        assert cloned.read_bytes() == b"realpkg"
        assert os.stat(cloned).st_nlink == 2
        assert (
            tmp_path / "repo" / "node_modules" / "realpkg" / "package.json"
        ).read_bytes() == b"realpkg"


def test_posix_arm_dispatch_runs_ln_sfn_with_worktree_target(tmp_path):
    """Dispatch pin for the #717 arm, runnable on the MSYS hosts where the
    real-arm class above must skip: with uname answering Linux (shim), the
    verbatim recreate_junction must rebuild BOTH link kinds through ln -sfn
    with the worktree-anchored absolute target. A cp -l regression never
    reaches the ln shim — the recorded invocation is the red."""
    _require_windows_driver()
    cases = [
        ("@scope/pkg", "-sfn @WT@/apps/web @WT@/node_modules/@scope/pkg"),
        (".bin/next", "-sfn @WT@/pkg/bin/next-file @WT@/node_modules/.bin/next"),
    ]
    for i, (link_rel, expected_invocation) in enumerate(cases):
        case_dir = tmp_path / f"dispatch-{i}"
        case_dir.mkdir()
        ln_log = case_dir / "ln.log"
        rc, out, err = _run(
            case_dir,
            link_rel,
            extra_shims={"uname": UNAME_LINUX_SHIM, "ln": LN_SHIM},
            extra_env={"LN_LOG_WIN": str(ln_log)},
        )
        assert rc == 0, err
        assert "recreated symlink" in out
        assert "recreated junction" not in out
        assert "recreated file link" not in out
        assert ln_log.read_text(encoding="utf-8").splitlines() == [expected_invocation]


def test_posix_driver_units_extract_and_syntax_check(tmp_path):
    """The extract-verbatim contract stays wired on every host: each unit
    marker must resolve against the CURRENT script text (a renamed function
    or moved two-phase block fails HERE — not as a Linux-only StopIteration)
    and the assembled driver file must parse under bash -n."""
    bash = _bash()
    units = _posix_units()
    driver_file = tmp_path / "posix-function.sh"
    driver_file.write_text(units, encoding="utf-8", newline="\n")
    proc = subprocess.run(
        [bash, "-n", driver_file.as_posix()],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    for unit in (
        "clone_hardlink_tree() {",
        "recreate_junction() {",
        "clone_deps_tree() {",
        "clone_all_deps() {",
        'done <<< "$skipped_link_pairs"',
    ):
        assert unit in units


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
