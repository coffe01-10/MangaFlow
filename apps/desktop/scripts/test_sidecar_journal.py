"""Ownership-journal write-path units for the desktop sidecar helper (#602).

The helper's ``_write_journal`` and the Rust shell's ``mark_stopped``
(shell-core ``protocol.rs``) both maintain
``runtime/mangaflow-desktop-<token>/owner.json`` with no mutual exclusion.
These tests pin the helper-side halves of the approved fix at the
filesystem level — deterministic, no threads, no processes:

1. Per-writer staging names: the helper stages to
   ``owner.json.helper.pending`` (the shell stages to
   ``owner.json.shell.pending``), so the two writers can no longer
   rendezvous on one pending file.
2. Terminal-state CAS: when the CURRENT journal is already terminal
   (``stopped``/``failed``) and the record about to be published is
   non-terminal (e.g. a late ``ready``), publication is SKIPPED — a late
   ``ready`` rename landing after the shell's ``stopped`` rename used to
   revive a dead session's journal to a state the stale-runtime sweep
   never reclaims (the directory leaked forever). Missing/unparsable
   current journals fail open; terminal→terminal transitions publish.

Import pattern follows ``test_sidecar_e2e.py`` (importlib on the helper
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
        "mangaflow_desktop_helper_journal", str(HELPER)
    )
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    return helper


def _journal_path(tmp_path: Path) -> Path:
    """A runtime-shaped journal path (the name the helper's own checks use)."""
    runtime = tmp_path / "runtime" / f"mangaflow-desktop-{TOKEN}"
    runtime.mkdir(parents=True)
    return runtime / "owner.json"


def _record(state: str) -> dict:
    return {"version": 1, "token": TOKEN, "role": "app", "state": state}


def _write_state(journal: Path, state: str) -> None:
    journal.write_text(json.dumps(_record(state)), encoding="utf-8")


def test_terminal_stopped_journal_rejects_a_late_ready(tmp_path: Path, monkeypatch):
    """(a) The core race of #602: the shell already marked the session
    stopped; the helper's late ``ready`` rename must NOT revive it. The file
    stays byte-identical, no exception escapes, and the skip is logged naming
    both states and the skipped target."""
    helper = _load_helper()
    journal = _journal_path(tmp_path)
    _write_state(journal, "stopped")
    before = journal.read_bytes()

    import io

    captured = io.StringIO()
    monkeypatch.setattr(helper, "_log", lambda message: captured.write(message + "\n"))
    helper._write_journal(journal, _record("ready"))  # must not raise

    assert journal.read_bytes() == before, "a terminal journal must stay byte-identical"
    logged = captured.getvalue()
    assert "stopped" in logged and "ready" in logged, logged
    assert str(journal) in logged, f"the skip line must name the skipped target: {logged}"
    # The skipped publication must not leave its staging file behind.
    assert not (journal.parent / "owner.json.helper.pending").exists()


def test_terminal_failed_journal_rejects_a_late_ready(tmp_path: Path):
    """(b) ``failed`` is terminal for the same reason (the sweep reclaims
    stopped/failed past the grace window; reviving to ready leaks)."""
    helper = _load_helper()
    journal = _journal_path(tmp_path)
    _write_state(journal, "failed")
    before = journal.read_bytes()

    helper._write_journal(journal, _record("ready"))  # must not raise

    assert journal.read_bytes() == before


def test_terminal_journal_accepts_a_terminal_record(tmp_path: Path):
    """(c) terminal→terminal publishes: a late helper ``failed`` may land on
    a ``stopped`` journal — allowed and useful forensics."""
    helper = _load_helper()
    journal = _journal_path(tmp_path)
    _write_state(journal, "stopped")

    failure = _record("failed")
    failure["error"] = "alembic:OperationalError"
    helper._write_journal(journal, failure)

    published = json.loads(journal.read_text(encoding="utf-8"))
    assert published["state"] == "failed"
    assert published["error"] == "alembic:OperationalError"


def test_missing_journal_publishes(tmp_path: Path):
    """(d) No current journal → nothing to compare against → publish (the
    first helper write on a fresh session must not be blocked)."""
    helper = _load_helper()
    journal = _journal_path(tmp_path)
    assert not journal.exists()

    helper._write_journal(journal, _record("ready"))

    published = json.loads(journal.read_text(encoding="utf-8"))
    assert published["state"] == "ready"


def test_unparsable_current_journal_fails_open(tmp_path: Path):
    """(e) A current journal that cannot parse (or parses to a non-object)
    must not block publication: fail-open, mirroring the shell-side read's
    treatment of unreadable journals."""
    helper = _load_helper()
    journal = _journal_path(tmp_path)

    journal.write_text('{"state": "rea', encoding="utf-8")
    helper._write_journal(journal, _record("ready"))
    assert json.loads(journal.read_text(encoding="utf-8"))["state"] == "ready"

    # A parsable non-object root (42) is just as unparsable as a state
    # source: absent state → fail open.
    journal.write_text("42", encoding="utf-8")
    helper._write_journal(journal, _record("ready"))
    assert json.loads(journal.read_text(encoding="utf-8"))["state"] == "ready"

    # A non-STRING state (unhashable values included) is not terminal and
    # must fail open too — the membership test may not turn into a
    # TypeError on hostile/corrupt-but-parsable journals.
    journal.write_text('{"state": ["stopped"]}', encoding="utf-8")
    helper._write_journal(journal, _record("ready"))
    assert json.loads(journal.read_text(encoding="utf-8"))["state"] == "ready"


def test_helper_stages_under_its_own_pending_name(tmp_path: Path, monkeypatch):
    """Per-writer staging pin (#602): the helper must stage to
    ``owner.json.helper.pending`` — never the legacy shared
    ``owner.json.pending`` the shell's ``mark_stopped`` also staged to — and
    a successful write leaves no pending sibling behind."""
    helper = _load_helper()
    journal = _journal_path(tmp_path)
    _write_state(journal, "created")

    staged: list[str] = []
    real_replace = os.replace

    def capture_replace(src, dst):
        staged.append(Path(src).name)
        return real_replace(src, dst)

    monkeypatch.setattr(helper.os, "replace", capture_replace)
    helper._write_journal(journal, _record("ready"))

    assert staged == ["owner.json.helper.pending"], (
        f"the helper must stage under its own pending name: {staged}"
    )
    assert not (journal.parent / "owner.json.helper.pending").exists()
    assert not (journal.parent / "owner.json.pending").exists(), (
        "the legacy shared pending name must stay unused (#602)"
    )
    assert json.loads(journal.read_text(encoding="utf-8"))["state"] == "ready"


def test_skip_cleanup_survives_an_unlink_failure(tmp_path: Path, monkeypatch):
    """The skipped-publication cleanup is best-effort: an unlink failure must
    not turn a deliberate skip into an exception (the skip decision is the
    contract; a lingering staging file is inert clutter the runtime sweep
    reclaims with the directory)."""
    helper = _load_helper()
    journal = _journal_path(tmp_path)
    _write_state(journal, "stopped")
    before = journal.read_bytes()

    def broken_unlink(path):
        raise OSError("device detached")

    monkeypatch.setattr(helper.Path, "unlink", broken_unlink)
    helper._write_journal(journal, _record("ready"))  # must not raise

    assert journal.read_bytes() == before
