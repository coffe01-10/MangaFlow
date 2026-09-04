#!/usr/bin/env bash
# Run the V02-53B PoC sidecar e2e (real API + fake model channel) in the
# sandbox venv. Creates .venv-poc on first use.
set -euo pipefail
POC_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "$POC_ROOT/../.." && pwd)"
VENV="$REPO_ROOT/.venv-poc"

if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q -r "$REPO_ROOT/apps/api/requirements.txt" -r "$REPO_ROOT/apps/api/requirements-dev.txt"
fi

export MANGAFLOW_POC_PYTHON="$VENV/bin/python"
export MANGAFLOW_POC_HELPER="$POC_ROOT/sidecar/mangaflow_poc_helper.py"
cd "$REPO_ROOT"
exec "$VENV/bin/python" -m pytest "$POC_ROOT/scripts/test_poc_sidecar_e2e.py" -v "$@"
