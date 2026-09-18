"""Contract pins for package-sidecar.sh (the PyInstaller bundler, D2).

The script had zero automated coverage. It carries one SECURITY property
and two packaging contracts that a casual edit could silently break:

1. The PyInstaller scratch directory MUST come from `mktemp -d` (0700).
   A predictable /tmp path is writable by any local user before the build
   starts, and PyInstaller FOLLOWS SYMLINKS when writing workpath/specpath
   artifacts — a planted /tmp/mangaflow-desktop-pyi/warn-*.txt -> ~/victim
   link would be written through. The mktemp form forecloses that.
2. The frozen importer resolves app.* only if the hidden-import set and
   --collect-submodules name the real modules (app.main, app.database,
   fake_channel…): dropping one freezes a sidecar that dies at boot.
3. The app-directory layout keeps alembic.ini + migrations/ inside
   _internal/ — the frozen app.main resolves its upgrade root there.

Run-verification is NOT RUN on Windows (the Windows PyInstaller leg) and
the full build needs the .venv-desktop pyinstaller; these pins are
static/read-only except the refusal arm, which runs in a fake repo tree.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from _bash_resolve import resolve_posix_bash

_SCRIPT = Path(__file__).with_name("package-sidecar.sh").resolve()


def test_packaging_refuses_without_pyinstaller(tmp_path):
    """No .venv-desktop pyinstaller → loud refusal naming the bootstrap,
    never a half build. Runs in a fake repo tree so the real venv (and any
    network/PyInstaller work) is untouched."""
    bash = resolve_posix_bash()
    if bash is None:
        pytest.skip("no POSIX-capable bash on this host (#839)")
    fake = tmp_path / "repo" / "apps" / "desktop" / "scripts"
    fake.mkdir(parents=True)
    script = fake / "package-sidecar.sh"
    script.write_text(_SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    result = subprocess.run(
        [bash, str(script)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "run-sidecar-e2e" in result.stderr, result.stderr
    assert "pyinstaller" in result.stderr, result.stderr


def test_scratch_directory_is_an_unpredictable_mktemp():
    source = _SCRIPT.read_text(encoding="utf-8")
    assert "mktemp -d" in source, (
        "the PyInstaller scratch dir must come from mktemp -d (0700): a "
        "predictable /tmp path is writable by any local user and PyInstaller "
        "follows symlinks when writing artifacts — a planted link would be "
        "written through"
    )
    assert 'PYI_SCRATCH="$(mktemp -d' in source, (
        "the scratch variable must be assigned from mktemp directly, not "
        "recomposed from a predictable prefix"
    )


def test_hidden_imports_pin_the_frozen_importer_contract():
    source = _SCRIPT.read_text(encoding="utf-8")
    for module in (
        "--hidden-import app",
        "--hidden-import app.main",
        "--hidden-import app.database",
        "--hidden-import fake_channel",
        "--collect-submodules app",
    ):
        assert module in source, (
            f"{module} must stay in the PyInstaller invocation — dropping it "
            "freezes a sidecar that dies at boot on the missing module"
        )


def test_support_files_land_inside_internal():
    source = _SCRIPT.read_text(encoding="utf-8")
    # The copy DESTINATION is the contract: the frozen app resolves
    # alembic.ini under <bundle>/_internal/, so both copies must target
    # "$LAYOUT/_internal/". (An ordering assertion on two loose substrings
    # is satisfied by any text that mentions them — it pinned nothing and
    # stayed green even with the destination pointing at $LAYOUT itself.)
    assert 'alembic.ini" "$LAYOUT/_internal/"' in source, (
        "alembic.ini must be copied INTO _internal — the frozen app "
        "resolves its upgrade root there"
    )
    assert 'migrations" "$LAYOUT/_internal/"' in source, (
        "migrations/ must be copied INTO _internal beside alembic.ini"
    )
    assert 'mkdir -p "$LAYOUT/_internal"' in source, (
        "the _internal directory must be created explicitly"
    )
