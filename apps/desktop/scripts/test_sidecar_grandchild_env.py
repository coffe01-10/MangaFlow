"""Grandchild spawn env/stdin isolation (#871).

``_spawn_grandchild`` is Unix-only (Windows returns None) but the Popen
kwargs are pinned here by stubbing ``sys.platform`` so the Windows CI
host still executes the contract. No real child is started.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sidecar"))

import mangaflow_desktop_helper as helper  # noqa: E402


class _FakeProcess:
    pid = 4242


def test_spawn_grandchild_uses_devnull_stdin_and_a_scrubbed_env(monkeypatch):
    """The descendant must not inherit the protocol stdin or the helper's
    app/python injection env. A PYTHONPATH-planted sitecustomize used to
    consume the GO line; DATABASE_URL/LD_PRELOAD rode along from
    ``_apply_app_environment``."""

    captured: dict[str, object] = {}

    def fake_popen(*_args, **kwargs):
        captured.update(kwargs)
        return _FakeProcess()

    monkeypatch.setattr(helper.sys, "platform", "linux")
    monkeypatch.setattr(helper.subprocess, "Popen", fake_popen)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///secret.db")
    monkeypatch.setenv("STORAGE_ROOT", "C:/stolen")
    monkeypatch.setenv("LD_PRELOAD", "evil.so")
    monkeypatch.setenv("PYTHONPATH", "/planted")
    monkeypatch.setenv("pythonpath", "/planted-lower")
    monkeypatch.setenv("PYTHONSTARTUP", "/planted/sitecustomize.py")

    child = helper._spawn_grandchild()
    assert child is not None
    assert child.pid == 4242
    assert captured["stdin"] is subprocess.DEVNULL, captured
    assert captured["stdout"] is subprocess.DEVNULL
    assert captured["stderr"] is subprocess.DEVNULL
    env = captured["env"]
    assert isinstance(env, dict), env
    upper = {name.upper() for name in env}
    for banned in (
        "DATABASE_URL",
        "STORAGE_ROOT",
        "LD_PRELOAD",
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "MANGAFLOW_DESKTOP_TOKEN",
        "MANGAFLOW_DESKTOP_JOURNAL",
    ):
        assert banned not in upper, f"{banned} must not ride into the grandchild: {env}"


def test_spawn_grandchild_still_returns_none_on_windows(monkeypatch):
    monkeypatch.setattr(helper.sys, "platform", "win32")

    def boom(*_args, **_kwargs):
        raise AssertionError("Windows must not spawn a grandchild")

    monkeypatch.setattr(helper.subprocess, "Popen", boom)
    assert helper._spawn_grandchild() is None


def test_descendant_env_reuses_the_node_scrub_plus_python_hooks(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///secret.db")
    monkeypatch.setenv("PYTHONPATH", "/planted")
    monkeypatch.setenv("PATH", "C:/Windows/system32")
    env = helper._descendant_env()
    upper = {name.upper(): value for name, value in env.items()}
    assert "DATABASE_URL" not in upper
    assert "PYTHONPATH" not in upper
    assert "PATH" in upper
