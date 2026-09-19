"""Text-level structural pins for shell-core call sites no test executes.

Two mutation-verified coverage gaps (#968, #942) sit in failure/retry arms
whose realistic construction needs an injection seam or a racing external
writer — both lead-owned designs per AGENTS.md (journal/process ownership).
Until those seams exist, these pins hold the STRUCTURE the arms must keep
(the c106c43 precedent: a dropped or reordered guard is text-detectable even
when the arm itself stays unexecuted). They run everywhere the default pytest
gate runs, including the Windows job where the cargo bridge is POSIX-gated.
"""

from __future__ import annotations

from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "apps" / "desktop" / "shell-core" / "src"


def _function_body(source: str, signature: str) -> str:
    """The source from ``signature`` to the next top-level ``fn ``/``}`` at
    column zero — the same split the in-file protocol.rs pins use."""
    start = source.index(signature)
    tail = source[start + len(signature) :]
    for marker in ("\nfn ", "\npub fn ", "\nimpl ", "\n}\n"):
        cut = tail.find(marker)
        if cut != -1:
            tail = tail[: cut + (len(marker) if marker == "\n}\n" else 0)]
    return source[start: start + len(signature) + len(tail)]


def test_stderr_open_failure_records_stopped_before_returning():
    """#968: the helper-stderr open's failure arm must record the run's
    terminal state BEFORE the ``SpawnError::Io`` return. The bare ``?`` this
    arm replaced returned without any record even though ``RunLog::create``
    had already succeeded — the journal stayed non-terminal ("created")
    forever and the sweep never reclaims non-terminal journals."""
    source = (_SRC / "handshake.rs").read_text(encoding="utf-8")
    body = _function_body(source, "pub fn spawn_helper(")

    arm_start = body.index("let helper_stderr = match open_append_regular(")
    # The stderr match spans to the Command construction that follows it.
    match_src = body[arm_start : body.index("let mut command = Command::new", arm_start)]
    err_block = match_src[match_src.index("Err(error) => {") :]
    record_at = err_block.find("record_stopped(&run_log, &layout, None);")
    return_at = err_block.find("return Err(SpawnError::Io(error));")
    assert record_at != -1, (
        "the stderr-open failure arm must record_stopped before returning "
        "(#968: a bare `?` leaves the journal non-terminal forever)"
    )
    assert return_at != -1, "the arm must still return SpawnError::Io"
    assert record_at < return_at, (
        "record_stopped must precede the return (a reordered return drops "
        "the record the sweep needs)"
    )
    # The same record-before-return contract holds for the canonicalize arm
    # directly above it (the pre-spawn logs-root resolution).
    canon_src = body[
        body.index("let canonical_logs_root = match logs_dir(user_data).canonicalize()")
        : arm_start
    ]
    canon_err = canon_src[canon_src.index("Err(error) => {") :]
    assert (
        canon_err.find("record_stopped(&run_log, &layout, None);")
        < canon_err.find("return Err(SpawnError::Io(error));")
    ), "the canonicalize failure arm must also record before returning"


def test_mark_stopped_merge_retry_republishes_and_propagates():
    """#942: mark_stopped's post-write verify re-read branch must RE-PUBLISH
    the merged record and PROPAGATE the retry's error. The mutation
    ``let _ = write_journal_atomic(...)`` (dropping the re-publish AND its
    error) survived the whole suite; a dropped branch or a swallowed error
    re-opens the revived-journal leak (#602) silently."""
    source = (_SRC / "protocol.rs").read_text(encoding="utf-8")
    body = _function_body(source, "pub fn mark_stopped(")

    first_write = body.index("write_journal_atomic(&journal, &value, &self.user_data)?;")
    re_read = body.index("if let Some(text) = read_journal_bounded(&journal)")
    merge = body.index("if let Some(merged) = merge_stop_onto_current(")
    retry = body.index(
        "return write_journal_atomic(&journal, &merged, &self.user_data);"
    )
    assert first_write < re_read < merge < retry, (
        "the retry must live inside the post-write re-read branch, after the "
        "first atomic write"
    )
    assert "let _ = write_journal_atomic(&journal, &merged" not in body, (
        "the retry's error may not be swallowed (#942 mutation must fail)"
    )
