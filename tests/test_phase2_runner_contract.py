"""Platform-independent contract tests for the phase2 acceptance runner.

The phase2 runner library (scripts/phase2_runner_lib.cjs) is loaded by
playwright.config.ts and global-setup.ts on every platform, but its only
behavioral tests used to live inside the win32-gated module
(test_e2e_isolation.py), so POSIX runs silently skipped them (#431).
These tests have no Windows dependency and run wherever `node` exists.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_phase2_runner_failure_paths():
    result = subprocess.run(
        [os.environ.get("MANGAFLOW_NODE", "node"), "--test", "scripts/phase2_runner.test.mjs"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_controller_forwards_mixed_encoding_log_as_raw_bytes():
    sys.path.insert(0, str(ROOT / "scripts"))
    from run_e2e_owned import forward_log_chunk

    class Buffer:
        def __init__(self):
            self.written = bytearray()
            self.flushed = False

        def write(self, chunk):
            self.written.extend(chunk)

        def flush(self):
            self.flushed = True

    class TextTarget:
        def __init__(self):
            self.buffer = Buffer()

        def write(self, _text):
            raise AssertionError("log forwarding must not use text encoding")

    target = TextTarget()
    chunk = b"pytest path: \xff\xfe\xe4\xb8\xad\xe6\x96\x87\n"
    forward_log_chunk(chunk, stdout=target)
    assert bytes(target.buffer.written) == chunk
    assert target.buffer.flushed


def test_phase2_runner_failure_paths_use_the_documented_node_resolver():
    """The subprocess must resolve node exactly like run_e2e_owned.py does:
    MANGAFLOW_NODE first, then PATH (`shutil.which`). A resolution drift
    between the controller and this contract test would run different
    node builds and let a platform-specific runner bug pass unseen."""

    import inspect

    source = inspect.getsource(test_phase2_runner_failure_paths)
    assert 'os.environ.get("MANGAFLOW_NODE", "node")' in source, (
        "the contract test must use the documented MANGAFLOW_NODE resolver"
    )
