"""Runner-list integrity for the desktop sidecar suites (#343 discipline).

``pytest.ini`` excludes the whole ``apps/desktop`` tree from default
collection, and ``run-sidecar-e2e.sh`` invokes pytest with an EXPLICIT list
of test files. An unlisted file is therefore an untested file: its
regressions pass every gate — including ``npm run check`` and the e2e
runner — forever, with no signal. This contract test diffs the directory
glob against the runner's invocation so the next added suite cannot ship
silently unwired.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "apps" / "desktop" / "scripts"
RUNNER = SCRIPTS / "run-sidecar-e2e.sh"


def test_every_desktop_script_suite_is_wired_into_the_sidecar_runner():
    runner_source = RUNNER.read_text(encoding="utf-8")
    # The invocation block lists basenames; collect everything pytest-shaped
    # in the runner (at minimum the test files it names).
    import re

    listed = set(re.findall(r"test_[a-z0-9_]+\.py", runner_source))
    assert listed, "the runner must keep its explicit pytest file list"

    on_disk = {path.name for path in SCRIPTS.glob("test_*.py")}
    unwired = on_disk - listed
    assert not unwired, (
        "apps/desktop/scripts test files absent from run-sidecar-e2e.sh's "
        f"pytest invocation are never run anywhere (pytest.ini excludes the "
        f"desktop tree): {sorted(unwired)}"
    )
