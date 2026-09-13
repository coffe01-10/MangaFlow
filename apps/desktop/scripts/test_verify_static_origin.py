"""Contract tests for the D5 verify-static-origin script's teardown kill (#588).

The script spawns the sidecar helper and must never let it outlive the run,
yet every teardown path (fail(), the 20s readiness timeout, the 1 MiB stdout
bound, the final grace escalation, and main().catch) used to call
``process.kill(-helper.pid, "SIGKILL")`` directly. That is POSIX
process-group semantics: on Windows libuv rejects pid<=0 with EINVAL without
attempting anything, each call site's try/catch swallowed the throw as
"already gone", and the helper stayed alive holding its loopback port —
exactly the orphan class the kills were added to prevent.

These tests pin the structural fix: ONE ``killHelperTree`` choke-point holds
the file's only ``process.kill``, carries a win32 ``taskkill /PID <pid> /T
/F`` tree-kill branch, and separates "already gone" (ESRCH / taskkill exit
128, silent) from real failure (one-line stderr diagnostic naming the pid).
Static source assertions mirror how test_guard_frontend_dist.py pins script
structure: the script drives a real helper + browser run and cannot be
imported safely from a unit test.
"""

from __future__ import annotations

from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
SCRIPT = _SCRIPTS / "verify-static-origin.mjs"


def _source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def _span(source: str, start_marker: str, end_marker: str) -> str:
    """The source slice from start_marker to the first end_marker after it."""
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    return source[start:end]


def _kill_helper_body(source: str) -> str:
    """killHelperTree's body: module top level, so it ends at the first
    closing brace at column zero after the definition (inner braces are
    indented)."""
    return _span(source, "function killHelperTree(", "\n}\n")


def _code_text(source: str) -> str:
    """Comment-stripped source for occurrence counting: whole-line ``//``
    comments are dropped and trailing `` //`` comments truncated. The
    script's strings never contain `` //`` (URLs appear as ``http://``
    with no preceding space), so this does not cut live code."""
    lines = []
    for line in source.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("//"):
            continue
        lines.append(line.split(" //", 1)[0])
    return "\n".join(lines)


def test_the_only_direct_process_kill_lives_inside_kill_helper_tree():
    """No teardown path may kill the helper on its own: every
    ``process.kill`` in the file must sit inside killHelperTree, so the
    Windows/POSIX split and the ESRCH-vs-failure reporting exist exactly
    once (a second direct kill reintroduces the #588 orphan silently)."""
    source = _source()
    code = _code_text(source)
    assert code.count("process.kill") == 1, (
        "process.kill must appear exactly once in code (inside killHelperTree); "
        "direct kills elsewhere reintroduce the #588 Windows orphan"
    )
    body = _kill_helper_body(source)
    assert "process.kill" in body, "the single process.kill left killHelperTree"


def test_every_teardown_path_routes_through_kill_helper_tree():
    """fail(), the readiness timeout, the 1 MiB stdout bound, the final
    grace escalation, and main().catch must all call killHelperTree (and
    none may kill the helper directly)."""
    source = _source()
    fail_body = _span(source, "function fail(message) {", "process.exitCode = 1;")
    timeout_body = _span(
        source, "const timer = setTimeout(() => {", 'reject(new Error("helper readiness timeout"))'
    )
    stdout_body = _span(
        source,
        "if (buffer.length > 1048576) {",
        'reject(new Error("helper stdout exceeded 1 MiB before READY"))',
    )
    grace_body = _span(source, "const killGrace = setTimeout(() => {", "}, 15000);")
    catch_body = source[source.index("main().catch(") :]

    for name, span in [
        ("fail()", fail_body),
        ("readiness timeout", timeout_body),
        ("stdout bound", stdout_body),
        ("grace escalation", grace_body),
        ("main().catch", catch_body),
    ]:
        assert "killHelperTree(" in span, f"teardown path {name} bypasses killHelperTree"
        assert "process.kill" not in span, (
            f"teardown path {name} kills the helper directly (#588 regression)"
        )


def test_windows_branch_tree_kills_via_taskkill():
    """The win32 branch must tree-kill by pid with taskkill (/T /F) via
    spawnSync — libuv cannot take the negative-pid group kill, and the
    helper is a plain spawn() with no Job Object to close (#588)."""
    body = _kill_helper_body(_source())
    assert 'process.platform === "win32"' in body
    assert "spawnSync" in body
    assert "taskkill" in body
    for flag in ("/PID", "/T", "/F"):
        assert flag in body, f"taskkill {flag} missing from the win32 branch"


def test_taskkill_is_spawned_only_inside_the_kill_helper():
    """The taskkill invocation (an external process) may not leak into other
    code paths (the import line is exempt — pinned by its own test)."""
    source = _source()
    code = "\n".join(
        line
        for line in _code_text(source).splitlines()
        if not line.lstrip().startswith("import ")
    )
    body_code = _code_text(_kill_helper_body(source))
    assert code.count("taskkill") == body_code.count("taskkill")
    assert code.count("spawnSync") == body_code.count("spawnSync")


def test_spawn_sync_is_imported_from_node_child_process():
    import_span = _span(_source(), "import { spawn", 'from "node:child_process";')
    assert "spawnSync" in import_span, "killHelperTree's spawnSync must be imported"


def test_posix_branch_keeps_process_group_kill_semantics():
    """POSIX keeps the negative-pid (process group) kill — the helper is
    setsid'd, so -pid reaches it and anything it spawned."""
    body = _kill_helper_body(_source())
    assert "process.kill(-child.pid, signal)" in body


def test_kill_failure_outcomes_are_distinguished_from_already_gone():
    """ESRCH (and taskkill exit 128, "process not found") after a real
    attempt is already-gone and stays silent; every OTHER failure gets a
    one-line stderr diagnostic that NAMES THE PID, so the log stops
    misreporting a kill that never happened as "already gone"."""
    body = _kill_helper_body(_source())
    lines = body.splitlines()

    esrch_lines = [line for line in lines if "ESRCH" in line]
    assert esrch_lines, "the POSIX branch must special-case ESRCH"
    assert all("console.error" not in line for line in esrch_lines), (
        "ESRCH after a real attempt is 'already gone' and must stay silent"
    )
    # taskkill exit 128 is the Windows "process not found" already-gone case:
    # its guard must exclude 128 from the failure diagnostic.
    guards = [
        line
        for line in _code_text(body).splitlines()
        if "128" in line and "status" in line
    ]
    assert guards, "taskkill's 128 exit must be handled as already-gone"
    assert any("!== 0" in line and "!== 128" in line for line in guards), (
        "the failure diagnostic must fire for non-zero status EXCEPT 128"
    )

    diagnostics = [line for line in lines if "console.error" in line]
    assert diagnostics, "kill failure diagnostics must exist"
    for line in diagnostics:
        assert "${child.pid}" in line, (
            f"failure diagnostic must name the pid: {line.strip()}"
        )
