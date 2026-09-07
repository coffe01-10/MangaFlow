"""Regression: CLI executable validation is absolute-path syntax, not host CWD.

``update_connection`` validated ``cli_executable`` with
``Path(executable).is_absolute()``, which depends on the platform running
the API: a Windows-form absolute path such as ``C:\\tools\\agy.exe`` was
rejected with 422 on POSIX hosts (and POSIX paths on Windows), breaking
the documented "name or absolute path" contract and the offline suite.
Both syntaxes are now accepted; the probe still fails closed when the
configured file does not exist on the actual host.
"""

import pytest

from app.services.provider_catalog import is_absolute_executable_path


@pytest.mark.parametrize(
    "value",
    [
        "C:\\tools\\agy.exe",
        "c:\\tools\\agy.exe",
        "\\\\?\\C:\\tools\\codex.exe",
        "\\\\server\\share\\grok.exe",
        "/usr/local/bin/agy",
        "/opt/codex-cli/codex",
    ],
)
def test_absolute_syntaxes_are_accepted(value: str):
    assert is_absolute_executable_path(value)


@pytest.mark.parametrize(
    "value",
    [
        "tools/agy.exe",
        "tools\\agy.exe",
        "C:agy.exe",
        "agy",
        ".\\codex.exe",
        "..\\codex.exe",
    ],
)
def test_relative_and_drive_relative_syntaxes_are_rejected(value: str):
    assert not is_absolute_executable_path(value)
