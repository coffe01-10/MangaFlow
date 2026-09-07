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
exec "$VENV/bin/python" -m pytest "$DESKTOP_ROOT/scripts/test_sidecar_e2e.py" -v "$@"
