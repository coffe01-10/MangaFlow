#!/usr/bin/env bash
# Run the desktop sidecar e2e (real API + fake model channel) in the
# sandbox venv. Creates .venv-desktop on first use.
set -euo pipefail

# ensure_e2e_venv <venv_dir> <requirements...>: create the venv if missing
# and install the requirements. A previous run interrupted mid-install used
# to leave a PARTIAL venv that every later run accepted (the only check was
# that bin/python existed) and then died at import time with confusing
# errors — and nothing ever self-healed it. A stamp file now records the
# requirements hash of the LAST COMPLETED install: missing/mismatched stamp
# means the install never finished (or the requirements changed) and the
# install is (re)run. The pip step is a function so the contract test can
# override it — a test must never touch the network.
ensure_e2e_venv() {
  local venv="$1"; shift
  # Misuse guard: with no requirement files, cat would read stdin and hang.
  if [ "$#" -eq 0 ]; then
    echo "ensure_e2e_venv: no requirement files given" >&2
    return 2
  fi
  local stamp_file="$venv/.mangaflow-bootstrap"
  local expected
  expected="$(cat "$@" | md5sum | cut -d' ' -f1)"
  if ! resolve_venv_python "$venv"; then
    # Explicit propagation, mirroring the install step below: set -e is
    # suppressed inside an if-condition caller, and the function must fail
    # before any stamp write either way.
    python3 -m venv "$venv" || return $?
  fi
  if [ ! -f "$stamp_file" ] || [ "$(cat "$stamp_file" 2>/dev/null)" != "$expected" ]; then
    # Explicit propagation: a failed install must never reach the stamp
    # write, even if a future caller drops set -e.
    install_e2e_requirements "$venv" "$@" || return $?
    printf '%s\n' "$expected" > "$stamp_file"
  fi
}

# resolve_venv_python <venv_dir>: echo the venv's interpreter path, layout
# agnostic. A Windows-hosted venv (git-bash driving Windows python — the
# Scripts/ layout start-desktop.cmd requires at
# .venv-desktop/Scripts/python.exe) has no bin/python, so the old bin-only
# predicate re-ran `python3 -m venv` on EVERY invocation and the final exec
# failed: the runner could not run on exactly the platform whose layout
# start-desktop.cmd tells users to create this venv for. POSIX venvs keep
# bin/python; Windows venvs carry Scripts/python.exe. Returns non-zero when
# neither exists (fresh venv, creation needed).
resolve_venv_python() {
  local venv="$1"
  if [ -x "$venv/bin/python" ]; then
    echo "$venv/bin/python"
    return 0
  fi
  if [ -x "$venv/Scripts/python.exe" ]; then
    echo "$venv/Scripts/python.exe"
    return 0
  fi
  return 1
}

install_e2e_requirements() {
  local venv="$1"; shift
  local venv_python
  venv_python="$(resolve_venv_python "$venv")" || return 1
  # One -r per file: `pip install -r f1 f2` would parse f2 as a requirement
  # string and fail (caught by running the real runner after the refactor).
  local pip_args=() req
  for req in "$@"; do pip_args+=(-r "$req"); done
  # The alternation keeps bash <= 4.3 (empty array + set -u = unbound
  # variable) from breaking on a zero-file call.
  "$venv_python" -m pip install -q ${pip_args[@]+"${pip_args[@]}"}
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
DESKTOP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "$DESKTOP_ROOT/../.." && pwd)"
VENV="$REPO_ROOT/.venv-desktop"

ensure_e2e_venv "$VENV" "$REPO_ROOT/apps/api/requirements.txt" "$REPO_ROOT/apps/api/requirements-dev.txt"

VENV_PYTHON="$(resolve_venv_python "$VENV")"
export MANGAFLOW_DESKTOP_PYTHON="$VENV_PYTHON"
export MANGAFLOW_DESKTOP_HELPER="$DESKTOP_ROOT/sidecar/mangaflow_desktop_helper.py"
cd "$REPO_ROOT"
# The plan B test needs the relocated Next standalone bundle; build it when
# missing (the build script verifies the relay destination, copies static
# assets, and moves the tree out of `.next`'s reach).
if [ ! -f "$REPO_ROOT/apps/desktop/dist/web-standalone/server.js" ]; then
  "$VENV_PYTHON" "$DESKTOP_ROOT/scripts/build-web-standalone.py"
fi
# The relay/bind regression suites (test_sidecar_relay*.py) are
# pure-loopback stdlib+pytest and run everywhere the e2e runs; the env/
# api-root suite pins the helper's startup ownership contract; the dist
# build lock suite keeps the shared dist/ writers mutually exclusive
# (#350); the assemble suite pins the staging sweep/swap contracts and
# their dist-lock serialization (#409/#457); the build-web-standalone
# suite pins the bundle swap's clear-refuse and atomic stamp (#461); the
# frontend-dist guard suite pins tauri's pre-bundle refusal of the
# placeholder export (#444).
# Keep them all in this runner so the contracts stay exercised
# instead of depending on someone remembering a manual pytest command.
# (pytest.ini's testpaths/norecursedirs exclude apps/desktop from a bare
# pytest, so an unlisted file here is an untested file — #343.)
exec "$VENV_PYTHON" -m pytest \
  "$DESKTOP_ROOT/scripts/test_sidecar_e2e.py" \
  "$DESKTOP_ROOT/scripts/test_sidecar_relay.py" \
  "$DESKTOP_ROOT/scripts/test_sidecar_relay_bind.py" \
  "$DESKTOP_ROOT/scripts/test_sidecar_env_and_api_root.py" \
  "$DESKTOP_ROOT/scripts/test_dist_build_lock.py" \
  "$DESKTOP_ROOT/scripts/test_assemble_web_resources.py" \
  "$DESKTOP_ROOT/scripts/test_build_web_standalone.py" \
  "$DESKTOP_ROOT/scripts/test_guard_frontend_dist.py" \
  "$DESKTOP_ROOT/scripts/test_run_sidecar_e2e.py" \
  -v "$@"
fi
