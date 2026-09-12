"""Regression tests for the helper's startup environment ownership (#313, #314).

Two red-team findings from 2026-09-09 are pinned here:

- MANGAFLOW_DISABLE_DOTENV used setdefault, so an inherited value of ``0``
  re-enabled .env loading relative to the helper's CWD on every start;
- --api-root was inserted at sys.path[0] unvalidated, letting a wrong or
  hostile tree shadow the helper's own modules (fake_channel) and hijack
  alembic's env.py before the API ever imported.

The tests above pin the validation functions themselves; the subprocess
test at the bottom pins the WIRING (#343): the real helper, started exactly
the way the shell starts it, must fail closed (exit 1 + failed journal +
no READY line) with a bad --api-root before anything from that tree can be
imported.

    python3 -m pytest apps/desktop/scripts/test_sidecar_env_and_api_root.py -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sidecar"))

import mangaflow_desktop_helper as helper  # noqa: E402

HELPER_PATH = Path(helper.__file__).resolve()
HOSTILE_IMPORT_MARKER = "HOSTILE_FAKE_CHANNEL_IMPORTED"


def _plant_hostile_fake_channel(root: Path) -> None:
    """A fake_channel.py that records its own import.

    If the fail-closed-before-import wiring ever regresses (validation
    deleted, or sys.path.insert moved ahead of it), this module-level side
    effect runs and leaves the marker behind — the assertion on its absence
    is what makes the ordering claim observable instead of trusted.
    """

    (root / "fake_channel.py").write_text(
        "from pathlib import Path\n"
        f"Path(__file__).with_name({HOSTILE_IMPORT_MARKER!r}).touch()\n"
        "raise SystemExit('hostile fake_channel was imported')\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    "reason,plant",
    [
        (
            "api-root/shadowing-fake-channel",
            lambda root: (
                (root / "alembic.ini").write_text("", encoding="utf-8"),
                (root / "app").mkdir(),
                (root / "app" / "main.py").write_text("", encoding="utf-8"),
                _plant_hostile_fake_channel(root),
            ),
        ),
        ("api-root/missing-alembic-ini", lambda root: None),
    ],
)
def test_bad_api_root_fails_closed_in_subprocess_before_import(
    tmp_path: Path, reason: str, plant
):
    """Behavioral pin of _run_app's prologue order (#343 part 2).

    The pure-function tests above stay green even if the CALLS in _run_app
    were deleted or reordered (validation :497-504 vs sys.path.insert :505),
    so this test runs the real helper as a subprocess with a planted bad
    --api-root and asserts the observable fail-closed contract: exit 1, a
    journal record of state=failed with the api-root/ error, no READY line
    on stdout, and no trace of the hostile tree's modules having been
    imported.
    """

    plant(tmp_path)
    token = os.urandom(16).hex()
    runtime = tmp_path / "runtime" / f"mangaflow-desktop-{token}"
    runtime.mkdir(parents=True)
    journal = runtime / "owner.json"
    result = subprocess.run(
        [
            sys.executable,
            str(HELPER_PATH),
            "app",
            "--api-root",
            str(tmp_path),
            "--user-data",
            str(tmp_path / "user-data"),
            # The hostile tree only gets a chance to shadow fake_channel
            # when the channel install path is requested at all.
            "--fake-channel",
        ],
        env=dict(
            os.environ,
            MANGAFLOW_DESKTOP_TOKEN=token,
            MANGAFLOW_DESKTOP_JOURNAL=str(journal),
            MANGAFLOW_DISABLE_DOTENV="1",
        ),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 1, (
        f"helper must fail closed on a bad --api-root; stderr:\n{result.stderr}"
    )
    assert "MANGAFLOW_READY" not in result.stdout, (
        "a rejected api-root must never publish readiness"
    )
    assert "api root rejected" in result.stderr, result.stderr

    record = json.loads(journal.read_text(encoding="utf-8"))
    assert record["state"] == "failed", record
    assert record["error"] == reason, record
    assert record["token"] == token, record

    # The fail-closed-BEFORE-import half of the contract: nothing from the
    # hostile tree was ever loaded (the marker module did not run).
    assert not (tmp_path / HOSTILE_IMPORT_MARKER).exists(), (
        "the hostile api-root tree was imported before/instead of rejection"
    )


def test_disable_dotenv_is_forced_over_an_inherited_zero(monkeypatch, tmp_path):
    monkeypatch.setenv("MANGAFLOW_DISABLE_DOTENV", "0")
    # Pre-register every key _apply_app_environment force-sets: without
    # this, the four globals leak into the same pytest process after the
    # test (a stale tmp_path DATABASE_URL would poison later DB-dependent
    # tests).
    for key in ("DATABASE_URL", "STORAGE_ROOT", "UPLOAD_ROOT", "WEB_ORIGIN"):
        monkeypatch.setenv(key, f"pre-existing-{key}")
    helper._apply_app_environment(tmp_path, "http://tauri.localhost")
    # The shell owns this environment; setdefault would have kept the
    # inherited "0" and re-enabled .env loading from the helper's CWD.
    assert helper.os.environ["MANGAFLOW_DISABLE_DOTENV"] == "1"
    assert helper.os.environ["DATABASE_URL"] == f"sqlite:///{tmp_path / 'data' / 'mangaflow.db'}"
    assert helper.os.environ["STORAGE_ROOT"] == str(tmp_path / "storage")
    assert helper.os.environ["UPLOAD_ROOT"] == str(tmp_path / "uploads")
    assert helper.os.environ["WEB_ORIGIN"] == "http://tauri.localhost"


def test_traverse_only_api_root_still_rejects_the_shadow(monkeypatch, tmp_path):
    """A traverse-only (0o111) api_root passes the marker `is_file()` checks
    but cannot be listed - the fail-closed fallback must keep the byte-exact
    probe (stat works through +x) instead of accepting. Pins the #359
    round-1 review F1 fix's fallback branch. POSIX-only: the 0o111 shape
    needs POSIX permission semantics (skip on Windows, where the fallback
    is unreachable via the ACL model)."""
    if os.name == "nt":
        pytest.skip("0o111 traverse-only shape needs POSIX permission semantics")
    # One planted shadow per probe name in the R3-extended fallback
    # (fake_channel.py / fake_channel / alembic.py / alembic / uvicorn.py /
    # uvicorn): a traverse-only root must fail closed on ANY of them - and
    # a clean traverse-only root must pass (no false positive from the
    # fallback's byte-exact probes).
    probes = [
        "fake_channel.py", "fake_channel",
        "alembic.py", "alembic",
        "uvicorn.py", "uvicorn",
    ]
    for shadow in probes:
        root = tmp_path / f"root-{shadow.replace('.', '-')}"
        root.mkdir(parents=True)
        (root / "alembic.ini").write_text("[alembic]\n", encoding="utf-8")
        (root / "app").mkdir()
        (root / "app" / "main.py").write_text("", encoding="utf-8")
        (root / shadow).write_text("", encoding="utf-8")
        root.chmod(0o111)
        try:
            reason = helper._validate_api_root(root)
        finally:
            root.chmod(0o755)
        assert reason is not None and reason.startswith("api-root/shadowing-"), (
            f"traverse-only root with {shadow!r} must be rejected: {reason!r}"
        )
        clean = tmp_path / f"clean-{shadow.replace('.', '-')}"
        clean.mkdir(parents=True)
        (clean / "alembic.ini").write_text("[alembic]\n", encoding="utf-8")
        (clean / "app").mkdir()
        (clean / "app" / "main.py").write_text("", encoding="utf-8")
        clean.chmod(0o111)
        try:
            assert helper._validate_api_root(clean) is None, (
                f"clean traverse-only root with no {shadow!r} must pass"
            )
        finally:
            clean.chmod(0o755)


def test_read_context_drops_handshake_secrets_from_the_environment(monkeypatch, tmp_path):
    """After validation, the token and journal path must be gone from the
    helper's own os.environ: the long-lived server (and every subprocess it
    spawns later — the CLI channel's children inherit os.environ) must not
    carry the ownership secrets for its whole lifetime. The validated local
    variables are the single source from here on."""
    token = "b" * 32
    runtime = tmp_path / f"mangaflow-desktop-{token}"
    runtime.mkdir(parents=True)
    journal = runtime / "owner.json"
    monkeypatch.setenv("MANGAFLOW_DESKTOP_TOKEN", token)
    monkeypatch.setenv("MANGAFLOW_DESKTOP_JOURNAL", str(journal))

    returned_token, returned_journal = helper._read_context()

    assert returned_token == token
    assert returned_journal == journal
    assert os.environ.get("MANGAFLOW_DESKTOP_TOKEN") is None
    assert os.environ.get("MANGAFLOW_DESKTOP_JOURNAL") is None


def test_valid_api_root_tree_passes_validation(tmp_path):
    (tmp_path / "alembic.ini").write_text("[alembic]\n", encoding="utf-8")
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text("", encoding="utf-8")
    assert helper._validate_api_root(tmp_path) is None


@pytest.mark.parametrize(
    "reason,plant",
    [
        ("api-root/missing-alembic-ini", lambda root: None),
        ("api-root/missing-app-main", lambda root: (root / "alembic.ini").write_text("", encoding="utf-8")),
        (
            "api-root/shadowing-fake-channel",
            lambda root: (
                (root / "alembic.ini").write_text("", encoding="utf-8"),
                (root / "app").mkdir(),
                (root / "app" / "main.py").write_text("", encoding="utf-8"),
                (root / "fake_channel.py").write_text("", encoding="utf-8"),
            ),
        ),
        (
            "api-root/shadowing-fake-channel",
            # Case variant: Windows resolves imports case-insensitively
            # (NTFS), so `Fake_Channel.py` shadows the helper's module there
            # even though a byte-exact check passes. The scan lowercases.
            lambda root: (
                (root / "alembic.ini").write_text("", encoding="utf-8"),
                (root / "app").mkdir(),
                (root / "app" / "main.py").write_text("", encoding="utf-8"),
                (root / "Fake_Channel.py").write_text("", encoding="utf-8"),
            ),
        ),
        (
            "api-root/shadowing-fake-channel",
            # Directory form: Python 3 imports namespace packages without
            # __init__.py, so a bare `fake_channel/` directory shadows the
            # helper's module exactly like the file form.
            lambda root: (
                (root / "alembic.ini").write_text("", encoding="utf-8"),
                (root / "app").mkdir(),
                (root / "app" / "main.py").write_text("", encoding="utf-8"),
                (root / "fake_channel").mkdir(),
            ),
        ),
        (
            "api-root/shadowing-alembic.py",
            # #314: a root-level alembic module shadows the venv's real
            # `from alembic import command` — the migration driver itself.
            lambda root: (
                (root / "alembic.ini").write_text("", encoding="utf-8"),
                (root / "app").mkdir(),
                (root / "app" / "main.py").write_text("", encoding="utf-8"),
                (root / "alembic.py").write_text("raise SystemExit('PWNED')", encoding="utf-8"),
            ),
        ),
        (
            "api-root/shadowing-uvicorn",
            # #314: same shape for the server import the helper performs.
            lambda root: (
                (root / "alembic.ini").write_text("", encoding="utf-8"),
                (root / "app").mkdir(),
                (root / "app" / "main.py").write_text("", encoding="utf-8"),
                (root / "uvicorn").mkdir(),
            ),
        ),
        (
            "api-root/shadowing-alembic",
            # Directory form (the most common real alembic layout): the
            # namespace-package over-rejection is the deliberate policy.
            lambda root: (
                (root / "alembic.ini").write_text("", encoding="utf-8"),
                (root / "app").mkdir(),
                (root / "app" / "main.py").write_text("", encoding="utf-8"),
                (root / "alembic").mkdir(),
            ),
        ),
        (
            "api-root/shadowing-uvicorn.py",
            # File form for the server package, and a case variant to pin
            # the lowercase scan for the new names.
            lambda root: (
                (root / "alembic.ini").write_text("", encoding="utf-8"),
                (root / "app").mkdir(),
                (root / "app" / "main.py").write_text("", encoding="utf-8"),
                (root / "Uvicorn.py").write_text("", encoding="utf-8"),
            ),
        ),
    ],
)
def test_bad_api_root_trees_are_rejected_before_sys_path(tmp_path, reason, plant):
    plant(tmp_path)
    # A fake_channel.py at the root would sit at sys.path[0] and shadow the
    # helper's own module next to the sidecar package — the exact hijack the
    # red team described.
    assert helper._validate_api_root(tmp_path) == reason


def test_sqlalchemy_url_escapes_percent_interpolation(tmp_path):
    """#443: set_main_option routes through ConfigParser interpolation, so a
    bare % in the user-data path (a '100%' username is a legal name) died
    with an InterpolationSyntaxError before the API ever started. The
    documented escape (doubling) must round-trip to the original URL."""
    from alembic.config import Config as AlembicConfig

    url = "sqlite:///C:/Users/100%/data/mangaflow.db"
    config = AlembicConfig()
    helper.set_sqlalchemy_url(config, url)
    # Reading the option back runs the interpolation: the doubled %% must
    # resolve to the single original %.
    assert config.get_main_option("sqlalchemy.url") == url


def test_helper_registers_sigterm_exit_zero(tmp_path):
    """The helper must install a SIGTERM handler that exits 0: the shell's
    cooperative stop (stdin-EOF watcher raising SIGTERM; Unix shells'
    group-SIGTERM) relies on the graceful ``sys.exit(0)`` unwinding through
    main()'s cleanup. A regression (default SIGTERM disposition) would make
    every cooperative stop a signal death instead of a clean exit 0.

    Drives the REAL helper module (importlib, like the env tests) and
    asserts the registered handler converts SIGTERM to SystemExit(0) —
    pinned by raising SIGTERM in-process and catching the SystemExit."""
    import importlib.util
    import signal as signal_module

    spec = importlib.util.spec_from_file_location(
        "mangaflow_desktop_helper_sigterm", str(HELPER_PATH)
    )
    helper_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper_module)

    # The module-level main() guard means importing never registers the
    # handler; register exactly what main() registers (the same lambda
    # shape: lambda *_: sys.exit(0)).
    helper_module.signal.signal(
        signal_module.SIGTERM, lambda *_: sys.exit(0)
    )
    try:
        with pytest.raises(SystemExit) as excinfo:
            signal_module.raise_signal(signal_module.SIGTERM)
        assert excinfo.value.code == 0
    finally:
        signal_module.signal(signal_module.SIGTERM, signal_module.SIG_DFL)


@pytest.mark.parametrize(
    "bad_token",
    [
        "a" * 32 + "\n",  # shell launcher trailing-newline attack
        "a" * 32 + " ",  # trailing space
        "A" * 32,  # uppercase hex
        "a" * 33,  # too long
        "g" * 32,  # non-hex
        "",  # missing
    ],
    ids=["trailing-newline", "trailing-space", "uppercase", "too-long", "non-hex", "empty"],
)
def test_read_context_rejects_malformed_tokens(monkeypatch, tmp_path, bad_token):
    """Every malformed ownership-token shape must be refused before the
    journal path is trusted: the token is the filename anchor for the
    runtime directory, so a sloppy match (substring/prefix) would let a
    crafted env var point the helper at a foreign runtime directory."""
    monkeypatch.setenv("MANGAFLOW_DESKTOP_TOKEN", bad_token)
    monkeypatch.setenv(
        "MANGAFLOW_DESKTOP_JOURNAL",
        str(tmp_path / f"mangaflow-desktop-{bad_token.strip()}" / "owner.json"),
    )
    with pytest.raises(ValueError, match="invalid process ownership token"):
        helper._read_context()


def test_stdin_eof_watch_drains_post_go_bytes_and_signals(monkeypatch):
    """The stdin-EOF watcher's drain loop must be token-agnostic: any
    post-GO bytes (a double GO from a buggy launcher, stray writes) are
    drained and ignored, and EOF always raises SIGTERM into the process
    (the cooperative stop). A regression to a token-matching drain (e.g.
    only draining lines matching the GO prefix) would hang the cooperative
    stop on a buggy launcher's stray writes."""
    import importlib.util
    import io
    import signal as signal_module
    import time as time_module

    spec = importlib.util.spec_from_file_location(
        "mangaflow_desktop_helper_eof", str(HELPER_PATH)
    )
    helper_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper_module)

    # Deterministic SIGTERM capture instead of the real delivery.
    delivered = []
    monkeypatch.setattr(
        helper_module.signal, "raise_signal",
        lambda sig: delivered.append(sig),
    )

    fake_stdin = io.StringIO("MANGAFLOW_GO wrong-token\n" + "junk\n")
    monkeypatch.setattr(helper_module.sys, "stdin", fake_stdin)

    helper_module._start_stdin_eof_watch()
    deadline = time_module.monotonic() + 5
    while not delivered and time_module.monotonic() < deadline:
        time_module.sleep(0.05)
    assert delivered == [signal_module.SIGTERM], (
        f"EOF after draining junk must raise SIGTERM: {delivered!r}"
    )


def test_read_context_rejects_a_symlinked_journal(monkeypatch, tmp_path):
    """A symlink planted at the journal path must be refused before the
    token is trusted as the runtime anchor: the link could point the
    ownership journal at a foreign runtime directory the shell does not
    own. The absolute check alone would miss a RELATIVE symlink whose
    target is elsewhere."""

    token = "c" * 32
    foreign = tmp_path / "foreign-runtime"
    foreign.mkdir(parents=True)
    target = foreign / "owner.json"
    target.write_text("{}", encoding="utf-8")

    runtime = tmp_path / f"mangaflow-desktop-{token}"
    runtime.mkdir(parents=True)
    journal = runtime / "owner.json"
    journal.symlink_to(target)

    monkeypatch.setenv("MANGAFLOW_DESKTOP_TOKEN", token)
    monkeypatch.setenv("MANGAFLOW_DESKTOP_JOURNAL", str(journal))

    with pytest.raises(ValueError, match="absolute real path"):
        helper._read_context()

def test_write_journal_refuses_links_and_writes_atomically(tmp_path):
    """_write_journal's two guards: (1) a symlink at the journal OR the
    .pending sibling must be refused before any write (the journal is the
    ownership anchor; a link would redirect it); (2) the happy path writes
    via a .pending temp then os.replace — no partial journal can exist."""

    import importlib.util
    import json as json_module

    spec = importlib.util.spec_from_file_location(
        "mangaflow_desktop_helper_wj", str(HELPER_PATH)
    )
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)

    record = {"version": 1, "state": "ready"}
    token = "d" * 32
    runtime = tmp_path / f"mangaflow-desktop-{token}"
    runtime.mkdir(parents=True)
    journal = runtime / "owner.json"

    # (1) A symlink at the journal itself must be refused, target untouched.
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    journal.symlink_to(outside)
    with pytest.raises(RuntimeError, match="must not be a link"):
        helper._write_journal(journal, record)
    assert outside.read_text(encoding="utf-8") == "{}"

    journal.unlink()

    # (2) A symlink at the .pending sibling must also be refused — and
    # because write_text would FOLLOW that link, the outside target must
    # still carry its original bytes (a guard removed or reordered lets
    # the record clobber it through the link).
    pending = journal.with_name(journal.name + ".pending")
    pending.symlink_to(outside)
    with pytest.raises(RuntimeError, match="must not be a link"):
        helper._write_journal(journal, record)
    assert outside.read_text(encoding="utf-8") == "{}", (
        "the .pending link target must not be clobbered"
    )
    pending.unlink()

    # (3) Happy path: the journal lands with sorted-key JSON and no
    # .pending. Byte-exact: a dropped sort_keys changes the file's shape
    # even though the parsed content matches.
    helper._write_journal(journal, record)
    assert journal.read_text(encoding="utf-8") == json_module.dumps(
        record, sort_keys=True
    ), "the stamp must be the exact sorted-key serialization"
    assert not pending.exists()


def test_pid_starttime_reads_live_anchor_and_is_deterministic():
    """The helper's Linux identity anchor: /proc/self/stat field 22
    (index 19 after the comm close-paren split) as a positive int.
    A split/index drift would make the journal anchor disagree with the
    shell-core verifier's parser and fail every handshake with
    StartTimeMismatch. Pin: live value matches a direct /proc read,
    is a positive int, and is deterministic for the same process."""
    import pathlib

    a = helper._pid_starttime()
    b = helper._pid_starttime()
    assert a is not None and isinstance(a, int) and a > 0, f"got {a!r}"
    assert a == b, f"non-deterministic for the same process: {a} vs {b}"

    # Cross-check against a direct /proc/self/stat read.
    stat_text = pathlib.Path("/proc/self/stat").read_text(encoding="utf-8")
    expected = int(stat_text.rsplit(")", 1)[1].split()[19])
    assert a == expected, f"helper {a} != direct /proc read {expected}"


def test_pid_starttime_degrades_to_none_without_proc(monkeypatch):
    """On a host without /proc (or if the file vanishes mid-run), the
    function must return None — never raise."""
    monkeypatch.setattr(
        helper.Path, "read_text",
        lambda *a, **kw: (_ for _ in ()).throw(OSError("no /proc")),
    )
    # Monkeypatch the module's Path to a type whose read_text always fails.
    class FakePath:
        def __init__(self, p):
            self._p = p
        def read_text(self, *a, **kw):
            raise OSError("no /proc")
    import unittest.mock
    with unittest.mock.patch.object(helper, "Path", FakePath):
        assert helper._pid_starttime() is None


def test_read_context_rejects_a_symlinked_runtime_directory(tmp_path, monkeypatch):
    """The resolve() vs absolute() guard: a symlink planted at the runtime
    DIRECTORY (not the journal file) redirects the ownership anchor to a
    foreign directory — the resolve() comparison must catch it even though
    the directory NAME still matches the token."""

    token = "e" * 32
    foreign = tmp_path / "foreign-runtime-dir"
    (foreign / f"mangaflow-desktop-{token}").mkdir(parents=True)
    runtime_parent = tmp_path / "runtime-link-parent"
    runtime_parent.mkdir(parents=True)
    runtime_dir = runtime_parent / f"mangaflow-desktop-{token}"
    runtime_dir.symlink_to(foreign / f"mangaflow-desktop-{token}")
    (foreign / f"mangaflow-desktop-{token}" / "owner.json").write_text(
        "{}", encoding="utf-8"
    )

    monkeypatch.setenv("MANGAFLOW_DESKTOP_TOKEN", token)
    monkeypatch.setenv(
        "MANGAFLOW_DESKTOP_JOURNAL", str(runtime_dir / "owner.json")
    )

    with pytest.raises(ValueError, match="ownership mismatch"):
        helper._read_context()

def test_await_go_accepts_exact_line_and_rejects_drift(monkeypatch):
    """_await_go is the handshake's final gate: the line must be exactly
    GO_PREFIX + token after strip() — a wrong token, a GO for a previous
    run, or extra payload on the line must all be rejected (False), and
    only the exact match accepted (True). Pins both sides of the gate."""

    import importlib.util
    import io

    spec = importlib.util.spec_from_file_location(
        "mangaflow_desktop_helper_go", str(HELPER_PATH)
    )
    helper_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper_module)

    token = "e" * 32
    monkeypatch.setattr(
        helper_module, "GO_PREFIX", "MANGAFLOW_GO "
    )

    def run(stdin_text):
        fake = io.StringIO(stdin_text)
        monkeypatch.setattr(helper_module.sys, "stdin", fake)
        return helper_module._await_go(token)

    assert run(f"MANGAFLOW_GO {token}\n") is True
    assert run(f"MANGAFLOW_GO {token}  \n") is True  # strip() forgives EOL spaces
    assert run(f"MANGAFLOW_GO {'f' * 32}\n") is False  # wrong token
    assert run(f"MANGAFLOW_GO {token} extra\n") is False  # payload on the line
    assert run("\n") is False  # empty line
    assert run("") is False  # bare EOF-ish empty string



def test_bind_loopback_claims_an_ephemeral_port_and_sets_the_platform_option(monkeypatch):
    """The API bind: an ephemeral loopback port claimed atomically, with
    the platform-correct socket option (SO_REUSEADDR on POSIX so TIME_WAIT
    rebind works; SO_EXCLUSIVEADDRUSE on win32 so a co-bind fails closed —
    the opposite semantics, pinned via the option each platform sets)."""

    import importlib.util
    import socket as socket_module

    spec = importlib.util.spec_from_file_location(
        "mangaflow_desktop_helper_bind", str(HELPER_PATH)
    )
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)

    observed = {}
    real_setsockopt = socket_module.socket.setsockopt

    def spy_setsockopt(self, level, optname, value):
        observed[optname] = value
        return real_setsockopt(self, level, optname, value)

    monkeypatch.setattr(socket_module.socket, "setsockopt", spy_setsockopt)

    if sys.platform == "win32":
        monkeypatch.setattr(
            socket_module, "SO_REUSEADDR", getattr(socket_module, "SO_REUSEADDR", 4)
        )

    sock = helper._bind_loopback()
    try:
        host, port = sock.getsockname()
        assert host == "127.0.0.1", "the API must bind loopback only"
        assert port > 0, "the port must be ephemeral (kernel-assigned)"
        # A listening socket (the helper calls listen(128) before returning).
        assert sock.getsockopt(socket_module.SOL_SOCKET, socket_module.SO_ACCEPTCONN) != 0
        if sys.platform == "win32":
            assert observed.get(socket_module.SO_EXCLUSIVEADDRUSE) == 1
        else:
            assert observed.get(socket_module.SO_REUSEADDR) == 1
    finally:
        sock.close()

    # After close, a fresh bind claims a fresh kernel-assigned port (the
    # guard's claim is repeatability, not same-port TIME_WAIT rebind — a
    # closed listening socket produces no TIME_WAIT; that belongs to
    # connections).
    second = helper._bind_loopback()
    second_host, second_port = second.getsockname()
    try:
        assert second_host == "127.0.0.1"
    finally:
        second.close()


def test_apply_app_environment_creates_absent_keys(monkeypatch, tmp_path):
    """The inherited-zero pin (#313) covers the override leg; this pins the
    ABSENT leg — keys not in the parent environ at all must still be
    force-created (a shell started from a minimal env, e.g. a systemd unit
    or CI runner, must not produce a helper missing DATABASE_URL and
    silently defaulting to a CWD-relative sqlite file)."""

    for key in ("MANGAFLOW_DISABLE_DOTENV", "DATABASE_URL", "STORAGE_ROOT",
                "UPLOAD_ROOT", "WEB_ORIGIN"):
        monkeypatch.delenv(key, raising=False)

    helper._apply_app_environment(tmp_path, "http://tauri.localhost")

    assert helper.os.environ["MANGAFLOW_DISABLE_DOTENV"] == "1"
    assert helper.os.environ["DATABASE_URL"] == f"sqlite:///{tmp_path / 'data' / 'mangaflow.db'}"
    assert helper.os.environ["STORAGE_ROOT"] == str(tmp_path / "storage")
    assert helper.os.environ["UPLOAD_ROOT"] == str(tmp_path / "uploads")
    assert helper.os.environ["WEB_ORIGIN"] == "http://tauri.localhost"


def test_stdin_eof_watch_signals_on_immediate_eof(monkeypatch):
    """The EOF leg itself: stdin closing with ZERO post-GO bytes (the
    shell's graceful stop path — stdin closed, nothing written) must raise
    SIGTERM. The drain loop's contract is `readline() != ""`, so an empty
    first read is EOF, not junk — a regression to `if line:` (treating EOF
    as junk and looping forever) would hang the cooperative stop."""

    import importlib.util
    import io
    import signal
    import time as time_module

    spec = importlib.util.spec_from_file_location(
        "mangaflow_desktop_helper_eof0", str(HELPER_PATH)
    )
    helper_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper_module)

    delivered = []
    monkeypatch.setattr(
        helper_module.signal, "raise_signal",
        lambda sig: delivered.append(sig),
    )
    fake_stdin = io.StringIO("")
    monkeypatch.setattr(helper_module.sys, "stdin", fake_stdin)

    helper_module._start_stdin_eof_watch()
    deadline = time_module.monotonic() + 5
    while not delivered and time_module.monotonic() < deadline:
        time_module.sleep(0.05)
    assert delivered == [signal.SIGTERM], (
        f"immediate EOF must raise SIGTERM: {delivered!r}"
    )
