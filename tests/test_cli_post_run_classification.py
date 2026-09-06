"""Regression: post-run CLI failures keep honest classifications.

Three audit findings, one family. A successful paid CLI run used to be
discarded under misleading terminal labels whenever something failed after
the child had already finished:

- an adapter artifact-adoption OSError (ENOSPC, EACCES, a directory planted
  at the registered target) escaped ``AntigravityArtifactRunner.run`` /
  ``GrokBuildArtifactRunner.run`` and hit ``execute``'s CRASH fallback;
- controller-side infrastructure failures (diagnostics write, result read,
  COMPLETED finalize commit) collapsed to non-retryable CRASH while the
  equally post-run teardown ``TimeoutError`` had long been retryable;
- a Grok preflight ``inspect`` run dying with a nonzero exit (AV/indexer
  file lock, self-update glitch) stamped a terminal UNSUPPORTED capability
  verdict, permanently failing the job on a transient glitch.

Also covered: ``result.json`` with a UTF-8 BOM or non-UTF-8 bytes (used to
be CRASH via an uncaught UnicodeDecodeError), and the Windows runner's
executable resolution vanishing mid-launch (bare FileNotFoundError).
"""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.database import Base
from app.model_adapters.antigravity_cli import AntigravityArtifactRunner
from app.model_adapters.base import ProviderAdapterError
from app.model_adapters.grok_build_cli import GrokBuildArtifactRunner
from app.models import (
    AIModel,
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
    CLIProcessOutcome,
)
from app.services.cli_process_windows import WindowsJobCLIProcessRunner


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


def _write_result(path: Path, images=("output/images/out_001.png",), *, raw: bytes | None = None):
    path.parent.mkdir(parents=True, exist_ok=True)
    if raw is not None:
        path.write_bytes(raw)
        return
    path.write_text(
        json.dumps(
            {"schema_version": 1, "status": "SUCCEEDED", "images": list(images)}
        ),
        encoding="utf-8",
    )


class StaticOutcomeRunner:
    """Return one fixed outcome regardless of argv (antigravity/grok runners wrap it)."""

    def __init__(self, outcome: CLIProcessOutcome) -> None:
        self._outcome = outcome

    def run(self, **_kwargs) -> CLIProcessOutcome:
        return self._outcome


class SequencedOutcomeRunner:
    """Hand out queued outcomes in call order (grok runs inspect, then media)."""

    def __init__(self, outcomes: list[CLIProcessOutcome]) -> None:
        self._outcomes = list(outcomes)

    def run(self, **_kwargs) -> CLIProcessOutcome:
        return self._outcomes.pop(0)


class ResultWriterRunner:
    """Successful child that writes the registered output image and result.json."""

    def __init__(self, *, result_raw: bytes | None = None) -> None:
        self._result_raw = result_raw

    def run(self, *, cwd, **_kwargs):
        output = cwd.parent / "output"
        _png(output / "images" / "out_001.png")
        _write_result(output / "result.json", raw=self._result_raw)
        return CLIProcessOutcome(exit_code=0, stdout=b"{}")


def test_antigravity_adopt_oserror_is_invalid_output(tmp_path):
    """A directory planted at the registered target must not escape as CRASH."""

    run_directory = tmp_path / "run-1"
    workspace = run_directory / "workspace"
    (run_directory / "input").mkdir(parents=True)
    (run_directory / "output" / "images").mkdir(parents=True)
    (run_directory / "output" / "images" / "out_001.png").mkdir()
    (run_directory / "input" / "request.json").write_text(
        json.dumps({"output_spec": {"images": ["output/images/out_001.png"]}}),
        encoding="utf-8",
    )
    brain = workspace / ".agy-home" / ".gemini" / "antigravity-cli" / "brain"
    _png(brain / "artifact.png")
    outcome = CLIProcessOutcome(
        exit_code=0,
        stdout=json.dumps({"status": "SUCCESS"}).encode("utf-8"),
    )
    runner = AntigravityArtifactRunner(StaticOutcomeRunner(outcome), Settings())
    result = runner.run(
        cwd=workspace,
        timeout_seconds=30,
        environment={},
        argv=("agy",),
        cancel_requested=lambda: False,
    )
    assert result.error_code == "INVALID_OUTPUT"
    assert result.error_message == "Antigravity CLI 产物无法读取或落盘"


def test_grok_adopt_oserror_is_invalid_output(tmp_path):
    run_directory = tmp_path / "run-2"
    workspace = run_directory / "workspace"
    workspace.mkdir(parents=True)
    (run_directory / "input").mkdir(parents=True)
    (run_directory / "input" / "request.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "operation": "image_generate",
                "prompt": "一页漫画",
                "parameters": {},
                "output_spec": {"images": ["output/images/out_001.png"]},
            }
        ),
        encoding="utf-8",
    )
    grok_home = tmp_path / "grok-home"
    image = grok_home / "sessions" / "ns-1" / "run-2" / "images" / "1.jpg"
    _png(image)
    envelope = b"\n".join(
        [
            json.dumps(
                {"type": "tool_call", "toolCallId": "c1", "toolName": "image_gen"}
            ).encode("utf-8"),
            json.dumps(
                {
                    "type": "tool_call_update",
                    "status": "completed",
                    "toolCallId": "c1",
                    "rawOutput": {
                        "type": "ImageGen",
                        "path": str(image.resolve()),
                        "filename": "1.jpg",
                        "session_folder": "images",
                    },
                }
            ).encode("utf-8"),
            json.dumps({"type": "end"}).encode("utf-8"),
        ]
    )
    outcome = CLIProcessOutcome(exit_code=0, stdout=envelope)
    runner = GrokBuildArtifactRunner(
        SequencedOutcomeRunner(
            [
                CLIProcessOutcome(exit_code=0, stdout=b'{"hooks":[]}'),
                outcome,
            ]
        ),
        Settings(),
        operation="image_generate",
    )
    result = runner.run(
        cwd=workspace,
        timeout_seconds=30,
        environment={"GROK_HOME": str(grok_home)},
        argv=("grok",),
        cancel_requested=lambda: False,
    )
    assert result.error_code == "INVALID_OUTPUT"
    assert result.error_message == "Grok Build CLI 产物无法读取或落盘"


def test_grok_inspect_transient_exit_is_upstream(tmp_path):
    """Nonzero preflight exit stays fail-closed for this dispatch but retryable."""

    workspace = tmp_path / "run-3" / "workspace"
    workspace.mkdir(parents=True)
    outcome = CLIProcessOutcome(exit_code=1, stdout=b"", stderr=b"")
    runner = GrokBuildArtifactRunner(
        StaticOutcomeRunner(outcome), Settings(), operation="image_generate"
    )
    result = runner.run(
        cwd=workspace,
        timeout_seconds=30,
        environment={},
        argv=("grok",),
        cancel_requested=lambda: False,
    )
    assert result.error_code == "UPSTREAM"
    assert "钩子隔离" in result.error_message


def test_controller_post_run_diagnostics_oserror_is_upstream(cli_context, monkeypatch):
    settings, factory, controller, ids = cli_context
    run_id = _prepare(controller, ids)

    def broken_diagnostics(*_args, **_kwargs):
        raise OSError("stdout.log could not be written")

    monkeypatch.setattr(controller, "_diagnostics", broken_diagnostics)
    with pytest.raises(ProviderAdapterError) as raised:
        controller.execute(run_id, runner=ResultWriterRunner(), argv=("fake-cli",))
    assert raised.value.code == "UPSTREAM"
    assert raised.value.retryable is True
    with factory() as db:
        row = db.get(CLIExecutionRun, run_id)
        assert (row.state, row.error_code) == ("FAILED", "UPSTREAM")


def test_controller_completed_finalize_db_failure_is_upstream(cli_context, monkeypatch):
    settings, factory, controller, ids = cli_context
    run_id = _prepare(controller, ids)
    original_finish = controller._finish

    def flaky_finish(run_id_, *args, **kwargs):
        if kwargs.get("state") == "COMPLETED":
            raise SQLAlchemyError("commit raced a lock timeout")
        return original_finish(run_id_, *args, **kwargs)

    monkeypatch.setattr(controller, "_finish", flaky_finish)
    with pytest.raises(ProviderAdapterError) as raised:
        controller.execute(run_id, runner=ResultWriterRunner(), argv=("fake-cli",))
    assert raised.value.code == "UPSTREAM"
    assert raised.value.retryable is True
    with factory() as db:
        row = db.get(CLIExecutionRun, run_id)
        assert (row.state, row.error_code) == ("FAILED", "UPSTREAM")


def test_result_json_with_utf8_bom_is_accepted(cli_context):
    settings, factory, controller, ids = cli_context
    run_id = _prepare(controller, ids)
    result = controller.execute(
        run_id,
        runner=ResultWriterRunner(result_raw=b"\xef\xbb\xbf" + json.dumps(
            {"schema_version": 1, "status": "SUCCEEDED", "images": ["output/images/out_001.png"]}
        ).encode("utf-8")),
        argv=("fake-cli",),
    )
    assert result.images


def test_result_json_non_utf8_bytes_is_invalid_output(cli_context):
    settings, factory, controller, ids = cli_context
    run_id = _prepare(controller, ids)
    with pytest.raises(ProviderAdapterError) as raised:
        controller.execute(
            run_id,
            runner=ResultWriterRunner(result_raw=b"\xff\xfe\x00not-json"),
            argv=("fake-cli",),
        )
    assert raised.value.code == "INVALID_OUTPUT"
    assert raised.value.retryable is False


def test_windows_runner_vanishing_executable_is_unavailable(tmp_path, monkeypatch):
    executable = tmp_path / "codex.exe"
    executable.write_bytes(b"MZ")
    resolved = executable.resolve()
    executable.unlink()
    with pytest.raises(ProviderAdapterError) as raised:
        WindowsJobCLIProcessRunner._resolve(str(resolved), {})
    assert raised.value.code == "UNAVAILABLE"

    # Same TOCTOU on the PATH-discovery branch: which() succeeds, the file is
    # gone before resolve(strict=True).
    monkeypatch.setattr(
        "app.services.cli_process_windows.shutil.which", lambda value, path=None: str(resolved)
    )
    with pytest.raises(ProviderAdapterError) as raised_which:
        WindowsJobCLIProcessRunner._resolve("codex", {})
    assert raised_which.value.code == "UNAVAILABLE"
