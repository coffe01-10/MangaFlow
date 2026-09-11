#!/usr/bin/env bash
# Portable exclusive lock for the writers of apps/desktop/dist/ (#350).
#
# Three destructive WRITERS share the dist/ tree (build-frontend-static.sh's
# rm -rf + repopulate of dist/frontend, build-web-standalone.py's rmtree +
# move of dist/web-standalone, and the e2e runner's rebuild of the same
# bundle), so each destructive section must hold this lock and two writers
# can never interleave their deletions. READERS take nothing: a serving run
# (verify-static-origin.mjs) or a browser pointed at dist/frontend during a
# rebuild can observe the half-deleted window - that residual is documented,
# not covered here (round-5 review F-Doc).
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
# #383: this pairing only holds when bash and python come from the same
# environment source. On this repo's main path (git bash driving the
# Windows-native venv python) NEITHER side has flock/fcntl, so every
# writer takes the noclobber lock-file branch and they interlock through
# it. A mixed install — e.g. MSYS2 bash (has flock) driving a
# Windows-native python (no fcntl) — puts the bash writers on flock and
# the python writers on the lock file; the two mechanisms cannot see
# each other, so mutual exclusion silently fails. Such mixed hosts are
# unsupported.
#
# This file is SOURCEd (no side effects at source time); the functions are
# exercised by test_dist_build_lock.py, which run-sidecar-e2e.sh collects.

# The chosen mechanism is LATCHED at acquire time (round-6 review F-NIT):
# release must use the SAME mechanism acquire did. Re-probing `command -v
# flock` at release would take the flock arm while the noclobber lock file
# still exists - skipping the rm -f and wedging every later build into the
# timeout.
DIST_LOCK_MECHANISM=""

# acquire_dist_build_lock <lock_path> <timeout_seconds>
# Uses file descriptor 9 for the flock form; callers must not close it
# while the critical section runs. Returns non-zero on timeout.
# MANGAFLOW_DIST_LOCK_FORCE=noclobber pins the lock-file branch even on
# hosts that have flock(1) — a test seam, unset in production.
acquire_dist_build_lock() {
  local lock_path="$1" timeout_seconds="$2"
  mkdir -p "$(dirname "$lock_path")"
  if [ "${MANGAFLOW_DIST_LOCK_FORCE:-}" != "noclobber" ] && command -v flock >/dev/null 2>&1; then
    # Latched (round-6 review F-NIT, merged as the latch PR): release must
    # use the SAME mechanism acquire did — see release below.
    DIST_LOCK_MECHANISM="flock"
    exec 9>"$lock_path"
    if ! flock -w "$timeout_seconds" 9; then
      # Nothing was acquired, so nothing must stay held: close the
      # descriptor a later release's `flock -u 9` would otherwise aim at
      # whatever descriptor 9 has become by then.
      exec 9>&-
      echo "dist build lock: timed out after ${timeout_seconds}s waiting for $lock_path" >&2
      return 1
    fi
    return 0
  fi
  DIST_LOCK_MECHANISM="noclobber"
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
# call from an EXIT trap after a release that already happened — and,
# on the lock-file branch, after an acquire that TIMED OUT: release only
# deletes the file when it still names this shell's pid, so a timed-out
# waiter's trap can no longer cut the winner's lock short (pid reuse by
# an unrelated process remains the same theoretical caveat the lock-file
# mechanism already carries). The flock form holds the mutex on the open
# file description, not the file, so its release is unconditional.
release_dist_build_lock() {
  local lock_path="$1"
  if [ "$DIST_LOCK_MECHANISM" = "flock" ] && [ "${MANGAFLOW_DIST_LOCK_FORCE:-}" != "noclobber" ] && command -v flock >/dev/null 2>&1; then
    flock -u 9 2>/dev/null || true
    exec 9>&-
    DIST_LOCK_MECHANISM=""
    return 0
  fi
  if [ "$DIST_LOCK_MECHANISM" = "noclobber" ]; then
    # Master's pid-ownership check: a timed-out waiter's EXIT trap must not
    # cut the winner's lock short (pid reuse caveat unchanged).
    if grep -qx "$$" "$lock_path" 2>/dev/null; then
      rm -f "$lock_path"
    fi
    DIST_LOCK_MECHANISM=""
  fi
}
