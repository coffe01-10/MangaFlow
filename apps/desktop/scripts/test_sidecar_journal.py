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
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
HELPER = REPO_ROOT / "apps/desktop/sidecar/mangaflow_desktop_helper.py"

# The FIFO-refusal pins plant real named pipes (os.mkfifo is Unix-only);
# on a Windows-hosted venv they would error at fixture setup and take the
# whole suite down — skip the suite there instead of failing it (#573's
# runner explicitly supports that platform).
pytestmark = pytest.mark.skipif(
    not hasattr(os, "mkfifo"),
    reason="os.mkfifo (named pipes) is Unix-only; the FIFO-refusal pins "
    "need it",
)

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


def test_helper_refuses_a_symlink_at_its_own_pending_name(tmp_path: Path):
    """Helper-side symlink-refusal pin (#602): a link planted at the
    helper's OWN staging name ``owner.json.helper.pending`` must be refused
    BEFORE any write — ``write_text`` would follow the link and clobber its
    target, so a guard that is missing, reordered, or pointed back at the
    legacy shared ``.pending`` name lets the record leak through the link.
    The refusal must leave both the journal bytes and the link target
    untouched."""
    helper = _load_helper()
    journal = _journal_path(tmp_path)
    _write_state(journal, "created")
    before = journal.read_bytes()

    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    pending = journal.with_name(journal.name + ".helper.pending")
    pending.symlink_to(outside)

    with pytest.raises(RuntimeError, match="must not be a link"):
        helper._write_journal(journal, _record("ready"))

    assert outside.read_text(encoding="utf-8") == "{}", (
        "the .helper.pending link target must not be clobbered"
    )
    assert journal.read_bytes() == before, (
        "the refused write must not touch the journal either"
    )
    assert pending.is_symlink(), "the refusal must not remove the planted link"


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


def test_non_regular_journal_is_refused_not_hung(tmp_path: Path):
    """#685: the Rust read_journal_bounded refuses non-regular journals
    before opening; the helper's terminal check read the journal with no
    such guard, so a planted FIFO blocked read_text() forever. A FIFO at
    the journal path must fail fast (RuntimeError naming the file) instead
    of hanging, and must not clobber the FIFO with the pending payload."""

    runtime = tmp_path / f"runtime/mangaflow-desktop-{TOKEN}"
    runtime.mkdir(parents=True)
    journal = runtime / "owner.json"
    os.mkfifo(journal)
    record = _record("created")
    with pytest.raises(RuntimeError, match="regular file"):
        _load_helper()._write_journal(journal, record)
    # The FIFO survives untouched (a blocking-open never happened; the
    # pending staging is the only place bytes were written, and it must be
    # gone once the refusal unwinds).
    import stat

    assert stat.S_ISFIFO(journal.stat().st_mode)
    assert not journal.with_name(journal.name + ".helper.pending").exists()


def test_write_journal_refuses_a_fifo_at_the_pending_sibling(tmp_path):
    """#685 parity for the staged name: a FIFO planted at the
    `.helper.pending` sibling would block pending.write_text() forever
    (open for writing with no reader never returns) — the same
    same-user planting posture the journal itself refuses. The write
    must refuse loudly, leave the FIFO byte-for-byte intact, and not
    create the journal."""

    helper = _load_helper()
    journal = _journal_path(tmp_path)
    pending = journal.with_name("owner.json.helper.pending")
    os.mkfifo(pending)
    with pytest.raises(RuntimeError, match="regular file"):
        helper._write_journal(journal, _record("ready"))
    assert pending.exists(), "the planted FIFO must be left untouched"
    assert not journal.exists(), "a refused write must not create the journal"


def test_last_resort_merge_preserves_the_annotated_phase_error():
    """#696: the alembic leg journals ``alembic:<Type>`` and re-raises; the
    last-resort merge must keep that annotation in the terminal record
    (first annotated error wins as root cause), not clobber it with the
    bare exception class name."""

    helper = _load_helper()
    record = {"version": 1, "token": TOKEN, "state": "ready",
              "error": "alembic:OperationalError"}
    helper._merge_last_resort_failure(record, RuntimeError("late"))
    assert record["state"] == "failed"
    assert record["error"] == "alembic:OperationalError", record

    unannotated = {"version": 1, "token": TOKEN, "state": "created"}
    helper._merge_last_resort_failure(unannotated, RuntimeError("boom"))
    assert unannotated["state"] == "failed"
    assert unannotated["error"] == "RuntimeError", unannotated


def test_last_resort_journal_guard_failure_does_not_escape_main(
    monkeypatch, tmp_path: Path
):
    """Round-8 review: the #686/#692 journal guards raise RuntimeError (a
    planted link/FIFO at the journal or pending names), but the
    last-resort handler caught only OSError — on an already-failing run a
    guard refusal escaped main() as a raw traceback instead of the
    designed one-line "helper failed" exit. The handler must survive its
    own guards."""

    helper = _load_helper()
    journal = _journal_path(tmp_path)
    monkeypatch.setattr(sys, "argv", [str(HELPER), "stub"])

    def _refuse(*args, **kwargs):
        raise RuntimeError("process journal must be a regular file")

    def _fail(*args, **kwargs):
        raise RuntimeError("the real failure")

    monkeypatch.setattr(helper, "_read_context", lambda: (TOKEN, journal))
    monkeypatch.setattr(helper, "_run_stub", _fail)
    monkeypatch.setattr(helper, "_write_journal", _refuse)
    logged: list[str] = []
    monkeypatch.setattr(helper, "_log", lambda message: logged.append(message))

    assert helper.main() == 1, "last-resort must exit 1, not raise a traceback"
    assert any("the real failure" in line for line in logged), logged


def test_go_refusal_journals_failed_and_exits_75(tmp_path):
    """End-to-end: a handshake refused on a wrong GO token must leave the
    journal in a TERMINAL state — the stale-runtime sweep reclaims only
    stopped/failed, so a journal left at ready leaked the runtime
    directory forever. The stub mode is the fast, dependency-free flow
    for both refusal sites (the app mode shares the same pattern)."""

    import json as json_module
    import subprocess

    token = "0123456789abcdef0123456789abcdef"
    runtime = tmp_path / "runtime" / f"mangaflow-desktop-{token}"
    runtime.mkdir(parents=True)
    journal = runtime / "owner.json"

    proc = subprocess.Popen(
        [
            sys.executable,
            str(HELPER),
            "stub",
            "--grandchild",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env={
            **os.environ,
            "MANGAFLOW_DESKTOP_TOKEN": token,
            "MANGAFLOW_DESKTOP_JOURNAL": str(journal),
            "MANGAFLOW_DISABLE_DOTENV": "1",
        },
    )
    try:
        ready_line = proc.stdout.readline()
        assert ready_line.startswith("MANGAFLOW_READY "), ready_line
        # The wrong token: the helper must refuse, journal failed, exit 75.
        proc.stdin.write(f"MANGAFLOW_GO {'f' * 32}\n")
        proc.stdin.flush()
        assert proc.wait(timeout=30) == 75, "EXIT_HANDSHAKE_REFUSED is 75"
    finally:
        if proc.poll() is None:
            proc.kill()
    record = json_module.loads(journal.read_text(encoding="utf-8"))
    assert record["state"] == "failed", record
    assert record["error"] == "go-refused", record


def test_oversize_journal_fail_open_and_publish(tmp_path: Path):
    """#732: the terminal check's read is bounded (64 KiB + 1, the Rust
    read_journal_bounded parity) — a planted oversize journal truncates,
    parses as broken, and fails OPEN to a normal publish instead of
    buffering unbounded bytes into the helper."""

    journal = _journal_path(tmp_path)
    with journal.open("wb") as handle:
        handle.write(b'{"version":1,"token":"' + TOKEN.encode())
        handle.seek(64 * 1024 + 8)
        handle.write(b"\0")
    helper = _load_helper()
    helper._write_journal(journal, _record("ready"))
    published = json.loads(journal.read_text(encoding="utf-8"))
    assert published["state"] == "ready", published


@pytest.mark.skipif(
    not hasattr(os, "symlink"),
    reason="os.symlink (unprivileged) is Unix-only; the link-swap pin needs it",
)
def test_open_verified_pending_refuses_a_link_redirected_outside(tmp_path: Path):
    """#863 (Rust twin parity): a pending name that resolves OUTSIDE the
    runtime dir must refuse at the post-open verifier — and the refusal must
    be zero-damage: the outside target's sentinel bytes survive (the old
    truncating "wb" open zeroed them at open time)."""

    helper = _load_helper()
    runtime = tmp_path / "runtime" / f"mangaflow-desktop-{TOKEN}"
    runtime.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "victim.log"
    victim.write_bytes(b"SENTINEL-BYTES")
    pending = runtime / "owner.json.helper.pending"
    os.symlink(victim, pending)

    with pytest.raises(RuntimeError, match="outside the runtime directory"):
        helper._open_verified_pending(pending, runtime)
    assert victim.read_bytes() == b"SENTINEL-BYTES", (
        "the refusal must be zero-damage to the redirected target (#863)"
    )


def test_open_verified_pending_passes_a_regular_in_runtime_pending(tmp_path: Path):
    """#863 green path: a regular pending inside the runtime dir gets a
    verified, writable fd — and the guarded _write_journal still publishes."""

    helper = _load_helper()
    runtime = tmp_path / "runtime" / f"mangaflow-desktop-{TOKEN}"
    runtime.mkdir(parents=True)
    pending = runtime / "owner.json.helper.pending"
    fd = helper._open_verified_pending(pending, runtime)
    try:
        os.write(fd, b"probe")
    finally:
        os.close(fd)
    assert pending.read_bytes() == b"probe"
    journal = runtime / "owner.json"
    helper._write_journal(journal, _record("ready"))
    published = json.loads(journal.read_text(encoding="utf-8"))
    assert published["state"] == "ready", published


def test_write_journal_fsyncs_payload_and_parent_dir(tmp_path, monkeypatch):
    """Durability parity with the Rust twin (#899): the payload fsync
    (#824) commits the bytes, and the post-rename parent-dir fsync
    commits the directory entry — a power loss between them reverts the
    journal to missing/prior (which the sweep does not reclaim). Pin
    that a normal publish performs BOTH fsyncs."""

    import os as os_module

    real_fsync = os_module.fsync
    fsynced_fds: list[int] = []
    monkeypatch.setattr(
        os_module, "fsync", lambda fd: (fsynced_fds.append(fd), real_fsync(fd))
    )

    helper = _load_helper()
    journal = _journal_path(tmp_path)
    helper._write_journal(journal, _record("ready"))

    # The pending staging write fsyncs once (the payload); the directory
    # entry fsync after os.replace is the durability tail under parity.
    assert len(fsynced_fds) >= 2, (
        f"expected the payload fsync AND the parent-dir durability tail, "
        f"got {len(fsynced_fds)} fsync call(s)"
    )
    assert journal.read_text(encoding="utf-8") != ""
