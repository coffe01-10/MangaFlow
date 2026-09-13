"""Static no-undef gate for the desktop .mjs scripts (#713 class).

#713 shipped a block-scoped name read at function scope in
verify-static-origin.mjs — a runtime ReferenceError that node --check
passes (scope errors are not syntax) and that only surfaced when the
DEFAULT verification mode executed past the handshake. A no-undef lint
pass flags exactly that class statically. The repo has no eslint config
covering apps/desktop/scripts, so the gate runs eslint with
--no-config-lookup (no project config interference) and an explicit
minimal global set.

Skipped when eslint is unavailable: the gate is a static backstop, not
a hard dependency of the suites it protects.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent
_MJS_FILES = sorted(_SCRIPTS.glob("*.mjs"))

pytestmark = pytest.mark.skipif(
    shutil.which("npx") is None,
    reason="eslint runs through npx; unavailable on this host",
)

_GLOBALS = (
    "console:true",
    "process:true",
    "fetch:true",
    "window:true",
    "URL:true",
    "setTimeout:true",
    "clearTimeout:true",
    "location:true",
    "document:true",
    "AbortSignal:true",
)


def test_mjs_scripts_have_no_undefined_names():
    assert _MJS_FILES, "the desktop scripts must ship at least one .mjs file"
    argv = ["npx", "--no-install", "eslint", "--no-config-lookup",
            '--rule={"no-undef": "error"}']
    for entry in _GLOBALS:
        argv += ["--global", entry]
    argv += [str(path) for path in _MJS_FILES]
    done = subprocess.run(argv, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, (
        "no-undef findings (the #713 class: a block-scoped name read at "
        f"function scope crashes at runtime, not at syntax check):\n"
        f"{done.stdout}\n{done.stderr}"
    )
