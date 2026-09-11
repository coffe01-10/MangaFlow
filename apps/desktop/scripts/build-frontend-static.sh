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
# dependency tree is hardlink-cloned (cp -al) instead of symlinked — with
# junction entries skipped and rebuilt inside the worktree (#385).
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
  # Hardlink-dense trees make rm -rf flake on this host (transient rd /s
  # /q-style failures); retry once, then warn without failing the build —
  # the disposable mangaflow-desktop-web-* naming makes any residue
  # findable for manual deletion.
  if ! rm -rf "$WORKTREE"; then
    sleep 2
    rm -rf "$WORKTREE" || echo "warning: could not fully remove $WORKTREE (disposable; delete manually)" >&2
  fi
}
trap cleanup EXIT

git -C "$REPO_ROOT" worktree add --detach "$WORKTREE" HEAD >/dev/null
git -C "$WORKTREE" apply "$DESKTOP_ROOT/patches/web-static-export.patch"

# Hardlink-clone the installed workspace dependencies (same filesystem).
# #385: cp -al cannot hardlink an NTFS junction — git bash lstats the npm
# workspace self-link (node_modules/@mangaflow/web) as a symlink and
# link(2) on it fails with Permission denied — so the clone skips link
# entries and rebuilds each one afterwards as a junction INSIDE the
# worktree (a clone pointing back at the business tree would break the
# worktree sealing). The junction sits one directory below node_modules'
# top level, so the copy recurses into any subtree that contains a link
# instead of handing it to cp -al wholesale.
# clone_hardlink_tree <src> <dst> <rel_prefix>: copy every child of src
# into a fresh dst via cp -al, skipping link entries (junctions) and
# printing each skipped path relative to the CLONED TREE ROOT — the prefix
# accumulates across recursions so a nested skip (e.g. @mangaflow/web)
# reports its full tree-relative path, not the subtree-local one.
clone_hardlink_tree() {
  local src="$1" dst="$2" prefix="$3" entry name
  mkdir -p "$dst"
  for entry in "$src"/* "$src"/.[!.]* "$src"/..?*; do
    # Unmatched glob patterns survive as literals; skip those.
    [ -e "$entry" ] || [ -L "$entry" ] || continue
    name="${entry##*/}"
    if [ -L "$entry" ]; then
      printf '%s\n' "$prefix$name"
      continue
    fi
    if [ -d "$entry" ] && [ -n "$(find "$entry" -type l -print -quit)" ]; then
      clone_hardlink_tree "$entry" "$dst/$name" "$prefix$name/"
      continue
    fi
    cp -al "$entry" "$dst/$name"
  done
}

# recreate_junction <tree_rel> <link_rel>: rebuild one skipped link entry.
# The source link's target keeps only its repo-relative tail, re-anchored
# at the throwaway worktree, so the clone points at the worktree's OWN
# apps/web. Junction targets come back from readlink as absolute msys
# paths; relative targets (npm/pnpm .bin style, e.g.
# ../next/dist/bin/next) are resolved against the link's own directory to
# an absolute path first — the raw text never matches the REPO_ROOT
# prefix, which used to kill the build on every in-tree relative link
# (#392). mklink /J needs no privilege; the // escaping keeps MSYS from
# rewriting the switches as paths. File targets (junctions are
# directory-only) are rebuilt as hardlinks of the worktree's own copy.
recreate_junction() {
  local tree_rel="$1" link_rel="$2" target link_dir target_abs target_kind new_target
  target="$(readlink "$REPO_ROOT/$tree_rel/$link_rel")" || {
    echo "cannot read junction target for $tree_rel/$link_rel (#385)" >&2
    return 1
  }
  if [ -z "$target" ]; then
    echo "cannot read junction target for $tree_rel/$link_rel (#385)" >&2
    return 1
  fi
  link_dir="$(dirname "$REPO_ROOT/$tree_rel/$link_rel")"
  # Resolve the raw target text to an absolute path (#392): a relative
  # target resolves against the link's own directory, not the repo root.
  # Directory targets resolve by cd-ing into them; file targets resolve
  # through their parent directory with the base name joined back (cd
  # cannot enter a file). A missing target resolves to nothing and is
  # reported separately from an outside-the-repo target below — including
  # absolute targets: a dangling one must not escape with an empty
  # target_kind into the junction branch, where cmd would fail with its
  # raw message instead of this script's diagnosis (#396).
  target_abs=""
  target_kind=""
  if [ "${target:0:1}" = "/" ]; then
    if [ -d "$target" ]; then
      target_abs="$target"
      target_kind=dir
    elif [ -e "$target" ]; then
      target_abs="$target"
      target_kind=file
    fi
  elif [ -d "$link_dir/$target" ]; then
    target_abs="$(cd "$link_dir/$target" && pwd)"
    target_kind=dir
  elif [ -e "$link_dir/$target" ]; then
    target_abs="$(cd "$link_dir/$(dirname "$target")" && pwd)/$(basename "$target")"
    target_kind=file
  fi
  if [ -z "$target_abs" ]; then
    echo "link $tree_rel/$link_rel target '$target' does not exist or cannot be resolved from $link_dir (#392)" >&2
    return 1
  fi
  case "$target_abs" in
    "$REPO_ROOT"/*)
      new_target="$WORKTREE${target_abs#"$REPO_ROOT"}"
      ;;
    *)
      echo "link $tree_rel/$link_rel points outside the repo ($target_abs); unsupported layout (#385)" >&2
      return 1
      ;;
  esac
  if [ ! -e "$new_target" ]; then
    echo "link target $new_target missing in worktree (#385)" >&2
    return 1
  fi
  mkdir -p "$(dirname "$WORKTREE/$tree_rel/$link_rel")"
  if [ "$target_kind" = file ]; then
    # A junction can only point at a directory, so a relative file link is
    # rebuilt as a hardlink of the worktree's own copy — same filesystem,
    # no privilege needed, and the worktree stays sealed.
    cp -l "$new_target" "$WORKTREE/$tree_rel/$link_rel"
    echo "recreated file link $tree_rel/$link_rel -> $new_target"
  else
    cmd //c mklink //J "$(cygpath -w "$WORKTREE/$tree_rel/$link_rel")" "$(cygpath -w "$new_target")" >/dev/null
    echo "recreated junction $tree_rel/$link_rel -> $new_target"
  fi
}

clone_deps_tree() {
  local tree_rel="$1" skipped link_rel
  skipped="$(clone_hardlink_tree "$REPO_ROOT/$tree_rel" "$WORKTREE/$tree_rel" "")"
  while IFS= read -r link_rel; do
    [ -n "$link_rel" ] || continue
    recreate_junction "$tree_rel" "$link_rel"
  done <<< "$skipped"
}

clone_deps_tree node_modules
if [ -d "$REPO_ROOT/apps/web/node_modules" ]; then
  clone_deps_tree apps/web/node_modules
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

# Smoke gate (#385): a green `next build` plus a copied out/ tree can
# still hide a broken export (wrong output dir, empty routes). Pin the
# shell entry document and every stub combo the export patch generates
# via generateStaticParams (patches/web-static-export.patch: the poc
# section, poc asset view and poc settings routes); any missing file
# means the Tauri shell would 404 on a shipped screen. Next exports
# trailingSlash:false layout — flat <route>.html files, index.html only
# at the root — pinned here exactly as the live export produces them.
smoke_missing=0
for rel_html in \
  index.html \
  projects/poc/poc-invalid.html \
  projects/poc/assets/poc-invalid.html \
  projects/poc/settings.html
do
  if [ ! -f "$DESKTOP_ROOT/dist/frontend/$rel_html" ]; then
    echo "smoke gate: dist/frontend/$rel_html missing (#385)" >&2
    smoke_missing=1
  fi
done
if [ "$smoke_missing" -ne 0 ]; then
  exit 1
fi

echo "static export copied to $DESKTOP_ROOT/dist/frontend"
