"""Offline tests for the boot-time storage retention sweeps.

Covers the two unbounded-growth findings:

1. ``cli_executor.sweep_retained_cli_runs`` — RETAINED/FAILED/PENDING CLI run
   directories past the window are deleted after full revalidation; fresh
   runs, live-state rows, and runs containing a planted junction are kept.
2. ``media.sweep_orphan_generated_files`` — generated files and thumbnails
   older than the grace window with no referencing ``Asset`` row are removed;
   referenced files (including via thumbnail keys), soft-deleted-referenced
   files, fresh files, and link targets are kept.
"""

import json
import logging
import os
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.database import Base
from app.models import (
    AIModel,
    Asset,
    CLIExecutionRun,
    GenerationJob,
    ModelCallAttempt,
    Project,
    ProviderConnection,
    ProviderProfile,
)
from app.services.cli_executor import (
    CLIExecutionController,
    CLIExecutionRequest,
    sweep_retained_cli_runs,
)
from app.services.media import sweep_orphan_generated_files

_OLD_DAYS = 8
_WINDOW = timedelta(days=7)


def _age_file(path: Path, days: float = _OLD_DAYS) -> None:
    stamp = time.time() - days * 86400
    os.utime(path, (stamp, stamp))


def _make_junction(link: Path, target: Path) -> bool:
    """Junctions need no privilege on Windows, unlike symlinks."""

    if sys.platform != "win32":
        return False
    target.mkdir(parents=True, exist_ok=True)
    link.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        # mklink localizes its output (cp936 on this host): decode permissively,
        # the return code is the actual signal.
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return result.returncode == 0


# ---------------------------------------------------------------------------
# CLI run retention sweep
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_context(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'sweep-cli.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    settings = Settings(
        storage_root=tmp_path / "storage",
        upload_root=tmp_path / "uploads",
        cli_channel_max_concurrency=4,
    )
    settings.ensure_directories()
    with factory() as db:
        project = Project(name="CLI 清扫项目")
        provider = ProviderProfile(name="Fake CLI", preset_key="fake-cli-sweep", enabled=True)
        db.add_all([project, provider])
        db.flush()
        connection = ProviderConnection(
            provider_id=provider.id,
            name="Fake CLI",
            protocol="CLI_FAKE",
            base_url="cli://fake",
            enabled=True,
            health_state="AVAILABLE",
        )
        db.add(connection)
        db.flush()
        model = AIModel(
            connection_id=connection.id,
            provider_model_id="fake-image",
            display_name="Fake Image",
            model_type="IMAGE",
            operations=["image_generate"],
            enabled=True,
        )
        job = GenerationJob(
            project_id=project.id,
            target_type="PAGE_CANDIDATE",
            target_id="target-1",
            job_type="PAGE_GENERATE",
            status="GENERATING",
            attempt_count=1,
        )
        db.add_all([model, job])
        db.commit()
        ids = {
            "project": project.id,
            "job": job.id,
            "connection": connection.id,
            "model": model.id,
            "next_dispatch": 1,
        }
    controller = CLIExecutionController(settings, factory)
    try:
        yield settings, factory, controller, ids
    finally:
        engine.dispose()


def _stage_run(factory, controller, ids, prompt, *, state, cleanup, age_days=None) -> str:
    """Prepare a real run (journal, token, directories) and stage its row.

    Each run needs its own ModelCallAttempt (unique) and releases its lease
    slot immediately so several runs can be staged per test.
    """

    with factory() as db:
        attempt = ModelCallAttempt(
            job_id=ids["job"],
            project_id=ids["project"],
            job_attempt=1,
            dispatch_no=ids["next_dispatch"],
            provider="fake-cli",
            model_id="fake-image",
            catalog_model_id=ids["model"],
            connection_id=ids["connection"],
        )
        db.add(attempt)
        db.commit()
        attempt_id = attempt.id
    ids["next_dispatch"] += 1
    run_id = controller.prepare(
        job_id=ids["job"],
        model_call_attempt_id=attempt_id,
        connection_id=ids["connection"],
        catalog_model_id=ids["model"],
        request=CLIExecutionRequest(operation="image_generate", prompt=prompt),
    )
    with factory() as db:
        row = db.get(CLIExecutionRun, run_id)
        row.state = state
        row.cleanup_state = cleanup
        row.lease_slot = None
        row.finished_at = datetime.now(UTC)
        if age_days is not None:
            row.created_at = datetime.now(UTC) - timedelta(days=age_days)
        db.commit()
    return run_id


def _run_row(factory, run_id) -> CLIExecutionRun:
    with factory() as db:
        row = db.get(CLIExecutionRun, run_id)
        db.expunge(row)
        return row


def test_old_retained_removed_fresh_retained_kept(cli_context):
    settings, factory, controller, ids = cli_context
    stale_id = _stage_run(
        factory, controller, ids, "旧保留", state="FAILED", cleanup="RETAINED", age_days=_OLD_DAYS
    )
    fresh_id = _stage_run(
        factory, controller, ids, "新保留", state="FAILED", cleanup="RETAINED", age_days=0
    )

    counts = sweep_retained_cli_runs(settings, factory, older_than=_WINDOW)

    assert counts["removed"] == 1
    assert not (settings.storage_root / "cli_runs" / stale_id).exists()
    assert _run_row(factory, stale_id).cleanup_state == "CLEANED"
    fresh_dir = settings.storage_root / "cli_runs" / fresh_id
    assert fresh_dir.exists()
    assert _run_row(factory, fresh_id).cleanup_state == "RETAINED"


def test_old_pending_terminal_run_removed(cli_context):
    """PENDING cleanup on a terminal run (crash between finish and cleanup)
    is reapable once past the window."""

    settings, factory, controller, ids = cli_context
    pending_id = _stage_run(
        factory,
        controller,
        ids,
        "挂起清扫",
        state="FAILED",
        cleanup="PENDING",
        age_days=_OLD_DAYS,
    )

    counts = sweep_retained_cli_runs(settings, factory, older_than=_WINDOW)

    assert counts["removed"] == 1
    assert not (settings.storage_root / "cli_runs" / pending_id).exists()
    assert _run_row(factory, pending_id).cleanup_state == "CLEANED"


def test_live_state_row_never_swept(cli_context):
    """A PREPARING/RUNNING row is recover_abandoned's business; even an old
    PENDING row in a live state is never deleted by the retention sweep."""

    settings, factory, controller, ids = cli_context
    running_id = _stage_run(
        factory,
        controller,
        ids,
        "仍在运行",
        state="RUNNING",
        cleanup="PENDING",
        age_days=_OLD_DAYS,
    )

    counts = sweep_retained_cli_runs(settings, factory, older_than=_WINDOW)

    assert counts["removed"] == 0
    assert (settings.storage_root / "cli_runs" / running_id).exists()


def test_junction_inside_run_dir_not_followed(cli_context):
    settings, factory, controller, ids = cli_context
    run_id = _stage_run(
        factory, controller, ids, "带联接", state="FAILED", cleanup="RETAINED", age_days=_OLD_DAYS
    )
    run_dir = settings.storage_root / "cli_runs" / run_id
    outside = settings.storage_root / "outside-target"
    outside.mkdir(parents=True, exist_ok=True)
    sentinel = outside / "sentinel.txt"
    sentinel.write_text("keep me", encoding="utf-8")
    if not _make_junction(run_dir / "workspace" / "escape", outside):
        pytest.skip("junction creation unavailable on this host")

    counts = sweep_retained_cli_runs(settings, factory, older_than=_WINDOW)

    assert counts["removed"] == 0
    assert counts["failed"] >= 1
    # The run is retained for manual inspection; the link target is intact.
    assert run_dir.exists()
    assert _run_row(factory, run_id).cleanup_state == "FAILED"
    assert sentinel.read_text(encoding="utf-8") == "keep me"
    assert outside.exists()


def test_missing_directory_converges_to_cleaned(cli_context):
    settings, factory, controller, ids = cli_context
    run_id = _stage_run(
        factory,
        controller,
        ids,
        "目录已消失",
        state="FAILED",
        cleanup="RETAINED",
        age_days=_OLD_DAYS,
    )
    run_dir = settings.storage_root / "cli_runs" / run_id
    assert run_dir.exists()
    # Remove via the controller's own writable-inputs handling (read-only files).
    for path in (run_dir / "input").rglob("*"):
        if path.is_file():
            path.chmod(0o644)
    shutil.rmtree(run_dir)

    counts = sweep_retained_cli_runs(settings, factory, older_than=_WINDOW)

    assert counts["removed"] == 1
    assert _run_row(factory, run_id).cleanup_state == "CLEANED"


def _force_remove_run_dir(run_dir: Path) -> None:
    """rmtree a run dir, making the read-only input files writable first."""

    for path in (run_dir / "input").rglob("*"):
        if path.is_file():
            path.chmod(0o644)
    shutil.rmtree(run_dir)


def test_sweep_aborts_when_storage_root_unresolvable(cli_context, tmp_path, caplog):
    """A missing storage root must abort the sweep before any row is touched.

    Otherwise _validate_directory's root resolve raises FileNotFoundError for
    every row, and the per-row handler would converge them all to CLEANED —
    deleting nothing while hiding the rows from every later sweep (CLEANED is
    outside the SELECT set).
    """

    settings, factory, controller, ids = cli_context
    run_id = _stage_run(
        factory,
        controller,
        ids,
        "根目录缺失",
        state="FAILED",
        cleanup="RETAINED",
        age_days=_OLD_DAYS,
    )
    missing = Settings(
        storage_root=tmp_path / "missing-storage", upload_root=tmp_path / "missing-uploads"
    )

    with caplog.at_level(logging.WARNING, logger="mangaflow.cli"):
        counts = sweep_retained_cli_runs(missing, factory, older_than=_WINDOW)

    assert counts == {"removed": 0, "kept": 0, "failed": 0}
    assert "storage root unresolvable" in caplog.text
    assert _run_row(factory, run_id).cleanup_state == "RETAINED"
    assert (settings.storage_root / "cli_runs" / run_id).exists()
    # The abort poisoned nothing: a sweep with a resolvable root still works.
    assert controller.sweep_retained(older_than=_WINDOW)["removed"] == 1
    assert _run_row(factory, run_id).cleanup_state == "CLEANED"


def test_dir_vanishing_between_scan_and_cleanup_converges_cleaned(
    cli_context, monkeypatch
):
    """A directory deleted after the sweep's validation but before _cleanup's
    revalidation converges to CLEANED, not FAILED-forever."""

    settings, factory, controller, ids = cli_context
    run_id = _stage_run(
        factory,
        controller,
        ids,
        "清扫后消失",
        state="FAILED",
        cleanup="RETAINED",
        age_days=_OLD_DAYS,
    )
    run_dir = settings.storage_root / "cli_runs" / run_id
    real_validate = controller._validate_directory
    calls: list[str] = []

    def validate_then_vanish(row):
        calls.append(row.id)
        if len(calls) == 2:  # called from _cleanup: the sweep scan already passed
            _force_remove_run_dir(run_dir)
        return real_validate(row)

    monkeypatch.setattr(controller, "_validate_directory", validate_then_vanish)

    counts = controller.sweep_retained(older_than=_WINDOW)

    assert counts["removed"] == 1
    assert counts["failed"] == 0
    assert not run_dir.exists()
    assert _run_row(factory, run_id).cleanup_state == "CLEANED"


def test_row_with_id_escaping_cli_runs_is_refused(cli_context):
    """A crafted row whose id resolves outside cli_runs (but still inside
    storage_root, with a matching journal) passes the old equality-only
    ownership anchor; containment must refuse deletion."""

    settings, factory, controller, ids = cli_context
    legit_id = _stage_run(
        factory,
        controller,
        ids,
        "正常清扫",
        state="FAILED",
        cleanup="RETAINED",
        age_days=_OLD_DAYS,
    )
    token = uuid4().hex
    evil_dir = settings.storage_root / "evil"
    evil_dir.mkdir(parents=True, exist_ok=True)
    (evil_dir / "journal.json").write_text(
        json.dumps({"version": 1, "run_id": "../evil", "token": token, "state": "RETAINED"}),
        encoding="utf-8",
    )
    sentinel = evil_dir / "sentinel.txt"
    sentinel.write_text("keep me", encoding="utf-8")
    with factory() as db:
        attempt = ModelCallAttempt(
            job_id=ids["job"],
            project_id=ids["project"],
            job_attempt=1,
            dispatch_no=ids["next_dispatch"],
            provider="fake-cli",
            model_id="fake-image",
            catalog_model_id=ids["model"],
            connection_id=ids["connection"],
        )
        db.add(attempt)
        db.commit()
        ids["next_dispatch"] += 1
        evil = CLIExecutionRun(
            id="../evil",
            job_id=ids["job"],
            model_call_attempt_id=attempt.id,
            connection_id=ids["connection"],
            catalog_model_id=ids["model"],
            run_token=token,
            relative_path="evil",
            operation="image_generate",
            state="FAILED",
            cleanup_state="RETAINED",
            lease_slot=None,
            request_checksum=sha256(b"evil").hexdigest(),
            output_manifest={},
        )
        db.add(evil)
        db.commit()
        evil.created_at = datetime.now(UTC) - timedelta(days=_OLD_DAYS)
        db.commit()

    counts = sweep_retained_cli_runs(settings, factory, older_than=_WINDOW)

    # The legit run was removed; the escaping row was refused, not deleted.
    assert counts["removed"] == 1
    assert counts["failed"] == 1
    assert sentinel.read_text(encoding="utf-8") == "keep me"
    assert evil_dir.exists()
    assert _run_row(factory, "../evil").cleanup_state == "RETAINED"
    assert not (settings.storage_root / "cli_runs" / legit_id).exists()
    assert _run_row(factory, legit_id).cleanup_state == "CLEANED"


# ---------------------------------------------------------------------------
# Orphan generated-media sweep
# ---------------------------------------------------------------------------


@pytest.fixture
def media_context(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'sweep-media.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    settings = Settings(storage_root=tmp_path / "media-storage", upload_root=tmp_path / "uploads")
    settings.ensure_directories()
    with factory() as db:
        project = Project(name="媒体清扫项目")
        db.add(project)
        db.commit()
        project_id = project.id
    try:
        yield settings, factory, project_id
    finally:
        engine.dispose()


def _write_media(settings, relative: str, *, old: bool = True, content: bytes = b"img") -> Path:
    path = settings.storage_root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    if old:
        _age_file(path)
    return path


def _add_asset(factory, project_id, *, storage_key, thumb_320=None, thumb_640=None, deleted=False):
    with factory() as db:
        asset = Asset(
            project_id=project_id,
            kind="page_candidate",
            original_name=storage_key.rsplit("/", 1)[-1],
            storage_key=storage_key,
            mime_type="image/png",
            byte_size=3,
            sha256=sha256(storage_key.encode()).hexdigest(),
            source="AI_GENERATED",
            thumbnail_320_key=thumb_320,
            thumbnail_640_key=thumb_640,
            deleted_at=datetime.now(UTC) if deleted else None,
        )
        db.add(asset)
        db.commit()
        asset_id = asset.id
    return asset_id


def test_orphan_sweep_removes_only_old_unreferenced_media(media_context):
    settings, factory, project_id = media_context

    orphan = _write_media(settings, "generated/p1/b1/orphan.png")
    referenced = _write_media(settings, "generated/p1/b1/referenced.png")
    fresh_orphan = _write_media(settings, "generated/p1/b1/fresh.png", old=False)
    _add_asset(factory, project_id, storage_key="generated/p1/b1/referenced.png")

    counts = sweep_orphan_generated_files(settings, factory, older_than=_WINDOW)

    assert not orphan.exists()
    assert referenced.exists()
    assert fresh_orphan.exists()
    assert counts["removed"] == 1
    assert counts["failed"] == 0


def test_orphan_sweep_matches_thumbnails_by_key(media_context):
    settings, factory, project_id = media_context

    asset_id = _add_asset(factory, project_id, storage_key="generated/p1/b1/live.png")
    with factory() as db:
        row = db.get(Asset, asset_id)
        row.thumbnail_320_key = f"thumbnails/{asset_id}/320.webp"
        row.thumbnail_640_key = f"thumbnails/{asset_id}/640.webp"
        db.commit()
    _write_media(settings, "generated/p1/b1/live.png")
    kept_320 = _write_media(settings, f"thumbnails/{asset_id}/320.webp")
    kept_640 = _write_media(settings, f"thumbnails/{asset_id}/640.webp")
    ghost_thumb = _write_media(settings, "thumbnails/ghost-asset/320.webp")

    counts = sweep_orphan_generated_files(settings, factory, older_than=_WINDOW)

    assert kept_320.exists()
    assert kept_640.exists()
    assert not ghost_thumb.exists()
    assert counts["removed"] == 1


def test_orphan_sweep_keeps_soft_deleted_references(media_context):
    """Asset deletes unlink no files by design: a soft-deleted row still
    owns its bytes and must keep them."""

    settings, factory, project_id = media_context

    key = "generated/p1/b1/soft-deleted.png"
    kept = _write_media(settings, key)
    _add_asset(factory, project_id, storage_key=key, deleted=True)

    sweep_orphan_generated_files(settings, factory, older_than=_WINDOW)

    assert kept.exists()


def test_orphan_sweep_without_media_roots_is_noop(media_context):
    settings, factory, _project_id = media_context

    counts = sweep_orphan_generated_files(settings, factory, older_than=_WINDOW)

    assert counts == {"removed": 0, "failed": 0, "scanned": 0}


def test_orphan_sweep_junction_not_followed(media_context):
    settings, factory, project_id = media_context

    orphan = _write_media(settings, "generated/p1/b1/orphan.png")
    outside = settings.storage_root.parent / "outside-media"
    outside_file = outside / "payload.bin"
    outside.mkdir(parents=True, exist_ok=True)
    outside_file.write_bytes(b"outside")
    _age_file(outside_file)
    if not _make_junction(settings.storage_root / "thumbnails" / "escape", outside):
        pytest.skip("junction creation unavailable on this host")

    sweep_orphan_generated_files(settings, factory, older_than=_WINDOW)

    assert not orphan.exists()
    # The junction was neither traversed nor removed; its target is intact.
    assert outside_file.exists()
    assert (settings.storage_root / "thumbnails" / "escape").exists()
