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

cleanup() {
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

cd "$WORKTREE/apps/web"
MANGAFLOW_STATIC_EXPORT=1 NEXT_TELEMETRY_DISABLED=1 \
  "$WORKTREE/node_modules/.bin/next" build 2>&1 | tee "$DESKTOP_ROOT/dist/static-build.log"

if [ ! -f out/index.html ]; then
  echo "static export did not produce out/index.html" >&2
  exit 1
fi

rm -rf "$DESKTOP_ROOT/dist/frontend"
mkdir -p "$DESKTOP_ROOT/dist/frontend"
cp -r out/. "$DESKTOP_ROOT/dist/frontend/"
echo "static export copied to $DESKTOP_ROOT/dist/frontend"
