#!/usr/bin/env bash
# D5 (V02-53B evidence, V02-54 path): build the static-export frontend for the desktop shell.
#
# 1. Creates a throwaway git worktree at HEAD (business tree stays untouched).
# 2. Applies patches/web-static-export.patch (inert without env flags).
# 3. Runs `next build` with MANGAFLOW_STATIC_EXPORT=1.
# 4. Copies the export into apps/desktop/dist/frontend for the Tauri shell.
#
# Turbopack rejects node_modules symlinks pointing outside the project root,
# so the worktree lives next to the repo on the same filesystem and the
# dependency tree is hardlink-cloned (cp -al) instead of symlinked.
set -euo pipefail
DESKTOP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "$DESKTOP_ROOT/../.." && pwd)"
PARENT="$(dirname "$REPO_ROOT")"
WORKTREE="$(mktemp -d "$PARENT/mangaflow-desktop-web-XXXXXX")"

# The static-export swap below deletes and repopulates dist/frontend while
# other writers touch sibling dist/ trees (#350); the lock is released on
# every exit path through the trap.
source "$DESKTOP_ROOT/scripts/dist-build-lock.sh"
DIST_LOCK="$DESKTOP_ROOT/dist/.build.lock"
dist_lock_held=0
release_dist_lock_if_held() {
  if [ "$dist_lock_held" -eq 1 ]; then
    release_dist_build_lock "$DIST_LOCK"
    dist_lock_held=0
  fi
}

cleanup() {
  release_dist_lock_if_held
  git -C "$REPO_ROOT" worktree remove --force "$WORKTREE" >/dev/null 2>&1 || true
  rm -rf "$WORKTREE"
}
trap cleanup EXIT

git -C "$REPO_ROOT" worktree add --detach "$WORKTREE" HEAD >/dev/null
git -C "$WORKTREE" apply "$DESKTOP_ROOT/patches/web-static-export.patch"

# Hardlink-clone the installed workspace dependencies (same filesystem).
cp -al "$REPO_ROOT/node_modules" "$WORKTREE/node_modules"
if [ -d "$REPO_ROOT/apps/web/node_modules" ]; then
  cp -al "$REPO_ROOT/apps/web/node_modules" "$WORKTREE/apps/web/node_modules"
fi

# The build log tee below writes into dist/, which may not exist yet on a
# fresh checkout; the lock's own mkdir only runs at acquire time (#350),
# i.e. AFTER the build. Create just the directory here so the tee cannot
# fail before the lock is ever taken — nothing destructive happens outside
# the lock; the rm -rf + repopulate section below stays inside it.
mkdir -p "$DESKTOP_ROOT/dist"

cd "$WORKTREE/apps/web"
MANGAFLOW_STATIC_EXPORT=1 NEXT_TELEMETRY_DISABLED=1 \
  "$WORKTREE/node_modules/.bin/next" build 2>&1 | tee "$DESKTOP_ROOT/dist/static-build.log"

if [ ! -f out/index.html ]; then
  echo "static export did not produce out/index.html" >&2
  exit 1
fi

# Destructive section (#350): the delete+repopulate of dist/frontend must
# not interleave with the other dist/ writers (build-web-standalone.py's
# rmtree+move, the e2e runner's rebuild) or a serving run reading the tree.
acquire_dist_build_lock "$DIST_LOCK" 600
dist_lock_held=1
rm -rf "$DESKTOP_ROOT/dist/frontend"
mkdir -p "$DESKTOP_ROOT/dist/frontend"
cp -r out/. "$DESKTOP_ROOT/dist/frontend/"
# The shell-owned tools page is not part of the web export; keep it shipped.
cp "$DESKTOP_ROOT/shell/shell-tools.html" "$DESKTOP_ROOT/dist/frontend/"
release_dist_build_lock "$DIST_LOCK"
dist_lock_held=0
echo "static export copied to $DESKTOP_ROOT/dist/frontend"
