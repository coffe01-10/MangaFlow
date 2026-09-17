"""Durability-order pins for the sidecar ownership journal (#834).

``_write_journal`` publishes a state by staging
``owner.json.helper.pending`` and ``os.replace``-ing it over
``owner.json``. Since the red-team round #824 the payload is fsynced
before that replace, so a power loss cannot leave a zero-length or torn
journal that mark_stopped-style readers and the stale-runtime sweep would
then treat as unreadable forever.

The fsync-before-replace ORDER is the contract: no other pin in this
directory asserts it, so a regression that drops the fsync (or moves it
after the replace) is invisible to every gate. These pins run wherever
the helper itself runs — no mkfifo/Unix gating here, unlike
test_sidecar_journal.py, because fsync-before-replace is platform-
neutral (Windows: FlushFileBuffers).

Import pattern follows test_sidecar_journal.py (importlib on the helper
file — the sidecar is not an importable package).
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
HELPER = REPO_ROOT / "apps/desktop/sidecar/mangaflow_desktop_helper.py"

TOKEN = "0123456789abcdef0123456789abcdef"


def _load_helper():
    spec = importlib.util.spec_from_file_location(
        "mangaflow_desktop_helper_fsync", str(HELPER)
    )
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    return helper


def _journal_path(tmp_path: Path) -> Path:
    runtime = tmp_path / "runtime" / f"mangaflow-desktop-{TOKEN}"
    runtime.mkdir(parents=True)
    return runtime / "owner.json"


def _record(state: str) -> dict:
    return {"version": 1, "token": TOKEN, "role": "app", "state": state}


def test_write_journal_fsyncs_the_payload_before_the_replace(
    tmp_path: Path, monkeypatch
):
    """The fsync must land on the pending payload's fd BEFORE the
    os.replace that publishes it — anything else leaves a window where a
    crash produces an unpublished or torn journal."""

    helper = _load_helper()
    journal = _journal_path(tmp_path)
    real_fsync = os.fsync
    real_replace = os.replace
    calls: list[tuple[str, object]] = []

    def _fsync_spy(fd):
        calls.append(("fsync", fd))
        return real_fsync(fd)

    def _replace_spy(src, dst, **kwargs):
        calls.append(("replace", str(src)))
        return real_replace(src, dst, **kwargs)

    monkeypatch.setattr(os, "fsync", _fsync_spy)
    monkeypatch.setattr(os, "replace", _replace_spy)

    helper._write_journal(journal, _record("ready"))

    fsync_calls = [c for c in calls if c[0] == "fsync"]
    replace_calls = [c for c in calls if c[0] == "replace"]
    assert len(fsync_calls) == 1, f"exactly one payload fsync: {calls}"
    assert len(replace_calls) == 1, f"exactly one publish: {calls}"
    assert calls.index(fsync_calls[0]) < calls.index(replace_calls[0]), (
        f"fsync must precede the replace: {calls}"
    )
    # The fsynced fd is the pending payload's, and the publish renames the
    # pending sibling onto the journal (never a direct write).
    assert isinstance(fsync_calls[0][1], int), f"fsync got a real fd: {calls}"
    assert replace_calls[0][1].endswith("owner.json.helper.pending"), (
        f"the pending sibling is what gets published: {calls}"
    )
    assert json.loads(journal.read_text(encoding="utf-8"))["state"] == "ready"


def test_write_journal_keeps_the_prior_journal_when_fsync_fails(
    tmp_path: Path, monkeypatch
):
    """A failing fsync must abort BEFORE the replace: the prior journal
    content survives and nothing is published over it."""

    helper = _load_helper()
    journal = _journal_path(tmp_path)
    prior = _record("ready")
    journal.write_text(json.dumps(prior), encoding="utf-8")

    def _broken_fsync(fd):
        raise OSError("simulated: disk gone")

    published: list[tuple[object, object]] = []
    monkeypatch.setattr(os, "fsync", _broken_fsync)
    monkeypatch.setattr(
        os, "replace", lambda src, dst, **kw: published.append((src, dst))
    )

    try:
        helper._write_journal(journal, _record("stopped"))
    except OSError:
        pass
    else:
        raise AssertionError("the fsync failure must propagate to the caller")

    assert published == [], "a failed-fsync payload must never be published"
    assert json.loads(journal.read_text(encoding="utf-8")) == prior, (
        "the prior journal content must survive untouched"
    )
