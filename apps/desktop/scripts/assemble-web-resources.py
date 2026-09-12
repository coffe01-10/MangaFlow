"""Assemble the desktop web resources for the install bundle (W-15 slice 3).

Lays out, under `apps/desktop/src-tauri/web/` (tauri.conf resources, shipped
next to the shell executable by the NSIS/MSI installers):
  web/node/node.exe        (node runtime: NODE_EXE env, PATH, or the known
                            C:\\node\\node.exe location)
  web/standalone/...       (the verified relocated standalone tree produced
                            by build-web-standalone.py)

Run build-web-standalone.py first. The assembled tree is gitignored
(85MB of build artifacts); rebuild it before every `tauri build`.

The tree is never rebuilt in place. Everything is assembled into a sibling
staging directory (`web.tmp-<pid>`, same volume) and swapped in with
renames, so a mid-copy failure (disk full, file locked by a running app,
Ctrl-C) leaves the previous tree byte-identical instead of a truncated
bundle that the next `tauri build` would ship silently. The same guarantee
covers the swap itself: any failure after the old tree is moved aside (the
final rename failing, or an async KeyboardInterrupt/SystemExit/MemoryError
landing in the window between the two renames) rolls the old tree back
before the error propagates; if even the rollback fails, the retired tree
is kept on disk and a loud error explains how to restore it manually. The
old tree is only deleted after the new one is complete and in place
(#347, #382). A re-run that happens to reuse the parked tree's pid is
refused in that state: with `res` still missing, `web.old-<pid>` is the
only copy of the previous tree, and that run would delete it before
staging anything, so assemble fails fast and repeats the manual-restore
instructions (#393). While `res` is missing, no run touches or deletes
the parked tree; once any later run has swapped a complete tree back into
place, parked remnants from other pids are no longer the only copy of
anything and are swept as build debris (#409).
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SRC = REPO / "apps/desktop/dist/web-standalone"
RES = REPO / "apps/desktop/src-tauri/web"


def find_node() -> Path:
    override = os.environ.get("NODE_EXE")
    if override and Path(override).is_file():
        return Path(override)
    which = shutil.which("node")
    if which:
        return Path(which)
    fallback = Path(r"C:\node\node.exe")
    if fallback.is_file():
        return fallback
    raise SystemExit("no node runtime found (set NODE_EXE or put node on PATH)")


def _clear(path: Path) -> None:
    """Clear a crash remnant of an earlier run that happened to reuse this
    pid — best-effort first, refuse if a remnant survives (#409).

    A strict rmtree on a remnant holding one locked file (AV/indexer
    handle from the crashed run) aborted the whole build with a raw
    OSError mid-delete. Best-effort first; if even that cannot finish,
    refuse loudly — the staged copytree must never merge the new tree
    into a half-cleared remnant.
    """

    if not path.exists():
        return
    try:
        shutil.rmtree(path, ignore_errors=True)
    except OSError:
        pass  # truly best-effort: the refusal below decides the outcome
    if path.exists():
        raise SystemExit(
            f"could not fully clear {path} (a file inside is locked — AV/"
            "indexer/running app?); delete it manually and re-run."
        )


def _sweep_orphan_staging(res: Path) -> None:
    """Best-effort cleanup of staging remnants from *other* pids (#409).

    `web.old-<pid>`/`web.tmp-<pid>` siblings are removable only while `res`
    is in place: with `res` missing, a `web.old-*` may be the recovery copy
    the #382 rollback-failure path preserved (#393), so those are left to
    the existing refusal/recovery flow. Without this sweep, an ignored
    rmtree failure in a previous run's finally-block (AV/indexer handle)
    left a permanent ~85MB `web.old-<pid>` that a later pid-reusing run
    then hit with a strict rmtree and aborted. Failures here are ignored —
    this is hygiene, not correctness.
    """
    if not res.exists():
        return
    mine = f"{res.name}.old-{os.getpid()}"
    for sibling in res.parent.glob(f"{res.name}.old-*"):
        if sibling.name != mine:
            shutil.rmtree(sibling, ignore_errors=True)
    for sibling in res.parent.glob(f"{res.name}.tmp-*"):
        shutil.rmtree(sibling, ignore_errors=True)


def _recovery_error(res: Path, retired: Path) -> RuntimeError:
    return RuntimeError(
        f"web resource swap failed and the automatic rollback failed too: "
        f"{res} is missing and the only copy of the previous tree is parked "
        f"at {retired}. Do not delete it. Manually move {retired} back to "
        f"{res} (for example Move-Item '{retired}' '{res}') before the "
        "next tauri build. Re-running assemble instead of restoring "
        "manually is also safe: the new tree is swapped in and this parked "
        "copy is then removed as build debris."
    )


def _stale_retired_error(res: Path, retired: Path) -> RuntimeError:
    return RuntimeError(
        f"a previous assemble left {res} missing and {retired} holding the "
        f"only copy of the previous tree; refusing to run because this run "
        f"would delete that copy before swapping in the new tree. "
        f"Do not delete it. Manually move {retired} back to {res} (for "
        f"example Move-Item '{retired}' '{res}') first, then re-run. A "
        "different-pid run may proceed, and once it has swapped the new "
        "tree in, this parked copy is removed as build debris."
    )


def _dist_lock():
    """The shared dist/ build lock, reused from build-web-standalone (#457).

    The hyphenated sibling filename is not importable by name, so the spec
    load keeps ONE lock implementation instead of a diverging copy. The
    whole assemble — sweep, staging, the two-rename swap, and the
    post-swap debris sweep — runs under it: two concurrent assembles can
    no longer delete each other's live staging (`web.tmp-<pid>`) or a
    rollback source parked inside the rename window, and an in-flight
    standalone rebuild cannot hand assemble a half-swapped source tree.
    """

    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "build_web_standalone_lock",
        Path(__file__).with_name("build-web-standalone.py"),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._dist_build_lock(module.DIST_LOCK_PATH)


def assemble(src: Path = SRC, res: Path = RES, node: Path | None = None) -> Path:
    """Assemble the resource tree beside ``res`` and swap it in atomically.

    Build order: stage the complete new tree under ``<res>.tmp-<pid>``,
    move the old tree aside to ``<res>.old-<pid>``, rename the staged tree
    into place, and only then delete the retired tree. Any failure before
    the old tree is moved aside leaves ``res`` untouched (the staged
    half-tree is cleaned up). Any failure after that moment, whether the
    final rename failing on its own or an async exception
    (KeyboardInterrupt/SystemExit/MemoryError) landing in the window
    between the two renames, rolls the old tree back before the error
    propagates. If even the rollback fails, the retired tree is kept on
    disk (the cleanup never deletes it) and a RuntimeError with manual
    recovery instructions is raised (#382). A re-run while that parked
    copy is still the only one (``res`` missing, same-pid ``retired``
    present) is refused with the same instructions before anything is
    staged or deleted (#393).

    Everything above runs under the shared dist/ build lock (#457):
    concurrent assembles serialize instead of racing their sweeps against
    each other's live staging/rollback trees.
    """
    if not (src / "server.js").is_file():
        raise SystemExit("run build-web-standalone.py first")
    if node is None:
        node = find_node()
    with _dist_lock():
        return _assemble(src, res, node)


def _assemble(src: Path, res: Path, node: Path) -> Path:
    """Locked body of :func:`assemble` (caller holds the dist lock)."""
    _sweep_orphan_staging(res)
    staging = res.parent / f"{res.name}.tmp-{os.getpid()}"
    retired = res.parent / f"{res.name}.old-{os.getpid()}"
    _clear(staging)
    # A retired tree beside a live `res` is a crash remnant from an earlier
    # run that reused this pid (`res` in place means it is not the only
    # copy). With `res` missing, `retired` is the recovery copy the #382
    # rollback-failure path deliberately preserved: a same-pid re-run used
    # to delete it here, destroying the only old tree before the new one
    # was even staged, so refuse until it is restored (#393).
    if retired.exists() and not res.exists():
        raise _stale_retired_error(res, retired)
    # With `res` in place a same-pid remnant is pure hygiene — a strict
    # rmtree here (locked file from the previous crashed run) aborted the
    # whole build (#409), so mirror the finally-block's best-effort
    # semantics instead. The refusal above already protected the case
    # where the remnant is the only recovery copy.
    if retired.exists():
        shutil.rmtree(retired, ignore_errors=True)
    rollback_failed = False
    try:
        (staging / "node").mkdir(parents=True)
        shutil.copy2(node, staging / "node" / "node.exe")
        shutil.copytree(src, staging / "standalone")
        try:
            if res.exists():
                os.rename(res, retired)
            os.rename(staging, res)
        except BaseException:
            # Same-volume renames practically do not fail, but any failure
            # from the moment the old tree is moved aside must restore it.
            # The BaseException catch is load-bearing: an async Ctrl-C,
            # SystemExit, or MemoryError landing between the two renames
            # would otherwise skip a guard around the second rename alone
            # and reach the cleanup with the resource root missing.
            # Re-raise either way (KeyboardInterrupt included).
            if retired.exists() and not res.exists():
                try:
                    os.rename(retired, res)
                except OSError as rollback_exc:
                    rollback_failed = True
                    raise _recovery_error(res, retired) from rollback_exc
            raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        # Delete the retired tree only once `res` is in place again (the
        # new tree swapped in, or the old tree rolled back). If `res` is
        # missing while `retired` still holds the only copy of the old
        # tree, keep it for manual recovery and fail loudly instead of
        # silently deleting the last copy.
        if res.exists():
            shutil.rmtree(retired, ignore_errors=True)
        elif retired.exists() and not rollback_failed:
            # Covers rollback attempts that died with a non-OSError; the
            # rollback-failed path above already raises its own error.
            raise _recovery_error(res, retired)
    # Sweep crash remnants from OTHER pids: a run SIGKILLed inside the
    # two-rename window parks `<res>.old-<pid>` / `<res>.tmp-<pid>` trees
    # that a later different-pid run never touched (85 MB+ of gitignored
    # debris per occurrence). Safe exactly when `res` is in place - the
    # parked trees are by then no longer the only copy of anything.
    for remnant in res.parent.glob(f"{res.name}.old-*"):
        shutil.rmtree(remnant, ignore_errors=True)
    for remnant in res.parent.glob(f"{res.name}.tmp-*"):
        shutil.rmtree(remnant, ignore_errors=True)

    mb = sum(f.stat().st_size for f in res.rglob("*") if f.is_file()) / 1048576
    print(f"WEB_RESOURCES_READY {res} ({mb:.0f} MB)")
    return res


if __name__ == "__main__":
    assemble()
