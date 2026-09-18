"""Resolve a POSIX-capable bash for the desktop script contract suites.

On a Windows host the PATH-order ``bash`` can be the WSL stub
(``System32\\bash.exe``): without an installed distribution it prints a
UTF-16 notice and exits 1, so every contract pin that shells a bash script
fails for an ENVIRONMENT reason, not a contract reason (the #839 CI red:
windows-latest resolves ``bash`` to the stub ahead of Git's). Resolve once
per process instead of trusting the PATH order: prefer the Git-for-Windows
bash that ships beside the ``git`` on PATH, then the PATH ``bash`` (POSIX
hosts, and Windows hosts whose PATH already favors Git). A candidate counts
only when a probe ``-c`` body actually runs and answers.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

_PROBE_ANSWER = "mfbash-probe-ok"

_resolved: str | None = None
_resolution_done = False


def _git_bash_candidates() -> list[str]:
    git = shutil.which("git")
    if not git:
        return []
    # git.exe lives at <root>/Cmd/git.exe (installer) or <root>/bin/git.exe
    # (portable); bash.exe sits in <root>/bin for both layouts.
    try:
        root = Path(git).resolve().parents[1]
    except (OSError, IndexError):
        return []
    return [str(root / "bin" / "bash.exe")]


def resolve_posix_bash() -> str | None:
    """The first probe-verified POSIX bash, or None on this host."""

    global _resolved, _resolution_done
    if _resolution_done:
        return _resolved
    _resolution_done = True
    for candidate in [*_git_bash_candidates(), "bash"]:
        try:
            probe = subprocess.run(
                [candidate, "-c", f"echo {_PROBE_ANSWER}"],
                capture_output=True,
                text=True,
                timeout=20,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if probe.returncode == 0 and _PROBE_ANSWER in probe.stdout:
            _resolved = candidate
            break
    return _resolved
