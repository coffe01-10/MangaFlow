"""Regression tests for the helper's startup environment ownership (#313, #314).

Two red-team findings from 2026-09-09 are pinned here:

- MANGAFLOW_DISABLE_DOTENV used setdefault, so an inherited value of ``0``
  re-enabled .env loading relative to the helper's CWD on every start;
- --api-root was inserted at sys.path[0] unvalidated, letting a wrong or
  hostile tree shadow the helper's own modules (fake_channel) and hijack
  alembic's env.py before the API ever imported.

    python3 -m pytest apps/desktop/scripts/test_sidecar_env_and_api_root.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sidecar"))

import mangaflow_desktop_helper as helper  # noqa: E402


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
    ],
)
def test_bad_api_root_trees_are_rejected_before_sys_path(tmp_path, reason, plant):
    plant(tmp_path)
    # A fake_channel.py at the root would sit at sys.path[0] and shadow the
    # helper's own module next to the sidecar package — the exact hijack the
    # red team described.
    assert helper._validate_api_root(tmp_path) == reason
