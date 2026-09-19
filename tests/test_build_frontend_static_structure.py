"""Structural pins for build-frontend-static.sh's #734 gates (the c106c43
precedent: bash scripts have no in-process falsifier, but a DROPPED guard
is structurally detectable — the text-level analogue of the #943
source-split pins)."""

from __future__ import annotations

from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "apps" / "desktop" / "scripts"
_SCRIPT = _SCRIPTS / "build-frontend-static.sh"
_GATE = _SCRIPTS / "chunk-consistency-gate.sh"


def _source() -> str:
    return _SCRIPT.read_text(encoding="utf-8")


def test_stamp_write_is_inside_the_locked_section():
    """#742: the provenance stamp is a WRITE into the #350-locked dist
    trees — it must sit between the lock acquire and the release in the
    file order. #741 originally landed it after the release (an
    unlocked-write slip)."""

    source = _source()
    acquire_index = source.index('acquire_dist_build_lock "$DIST_LOCK"')
    stamp_index = source.index("built_at")
    release_index = source.index('release_dist_build_lock "$DIST_LOCK"', stamp_index)
    assert acquire_index < stamp_index < release_index, (
        "the stamp write must stay inside the acquired-lock window"
    )
    assert 'dist/frontend/build-info.json' in source


def test_extracted_gate_is_invoked_by_the_build():
    """The chunk-consistency gate was extracted to its own sourceable
    helper (contract-tested there); the build script must keep INVOKE it
    against the freshly built out/ tree — a refactor that drops the
    invocation re-opens the #734 internally-inconsistent-export hole."""

    gate = _GATE.read_text(encoding="utf-8")
    assert "references missing chunk" in gate
    source = _source()
    assert 'check_referenced_chunks "$WORKTREE/apps/web/out"' in source, (
        "the build must invoke the extracted consistency gate on out/"
    )
    # Ordering (DS-04): the gate runs against out/ BEFORE the destructive
    # dist/frontend replace — a failing gate must not leave a known-broken
    # export in the shippable path.
    assert source.index("check_referenced_chunks") < source.index(
        'rm -rf "$DESKTOP_ROOT/dist/frontend"'
    ), "the gate must precede the destructive dist/frontend replace"
