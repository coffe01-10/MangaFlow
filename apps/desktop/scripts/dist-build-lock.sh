#!/usr/bin/env bash
# Portable exclusive lock for the writers of apps/desktop/dist/ (#350).
#
# Three destructive writers share the dist/ tree (build-frontend-static.sh's
# rm -rf + repopulate of dist/frontend, build-web-standalone.py's rmtree +
# move of dist/web-standalone, and the e2e runner's rebuild of the same
# bundle), so each destructive section must hold this lock; a concurrent
# build or serving run must never observe a half-deleted tree.
#
# Mechanism, by platform capability:
#   1. POSIX flock(1) on the stable lock file. The mutex is the open file
#      description, so a crashed holder releases it at process death and
#      no stale file can wedge later builds. This is the same mechanism
#      build-web-standalone.py's fcntl.flock branch uses, so bash and
#      python writers interlock with each other.
#   2. A noclobber (O_EXCL) lock file with retry + timeout, for hosts
#      without flock (git bash on Windows ships no util-linux). The lock
#      FILE is the mutex: a holder that dies without releasing leaves it
#      behind, and later writers fail loudly after the timeout with the
#      remedy spelled out (dist/ is disposable build output).
# The two forms never mix on one host: flock exists exactly where python's
# fcntl does, so whichever mechanism a platform has, every writer on that
# platform uses it.
#
# This file is SOURCEd (no side effects at source time); the functions are
# exercised by test_dist_build_lock.py, which run-sidecar-e2e.sh collects.

# acquire_dist_build_lock <lock_path> <timeout_seconds>
# Uses file descriptor 9 for the flock form; callers must not close it
# while the critical section runs. Returns non-zero on timeout.
acquire_dist_build_lock() {
  local lock_path="$1" timeout_seconds="$2"
  mkdir -p "$(dirname "$lock_path")"
  if command -v flock >/dev/null 2>&1; then
    exec 9>"$lock_path"
    if ! flock -w "$timeout_seconds" 9; then
      echo "dist build lock: timed out after ${timeout_seconds}s waiting for $lock_path" >&2
      return 1
    fi
    return 0
  fi
  local waited=0
  until ( set -o noclobber; printf '%s\n' "$$" > "$lock_path" ) 2>/dev/null; do
    if [ "$waited" -ge "$timeout_seconds" ]; then
      echo "dist build lock: timed out after ${timeout_seconds}s waiting for $lock_path" >&2
      echo "dist build lock: if no build is running, delete the stale lock file" >&2
      return 1
    fi
    sleep 1
    waited=$((waited + 1))
  done
}

# release_dist_build_lock <lock_path>
# Must be called with the SAME lock path the section acquired. Safe to
# call from an EXIT trap after a release that already happened (the flock
# form tolerates a closed descriptor only via the caller's own held-flag
# discipline; keep acquire/release paired or guard the trap).
release_dist_build_lock() {
  local lock_path="$1"
  if command -v flock >/dev/null 2>&1; then
    flock -u 9 2>/dev/null || true
    exec 9>&-
    return 0
  fi
  rm -f "$lock_path"
}
