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
  # Fast path: a stamp that already matches is immutable evidence of a
  # completed install (the publish below is an atomic rename), so trusting
  # it needs no lock.
  if resolve_venv_python "$venv" >/dev/null \
     && [ -f "$stamp_file" ] \
     && [ "$(cat "$stamp_file" 2>/dev/null)" = "$expected" ]; then
    return 0
  fi
  # Slow path (#586): two runners reaching this point would interleave pip
  # installs into the SAME venv and both write the stamp — blessing a
  # corrupt venv forever, the exact never-self-heals state the stamp exists
  # to prevent. A mkdir lock serializes check → install → stamp (atomic on
  # POSIX and git-bash alike; flock is not shipped with git-bash). The lock
  # is a SIBLING of the venv, never a child: on the fresh path the venv dir
  # does not exist yet, and a lock inside it could neither be created nor
  # hold the venv creation it gates. A lock orphaned by a killed run is
  # broken after 30 minutes of silence — before that, a live install is
  # indistinguishable from a stale lock.
  local lock_dir="${venv}.bootstrap-lock"
  local waited=0
  local max_wait="${MANGAFLOW_E2E_BOOTSTRAP_MAX_WAIT:-900}"
  until mkdir "$lock_dir" 2>/dev/null; do
    if [ -n "$(find "$lock_dir" -maxdepth 0 -mmin +30 2>/dev/null)" ]; then
      rm -rf "$lock_dir"
      continue
    fi
    waited=$((waited + 5))
    if [ "$waited" -ge "$max_wait" ]; then
      echo "ensure_e2e_venv: bootstrap lock $lock_dir still held after ${waited}s; remove it if no other runner is active" >&2
      return 1
    fi
    sleep 5
  done
  local status=0
  _ensure_e2e_venv_locked "$venv" "$stamp_file" "$expected" "$@" || status=$?
  # Single release point: every exit of the critical section lands here.
  rmdir "$lock_dir" 2>/dev/null || true
  return "$status"
}

_ensure_e2e_venv_locked() {
  local venv="$1" stamp_file="$2" expected="$3"; shift 3
  # Re-check inside the lock: the runner that held it before us may have
  # completed the install while we waited.
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
    # Atomic publish: a concurrent fast-path reader must see either the old
    # stamp or the new one, never a partial file (#586).
    printf '%s\n' "$expected" > "$stamp_file.tmp.$$" \
      && mv -f "$stamp_file.tmp.$$" "$stamp_file"
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
# The full output lands in a persistent last-run log: a load flake that
# only shows up once (two singletons in one weekend were lost to
# `tail -1` pipelines) must keep its failure identity for triage. dist/
# is gitignored build output, so the log is disposable by construction.
# pipefail preserves pytest's exit code through the tee.
E2E_LOG_PATH="$DESKTOP_ROOT/dist/e2e-last-run.log"
pytest_exit=0
"$VENV_PYTHON" -m pytest \
  "$DESKTOP_ROOT/scripts/test_sidecar_e2e.py" \
  "$DESKTOP_ROOT/scripts/test_sidecar_relay.py" \
  "$DESKTOP_ROOT/scripts/test_sidecar_relay_bind.py" \
  "$DESKTOP_ROOT/scripts/test_sidecar_env_and_api_root.py" \
  "$DESKTOP_ROOT/scripts/test_dist_build_lock.py" \
  "$DESKTOP_ROOT/scripts/test_assemble_web_resources.py" \
  "$DESKTOP_ROOT/scripts/test_build_web_standalone.py" \
  "$DESKTOP_ROOT/scripts/test_guard_frontend_dist.py" \
  "$DESKTOP_ROOT/scripts/test_run_sidecar_e2e.py" \
  -v "$@" 2>&1 | tee "$E2E_LOG_PATH" || pytest_exit=$?
exit "$pytest_exit"
fi
