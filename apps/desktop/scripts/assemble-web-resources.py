"""Assemble the desktop web resources for the install bundle (W-15 slice 3).

Lays out, under `apps/desktop/src-tauri/web/` (tauri.conf resources, shipped
next to the shell executable by the NSIS/MSI installers):
  web/node/node.exe        (node runtime: NODE_EXE env, PATH, or the known
                            C:\\node\\node.exe location)
  web/standalone/...       (the verified relocated standalone tree produced
                            by build-web-standalone.py)

Run build-web-standalone.py first. The assembled tree is gitignored
(85MB of build artifacts); rebuild it before every `tauri build`.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SRC = REPO / "apps/desktop/dist/web-standalone"
RES = REPO / "apps/desktop/src-tauri/web"


def find_node() -> Path:
    override = os.environ.get("NODE_EXE")
    if override and Path(override).is_file():
        return Path(override)
    import shutil

    which = shutil.which("node")
    if which:
        return Path(which)
    fallback = Path(r"C:\node\node.exe")
    if fallback.is_file():
        return fallback
    raise SystemExit("no node runtime found (set NODE_EXE or put node on PATH)")


assert (SRC / "server.js").is_file(), "run build-web-standalone.py first"
node = find_node()

if RES.exists():
    shutil.rmtree(RES)
(RES / "node").mkdir(parents=True)
shutil.copy2(node, RES / "node" / "node.exe")
shutil.copytree(SRC, RES / "standalone")
mb = sum(f.stat().st_size for f in RES.rglob("*") if f.is_file()) / 1048576
print(f"WEB_RESOURCES_READY {RES} ({mb:.0f} MB)")
