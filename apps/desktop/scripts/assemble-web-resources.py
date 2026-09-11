"""Assemble the desktop web resources for the install bundle (W-15 slice 3).

Lays out, under `apps/desktop/src-tauri/web/` (tauri.conf resources, shipped
next to the shell executable by the NSIS/MSI installers):
  web/node/node.exe        (node runtime: NODE_EXE env, PATH, or the known
                            C:\\node\\node.exe location)
  web/standalone/...       (the verified relocated standalone tree produced
                            by build-web-standalone.py)

Run build-web-standalone.py first. The assembled tree is gitignored
(85MB of build artifacts); rebuild it before every `tauri build`.

The tree is never rebuilt in place. Everything is assembled into a sibling
staging directory (`web.tmp-<pid>`, same volume) and swapped in with
renames, so a mid-copy failure (disk full, file locked by a running app,
Ctrl-C) leaves the previous tree byte-identical instead of a truncated
bundle that the next `tauri build` would ship silently. The old tree is
only deleted after the new one is complete and in place (#347).
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SRC = REPO / "apps/desktop/dist/web-standalone"
RES = REPO / "apps/desktop/src-tauri/web"


def find_node() -> Path:
    override = os.environ.get("NODE_EXE")
    if override and Path(override).is_file():
        return Path(override)
    which = shutil.which("node")
    if which:
        return Path(which)
    fallback = Path(r"C:\node\node.exe")
    if fallback.is_file():
        return fallback
    raise SystemExit("no node runtime found (set NODE_EXE or put node on PATH)")


def _clear(path: Path) -> None:
    # Crash remnant of an earlier run that happened to reuse this pid.
    if path.exists():
        shutil.rmtree(path)


def assemble(src: Path = SRC, res: Path = RES, node: Path | None = None) -> Path:
    """Assemble the resource tree beside ``res`` and swap it in atomically.

    Build order: stage the complete new tree under ``<res>.tmp-<pid>``,
    move the old tree aside to ``<res>.old-<pid>``, rename the staged tree
    into place, and only then delete the retired tree. Any failure before
    the final rename leaves ``res`` untouched (and the staged half-tree is
    cleaned up); if the final rename itself fails, the old tree is rolled
    back into place before the error propagates.
    """
    if not (src / "server.js").is_file():
        raise SystemExit("run build-web-standalone.py first")
    if node is None:
        node = find_node()
    staging = res.parent / f"{res.name}.tmp-{os.getpid()}"
    retired = res.parent / f"{res.name}.old-{os.getpid()}"
    _clear(staging)
    _clear(retired)
    try:
        (staging / "node").mkdir(parents=True)
        shutil.copy2(node, staging / "node" / "node.exe")
        shutil.copytree(src, staging / "standalone")
        if res.exists():
            os.rename(res, retired)
        try:
            os.rename(staging, res)
        except BaseException:
            # Same-volume renames practically do not fail — but if this one
            # does, put the old tree back instead of leaving the resource
            # root missing. Re-raise either way (KeyboardInterrupt included).
            if retired.exists() and not res.exists():
                os.rename(retired, res)
            raise
    finally:
        # After a successful swap `retired` holds the old tree (delete it
        # now); after any failure both are half-states to be removed.
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(retired, ignore_errors=True)
    mb = sum(f.stat().st_size for f in res.rglob("*") if f.is_file()) / 1048576
    print(f"WEB_RESOURCES_READY {res} ({mb:.0f} MB)")
    return res


if __name__ == "__main__":
    assemble()
