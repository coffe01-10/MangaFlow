"""Build the Next standalone web bundle for the desktop shell (W-15 plan B).

Runs the standard production build (next.config.ts opts into
output:"standalone") and then performs the documented post-build step:
copy `.next/static` into the standalone tree — the standalone server does
not serve the compile tree's static assets by itself. Also seeds the
rewrites destination check: the bundle must point /api/v1/* at the helper's
fixed relay port (127.0.0.1:39443) — a build made with a different
MANGAFLOW_API_ORIGIN would silently proxy to the wrong target, so the
manifest is verified here and the build fails loudly instead.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
WEB = REPO / "apps" / "web"
RELAY_ORIGIN = "http://127.0.0.1:39443"

subprocess.run(
    ["npm.cmd", "run", "build", "--workspace", "@mangaflow/web"],
    cwd=REPO,
    check=True,
)

standalone = WEB / ".next" / "standalone" / "apps" / "web"
server_js = standalone / "server.js"
if not server_js.is_file():
    raise SystemExit(f"standalone build did not produce {server_js}")

manifest = standalone / ".next" / "routes-manifest.json"
destinations = json.loads(manifest.read_text(encoding="utf-8"))
rewrites = destinations.get("rewrites", {})
flat = json.dumps(rewrites)
if RELAY_ORIGIN not in flat:
    raise SystemExit(
        "standalone rewrites do not target the helper relay "
        f"{RELAY_ORIGIN}; rebuild without MANGAFLOW_API_ORIGIN set "
        "(the relay origin is the documented build-time constant)"
    )

static_dst = standalone / ".next" / "static"
if static_dst.exists():
    shutil.rmtree(static_dst)
shutil.copytree(WEB / ".next" / "static", static_dst)

print("WEB_STANDALONE_READY", standalone)
