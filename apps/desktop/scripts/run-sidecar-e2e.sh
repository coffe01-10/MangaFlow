#!/usr/bin/env bash
# Run the desktop sidecar e2e (real API + fake model channel) in the
# sandbox venv. Creates .venv-desktop on first use.
set -euo pipefail
DESKTOP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "$DESKTOP_ROOT/../.." && pwd)"
VENV="$REPO_ROOT/.venv-desktop"

if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q -r "$REPO_ROOT/apps/api/requirements.txt" -r "$REPO_ROOT/apps/api/requirements-dev.txt"
fi

export MANGAFLOW_DESKTOP_PYTHON="$VENV/bin/python"
export MANGAFLOW_DESKTOP_HELPER="$DESKTOP_ROOT/sidecar/mangaflow_desktop_helper.py"
cd "$REPO_ROOT"
# The plan B test needs the relocated Next standalone bundle; build it when
# missing (the build script verifies the relay destination, copies static
# assets, and moves the tree out of `.next`'s reach).
if [ ! -f "$REPO_ROOT/apps/desktop/dist/web-standalone/server.js" ]; then
  "$VENV/bin/python" "$DESKTOP_ROOT/scripts/build-web-standalone.py"
fi
# The relay/bind regression suites (test_sidecar_relay*.py) are
# pure-loopback stdlib+pytest and run everywhere the e2e runs; the env/
# api-root suite pins the helper's startup ownership contract; the dist
# build lock suite keeps the shared dist/ writers mutually exclusive
# (#350); the assemble suite pins the staging sweep/swap contracts and
# their dist-lock serialization (#409/#457); the build-web-standalone
# suite pins the bundle swap's clear-refuse and atomic stamp (#461).
# Keep them all in this runner so the contracts stay exercised
# instead of depending on someone remembering a manual pytest command.
# (pytest.ini's testpaths/norecursedirs exclude apps/desktop from a bare
# pytest, so an unlisted file here is an untested file — #343.)
exec "$VENV/bin/python" -m pytest \
  "$DESKTOP_ROOT/scripts/test_sidecar_e2e.py" \
  "$DESKTOP_ROOT/scripts/test_sidecar_relay.py" \
  "$DESKTOP_ROOT/scripts/test_sidecar_relay_bind.py" \
  "$DESKTOP_ROOT/scripts/test_sidecar_env_and_api_root.py" \
  "$DESKTOP_ROOT/scripts/test_dist_build_lock.py" \
  "$DESKTOP_ROOT/scripts/test_assemble_web_resources.py" \
  "$DESKTOP_ROOT/scripts/test_build_web_standalone.py" \
  -v "$@"
