"""Offline contract tests for the provider-neutral CLI controller."""

import json
import os
import stat
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.database import Base
from app.model_adapters.base import ProviderAdapterError
from app.models import (
    AIModel,
    CLIExecutionRun,
    GenerationJob,
    ModelCallAttempt,
    ModelProbe,
    Project,
    ProviderConnection,
    ProviderProfile,
)
from app.services.cli_executor import (
    CLIExecutionController,
    CLIExecutionRequest,
    CLIProcessOutcome,
    build_cli_environment,
)
from app.services.cli_probe import CLIProbeObservation, probe_cli_connection


def test_cli_environment_overrides_require_explicit_non_python_whitelist(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    environment = build_cli_environment(
        workspace,
        ("HOME",),
        {"HOME": str(workspace / "private-home")},
    )
    assert environment["HOME"] == str(workspace / "private-home")
    with pytest.raises(ProviderAdapterError, match="白名单"):
        build_cli_environment(workspace, (), {"HOME": "hidden"})
    with pytest.raises(ProviderAdapterError, match="受保护"):
        build_cli_environment(workspace, ("PYTHONPATH",), {"PYTHONPATH": "hidden"})


def _symlinks_creatable() -> bool:
    """Privilege probe: creating symlinks needs developer mode/admin on Windows."""
    probe = Path(os.environ.get("TEMP", ".")) / "mangaflow-symlink-probe"
    target = probe.with_suffix(".target")
    try:
        target.mkdir(exist_ok=True)
        os.symlink(target, probe)
        return True
    except OSError:
        return False
    finally:
        if probe.is_symlink():
            probe.unlink()
        if target.exists():
            target.rmdir()


@pytest.fixture
def cli_context(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'cli.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    settings = Settings(
        storage_root=tmp_path / "storage",
        upload_root=tmp_path / "uploads",
        cli_channel_max_concurrency=1,
        cli_run_timeout_seconds=30,
    )
    settings.ensure_directories()
    with factory() as db:
        project = Project(name="CLI 离线项目")
        provider = ProviderProfile(name="Fake CLI", preset_key="fake-cli", enabled=True)
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
            operations=["image_generate", "image_edit"],
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
        db.flush()
        attempt = ModelCallAttempt(
            job_id=job.id,
            project_id=project.id,
            job_attempt=1,
            dispatch_no=1,
            provider="fake-cli",
            model_id="fake-image",
            catalog_model_id=model.id,
            connection_id=connection.id,
        )
        db.add(attempt)
        db.commit()
        ids = {
            "job": job.id,
            "attempt": attempt.id,
            "connection": connection.id,
            "model": model.id,
        }
    try:
        yield settings, factory, CLIExecutionController(settings, factory), ids
    finally:
        engine.dispose()


def _png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), color="navy").save(path, format="PNG")


def _prepare(controller, ids, **overrides):
    request = replace(
        CLIExecutionRequest(operation="image_generate", prompt="一页漫画"), **overrides
    )
    return controller.prepare(
        job_id=ids["job"],
        model_call_attempt_id=ids["attempt"],
        connection_id=ids["connection"],
        catalog_model_id=ids["model"],
        request=request,
    )


class SuccessRunner:
    def run(self, *, cwd, environment, timeout_seconds, cancel_requested, **_kwargs):
        assert "MANGAFLOW_TEST_SECRET" not in environment
        assert timeout_seconds == 30 and not cancel_requested()
        output = cwd.parent / "output"
        _png(output / "images" / "out_001.png")
        (output / "result.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "SUCCEEDED",
                    "images": ["output/images/out_001.png"],
                }
            ),
            encoding="utf-8",
        )
        return CLIProcessOutcome(exit_code=0, stdout=b"authorization: secret")


class MissingResultRunner:
    def run(self, **_kwargs):
        return CLIProcessOutcome(exit_code=0)


class TamperRunner:
    def run(self, *, cwd, **_kwargs):
        request_path = cwd.parent / "input" / "request.json"
        request_path.chmod(stat.S_IREAD | stat.S_IWRITE)
        request_path.write_text("{}", encoding="utf-8")
        return CLIProcessOutcome(exit_code=0)


def test_success_persists_manifest_releases_slot_and_cleans(cli_context, monkeypatch):
    settings, factory, controller, ids = cli_context
    monkeypatch.setenv("MANGAFLOW_TEST_SECRET", "must-not-leak")
    run_id = _prepare(controller, ids)
    result = controller.execute(run_id, runner=SuccessRunner(), argv=("fake-cli",))
    assert result.images
    assert result.usage == {"estimated_cost": None, "cost_source": "CLI_EXTERNAL"}
    with factory() as db:
        row = db.get(CLIExecutionRun, run_id)
        assert (row.state, row.cleanup_state, row.lease_slot) == (
            "COMPLETED",
            "CLEANED",
            None,
        )
        assert row.output_manifest["images"][0]["sha256"]
    assert not (settings.storage_root / "cli_runs" / run_id).exists()


def test_unknown_result_and_tampered_request_retain_evidence(cli_context):
    settings, factory, controller, ids = cli_context
    run_id = _prepare(controller, ids)
    with pytest.raises(ProviderAdapterError) as caught:
        controller.execute(run_id, runner=MissingResultRunner(), argv=("fake-cli",))
    assert caught.value.code == "UNKNOWN_RESULT"
    assert (settings.storage_root / "cli_runs" / run_id).exists()
    with factory() as db:
        row = db.get(CLIExecutionRun, run_id)
        assert (row.state, row.cleanup_state, row.lease_slot) == (
            "FAILED",
            "RETAINED",
            None,
        )


def test_request_checksum_prevents_output_manifest_expansion(cli_context):
    settings, _factory, controller, ids = cli_context
    run_id = _prepare(controller, ids)
    with pytest.raises(ProviderAdapterError) as caught:
        controller.execute(run_id, runner=TamperRunner(), argv=("fake-cli",))
    assert caught.value.code == "INVALID_OUTPUT"
    assert (settings.storage_root / "cli_runs" / run_id).exists()


def test_channel_slot_is_hard_and_reusable(cli_context):
    _settings, factory, controller, ids = cli_context
    first = _prepare(controller, ids)
    with factory() as db:
        original = db.get(ModelCallAttempt, ids["attempt"])
        second = ModelCallAttempt(
            job_id=original.job_id,
            project_id=original.project_id,
            job_attempt=1,
            dispatch_no=2,
            provider="fake-cli",
            model_id="fake-image",
            catalog_model_id=ids["model"],
            connection_id=ids["connection"],
        )
        db.add(second)
        db.commit()
        second_ids = {**ids, "attempt": second.id}
    with pytest.raises(ProviderAdapterError) as caught:
        _prepare(controller, second_ids)
    assert caught.value.code == "CONCURRENCY_LIMIT"
    controller.execute(first, runner=SuccessRunner(), argv=("fake-cli",))
    assert _prepare(controller, second_ids)


def test_reference_is_copied_without_exposing_source_name(cli_context):
    settings, _factory, controller, ids = cli_context
    source = settings.upload_root / "private-name.png"
    _png(source)
    run_id = _prepare(controller, ids, reference_files=(source,))
    request = json.loads(
        (settings.storage_root / "cli_runs" / run_id / "input/request.json").read_text(
            encoding="utf-8"
        )
    )
    name = request["reference_images"][0]
    assert name.startswith("input/references/") and "private-name" not in name


def test_abandoned_recovery_refuses_active_then_releases_dead(cli_context):
    settings, factory, controller, ids = cli_context
    run_id = _prepare(controller, ids)
    journal_path = settings.storage_root / "cli_runs" / run_id / "journal.json"
    journal = json.loads(journal_path.read_text())
    journal.update(state="RUNNING", controller_pid=123)
    journal_path.write_text(json.dumps(journal))
    with factory() as db:
        db.get(CLIExecutionRun, run_id).state = "RUNNING"
        db.commit()
    assert controller.recover_abandoned(controller_is_active=lambda *_: True) == []
    assert controller.recover_abandoned(controller_is_active=lambda *_: False) == [run_id]
    with factory() as db:
        row = db.get(CLIExecutionRun, run_id)
        assert (row.error_code, row.cleanup_state, row.lease_slot) == (
            "CRASH",
            "RETAINED",
            None,
        )


class ProbeAdapter:
    def __init__(self, fail_at=None):
        self.fail_at = fail_at

    def _result(self, step, **metrics):
        if self.fail_at == step:
            return CLIProbeObservation(status="FAILED", error_code="FAILED", message=step)
        if self.fail_at == f"unknown-{step}":
            return CLIProbeObservation(status="UNKNOWN", message=f"unknown-{step}")
        return CLIProbeObservation(status="PASSED", metrics=metrics)

    def presence(self):
        return self._result("presence")

    def version(self):
        return self._result("version", version="1.2.3")

    def login(self):
        return self._result("login")

    def capability(self):
        return self._result("capability")


@pytest.mark.parametrize(
    ("fail_at", "state"),
    [
        (None, "AVAILABLE"),
        ("presence", "UNAVAILABLE"),
        ("login", "UNAUTHENTICATED"),
        ("capability", "UNSUPPORTED"),
        ("unknown-version", "UNKNOWN"),
    ],
)
def test_probe_state_machine_persists_every_step(cli_context, fail_at, state):
    _settings, factory, _controller, ids = cli_context
    with factory() as db:
        connection = probe_cli_connection(
            db, ids["connection"], ProbeAdapter(fail_at), auto_commit=True
        )
        assert connection.health_state == state and connection.enabled is True
        assert len(list(db.scalars(select(ModelProbe)))) == 4


def test_abandoned_recovery_survives_incomplete_identity_rows(cli_context):
    """A PREPARING row (crash between claim and _mark_running) or a liveness
    probe failure must not abort the whole scan: fresh rows are skipped, rows
    older than the grace window are released."""

    settings, factory, controller, ids = cli_context
    preparing_run = _prepare(controller, ids)
    # Second run needs its own attempt row; reuse the slot-rejection pattern
    # by finishing the first would release the slot, so instead verify the
    # PREPARING row itself: journal exists but has no controller identity.
    from datetime import UTC, datetime, timedelta

    def _probe_raises(_row, _journal):
        raise RuntimeError("CLI controller identity is incomplete")

    # Fresh PREPARING row: skipped this pass, scan continues (no exception).
    assert controller.recover_abandoned(controller_is_active=_probe_raises) == []
    with factory() as db:
        assert db.get(CLIExecutionRun, preparing_run).lease_slot is not None

    # Same row aged beyond the grace window: released.
    with factory() as db:
        row = db.get(CLIExecutionRun, preparing_run)
        row.created_at = datetime.now(UTC) - timedelta(seconds=600)
        db.commit()
    assert controller.recover_abandoned(controller_is_active=_probe_raises) == [
        preparing_run
    ]
    with factory() as db:
        row = db.get(CLIExecutionRun, preparing_run)
        assert (row.state, row.lease_slot, row.error_code) == ("FAILED", None, "CRASH")


@pytest.mark.skipif(os.name == "nt", reason="POSIX /proc-based probe contract")
def test_platform_recovery_entry_uses_posix_probe_when_not_windows():
    """The POSIX liveness fallback must classify dead/live/unknown controllers
    deterministically (pid + optional start time), and raise instead of
    guessing when identity is missing."""

    import subprocess
    import sys

    from app.services import cli_executor

    assert sys.platform != "win32"

    # dead pid: a process that already exited
    exited = subprocess.Popen([sys.executable, "-c", "pass"])
    exited.wait()
    assert cli_executor.posix_controller_is_active(None, {"controller_pid": exited.pid}) is False

    # live pid: this test process
    import os

    assert (
        cli_executor.posix_controller_is_active(None, {"controller_pid": os.getpid()})
        is True
    )

    # missing identity raises instead of guessing
    with pytest.raises(RuntimeError):
        cli_executor.posix_controller_is_active(None, {})


def test_recover_abandoned_cli_runs_selects_platform_probe(monkeypatch):
    """The production entry point must pick a working liveness probe for the
    host platform (previously this function existed but nothing called it)."""

    from app.config import Settings
    from app.services import cli_executor

    seen: dict[str, object] = {}

    class _Recorder:
        def recover_abandoned(self, *, controller_is_active):
            seen["probe"] = controller_is_active
            return []

    monkeypatch.setattr(
        cli_executor,
        "CLIExecutionController",
        lambda settings, factory: _Recorder(),
    )
    recovered = cli_executor.recover_abandoned_cli_runs(
        Settings(), object()  # factory is forwarded, never used by the recorder
    )
    assert recovered == []
    assert seen["probe"] is cli_executor.platform_controller_is_active


class SubsetImagesRunner:
    """Reports SUCCEEDED but omits one of the two registered images."""

    def run(self, *, cwd, **_kwargs):
        output = cwd.parent / "output"
        _png(output / "images" / "out_001.png")
        _png(output / "images" / "out_002.png")
        (output / "result.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "SUCCEEDED",
                    "images": ["output/images/out_001.png"],
                }
            ),
            encoding="utf-8",
        )
        return CLIProcessOutcome(exit_code=0)


class LinkOutputRunner:
    """Repoints the output directory at a link outside the run directory."""

    def run(self, *, cwd, **_kwargs):
        import os
        import shutil
        import tempfile

        output = cwd.parent / "output"
        if output.exists():
            shutil.rmtree(output)
        external = Path(tempfile.mkdtemp())
        _png(external / "images" / "out_001.png")
        (external / "result.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "SUCCEEDED",
                    "images": ["output/images/out_001.png"],
                }
            ),
            encoding="utf-8",
        )
        os.symlink(external, output)
        return CLIProcessOutcome(exit_code=0)


def test_partial_image_set_is_rejected_not_adopted(cli_context):
    settings, factory, controller, ids = cli_context
    run_id = _prepare(
        controller,
        ids,
        output_images=(
            "output/images/out_001.png",
            "output/images/out_002.png",
        ),
        max_images=2,
    )
    with pytest.raises(ProviderAdapterError) as caught:
        controller.execute(run_id, runner=SubsetImagesRunner(), argv=("fake-cli",))
    assert caught.value.code == "PARTIAL_OUTPUT"
    with factory() as db:
        row = db.get(CLIExecutionRun, run_id)
        assert (row.state, row.cleanup_state) == ("FAILED", "RETAINED")


@pytest.mark.skipif(
    os.name == "nt" and not _symlinks_creatable(),
    reason="requires symlink creation privilege (developer mode/admin on Windows)",
)
def test_output_directory_link_chain_is_rejected(cli_context):
    settings, factory, controller, ids = cli_context
    run_id = _prepare(controller, ids)
    with pytest.raises(ProviderAdapterError) as caught:
        controller.execute(run_id, runner=LinkOutputRunner(), argv=("fake-cli",))
    assert caught.value.code == "INVALID_OUTPUT"
    assert (settings.storage_root / "cli_runs" / run_id).exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX /proc start-ticks contract")
def test_posix_probe_detects_recycled_pid(tmp_path):
    from app.services import cli_executor
    """A PID now owned by a different process (different /proc starttime)
    reports the controller as dead instead of holding the lease slot."""

    journal = {"controller_pid": os.getpid()}
    assert cli_executor.posix_controller_is_active(None, journal) is True

    ticks = cli_executor._posix_start_ticks(os.getpid())
    assert ticks is not None  # /proc available on this host

    recycled = dict(journal, controller_start_ticks=ticks + 100000)
    assert cli_executor.posix_controller_is_active(None, recycled) is False

    matching = dict(journal, controller_start_ticks=ticks)
    assert cli_executor.posix_controller_is_active(None, matching) is True

    # Unreadable /proc degrades to the pid-only probe, never a false dead.
    unreadable = dict(journal, controller_start_ticks=ticks, controller_pid=99999999)
    assert cli_executor.posix_controller_is_active(None, unreadable) is False
