"""Safe preparation entry point for the Phase 2 acceptance harness.

The offline harness runs by default. Live pytest execution is available through
pytest itself with explicit isolated URLs (--run-live-integration --pg-url ...
--redis-url ...); the owner-scoped container orchestration behind
--run-live/--start-containers is still not implemented, so those switches stay
a nonzero BLOCKED result that must not be presented as live acceptance.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.acceptance_safety import (  # noqa: E402
    mask_url,
    validate_safe_acceptance_pg_url,
    validate_safe_acceptance_redis_url,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--run-live", action="store_true")
    parser.add_argument("--start-containers", action="store_true")
    parser.add_argument("--stop-containers", action="store_true")
    args = parser.parse_args(argv)

    # Validate data without embedding it in source code or importing app settings.
    pg = os.environ.get("MANGAFLOW_ACCEPTANCE_PG_URL", "")
    redis = os.environ.get("MANGAFLOW_ACCEPTANCE_REDIS_URL", "")
    try:
        if pg:
            validate_safe_acceptance_pg_url(pg)
        if redis:
            validate_safe_acceptance_redis_url(redis)
    except ValueError as exc:
        print(f"INVALID_CONFIGURATION: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        print(f"PostgreSQL: {mask_url(pg) if pg else 'NOT_CONFIGURED'}")
        print(f"Redis: {mask_url(redis) if redis else 'NOT_CONFIGURED'}")
        print("DRY_RUN: endpoint syntax only; no connection or ownership validation performed.")
        return 0

    if args.run_live or args.start_containers or args.stop_containers:
        print(
            "BLOCKED: owner-scoped live orchestration is not implemented. Run pytest with "
            "--run-live-integration and explicit isolated URLs instead. No service was "
            "connected, started, or stopped by this entry point.",
            file=sys.stderr,
        )
        return 2

    # The default preparation command is deliberately offline, regardless of
    # inherited opt-ins. It neither loads developer dotenv nor collects live tests.
    os.environ["MANGAFLOW_ENABLE_LIVE_INTEGRATION"] = "0"
    os.environ.pop("PYTEST_ADDOPTS", None)
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT / "apps" / "api"))
    with tempfile.TemporaryDirectory(prefix="mangaflow-acceptance-offline-") as directory:
        runtime = Path(directory)
        from app import config

        config.Settings.model_config["env_file"] = None
        settings = config.Settings(
            _env_file=None,
            database_url="sqlite://",
            queue_enabled=False,
            google_cloud_project=None,
            google_application_credentials=None,
            mangaflow_credential_master_key=None,
            mangaflow_proxy_url=None,
            storage_root=runtime / "storage",
            upload_root=runtime / "uploads",
        )
        config.get_settings = lambda: settings
        import pytest

        result = pytest.main(
            [
                str(ROOT / "tests" / "test_integration_harness.py"),
                "-q",
                "-p",
                "no:cacheprovider",
                f"--basetemp={runtime.as_posix()}/pytest",
            ]
        )
    print(f"OFFLINE_HARNESS_EXIT={result}; LIVE_ACCEPTANCE=NOT_RUN")
    return int(result)


if __name__ == "__main__":
    raise SystemExit(main())
