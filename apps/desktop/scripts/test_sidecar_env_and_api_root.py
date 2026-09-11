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
