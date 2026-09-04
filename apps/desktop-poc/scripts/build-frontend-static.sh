#!/usr/bin/env bash
# V02-53B D5: build the disposable static-export frontend for the desktop PoC.
#
# 1. Creates a throwaway git worktree at HEAD (business tree stays untouched).
# 2. Applies patches/web-static-export.patch (inert without env flags).
# 3. Runs `next build` with MANGAFLOW_STATIC_EXPORT=1.
# 4. Copies the export into apps/desktop-poc/dist/frontend for the Tauri shell.
#
# Turbopack rejects node_modules symlinks pointing outside the project root,
# so the worktree lives next to the repo on the same filesystem and the
# dependency tree is hardlink-cloned (cp -al) instead of symlinked.
set -euo pipefail
POC_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "$POC_ROOT/../.." && pwd)"
PARENT="$(dirname "$REPO_ROOT")"
WORKTREE="$(mktemp -d "$PARENT/mangaflow-poc-web-XXXXXX")"

cleanup() {
  git -C "$REPO_ROOT" worktree remove --force "$WORKTREE" >/dev/null 2>&1 || true
  rm -rf "$WORKTREE"
}
trap cleanup EXIT

git -C "$REPO_ROOT" worktree add --detach "$WORKTREE" HEAD >/dev/null
git -C "$WORKTREE" apply "$POC_ROOT/patches/web-static-export.patch"

# Hardlink-clone the installed workspace dependencies (same filesystem).
cp -al "$REPO_ROOT/node_modules" "$WORKTREE/node_modules"
if [ -d "$REPO_ROOT/apps/web/node_modules" ]; then
  cp -al "$REPO_ROOT/apps/web/node_modules" "$WORKTREE/apps/web/node_modules"
fi

cd "$WORKTREE/apps/web"
MANGAFLOW_STATIC_EXPORT=1 NEXT_TELEMETRY_DISABLED=1 \
  "$WORKTREE/node_modules/.bin/next" build 2>&1 | tee "$POC_ROOT/dist/static-build.log"

if [ ! -f out/index.html ]; then
  echo "static export did not produce out/index.html" >&2
  exit 1
fi

rm -rf "$POC_ROOT/dist/frontend"
mkdir -p "$POC_ROOT/dist/frontend"
cp -r out/. "$POC_ROOT/dist/frontend/"
echo "static export copied to $POC_ROOT/dist/frontend"
