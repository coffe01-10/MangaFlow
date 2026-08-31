"""Offline contract tests for the provider-neutral CLI controller."""

import json
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
)
from app.services.cli_probe import CLIProbeObservation, probe_cli_connection


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
