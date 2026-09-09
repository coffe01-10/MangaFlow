"""Build the Next standalone web bundle for the desktop shell (W-15 plan B).

Runs the standard production build (next.config.ts opts into
output:"standalone") and then performs the documented post-build steps:
- copy `.next/static` into the standalone tree (the standalone server does
  not serve the compile tree's static assets by itself);
- verify the rewrites destination is the helper's fixed relay port
  (127.0.0.1:39443) — a build made with a different MANGAFLOW_API_ORIGIN
  would silently proxy to the wrong target, so the manifest is checked here
  and the build fails loudly instead;
- MOVE the verified bundle out of `.next` into
  `apps/desktop/dist/web-standalone/` — every plain `next build`
  regenerates `.next` from scratch (re-baking :8000 and dropping the static
  copy), so the desktop bundle must live outside its reach.

Run this script again after any `npm run build`; the sidecar e2e asserts
the relocated bundle's manifest.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
WEB = REPO / "apps" / "web"
DESKTOP_DIST = REPO / "apps" / "desktop" / "dist" / "web-standalone"
RELAY_ORIGIN = "http://127.0.0.1:39443"

# npm's CLI shim is npm.cmd on Windows and npm everywhere else; this script
# runs from both the Windows packaging flow and run-sidecar-e2e.sh (Linux).
NPM = "npm.cmd" if os.name == "nt" else "npm"

subprocess.run(
    [NPM, "run", "build", "--workspace", "@mangaflow/web"],
    cwd=REPO,
    check=True,
    # The desktop bundle bakes the helper's fixed relay port into its
    # rewrites at build time (see the comment in next.config.ts); the plain
    # default (8000) stays for every non-desktop form.
    env=dict(os.environ, MANGAFLOW_API_ORIGIN="http://127.0.0.1:39443"),
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

if DESKTOP_DIST.exists():
    shutil.rmtree(DESKTOP_DIST)
DESKTOP_DIST.parent.mkdir(parents=True, exist_ok=True)
shutil.move(str(standalone), str(DESKTOP_DIST))

# Build provenance: the e2e asserts the bundle was built from THIS source
# tree, so a stale relocated bundle (dist/ is gitignored and survives for
# days) can never silently test outdated UI code.
def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout.strip()


build_info = {
    "source_commit": _git("rev-parse", "HEAD"),
    "apps_web_tree": _git("rev-parse", "HEAD:apps/web"),
    "relay_origin": RELAY_ORIGIN,
}
(DESKTOP_DIST / "build-info.json").write_text(
    json.dumps(build_info, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)

print("WEB_STANDALONE_READY", DESKTOP_DIST)
