"""Red-team remediation regressions for the CLI channel (issues #140/#141/#142).

#140: a failing controller-side cancel probe must not kill a paid run as CRASH.
#141: image adoption must fully decode — a header-valid truncated file fails.
#142: a teardown-phase timeout is a retryable TIMEOUT, not a terminal CRASH.
"""

import json
import logging
from dataclasses import replace
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.database import Base
from app.model_adapters.antigravity_cli import (
    AntigravityCLIImageAdapter,
    AntigravityCLIRuntime,
)
from app.model_adapters.antigravity_cli import _InvocationContext as _AgContext
from app.model_adapters.base import ProviderAdapterError
from app.model_adapters.codex_cli import CodexCLIImageAdapter, CodexCLIRuntime
from app.model_adapters.codex_cli import _InvocationContext as _CodexContext
from app.model_adapters.grok_build_cli import (
    GrokBuildCLIImageAdapter,
    GrokBuildCLIRuntime,
)
from app.model_adapters.grok_build_cli import _InvocationContext as _GrokContext
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
from app.services.media import inspect_upload_image

_PROBE_LOG_MESSAGE = "Cancel probe failed; treating job as not cancelled"


def _broken_session_factory():
    raise OperationalError("SELECT 1", {}, RuntimeError("stale pooled connection"))


_CHANNELS = {
    "codex": (
        CodexCLIImageAdapter,
        CodexCLIRuntime,
        _CodexContext,
    ),
    "antigravity": (
        AntigravityCLIImageAdapter,
        AntigravityCLIRuntime,
        _AgContext,
    ),
    "grok": (
        GrokBuildCLIImageAdapter,
        GrokBuildCLIRuntime,
        _GrokContext,
    ),
}


def _cancel_probe(channel: str, session_factory, job_id: str = "job-1"):
    adapter_cls, runtime_cls, context_cls = _CHANNELS[channel]
    adapter = adapter_cls(
        runtime_cls(
            settings=Settings(),
            connection_id="conn",
            catalog_model_id="model",
            provider_model_id="model",
            session_factory=session_factory,
        )
    )
    context = context_cls(
        job_id=job_id, model_call_attempt_id="attempt-1", lease_owner=None
    )
    return lambda: adapter._cancel_requested(context)


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
        project = Project(name="Red Team CLI 离线项目")
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


def _jpeg_bytes(width: int = 96, height: int = 128) -> bytes:
    """A JPEG with substantial entropy data (noise), unlike a solid-color
    image whose scan data is a few bytes."""

    buffer = BytesIO()
    Image.effect_noise((width, height), 40).convert("RGB").save(
        buffer, format="JPEG", quality=85
    )
    return buffer.getvalue()


def _truncated(payload: bytes, keep_ratio: float) -> bytes:
    return payload[: int(len(payload) * keep_ratio)]


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


def _write_result_json(output: Path, images: list[str]) -> None:
    (output / "result.json").write_text(
        json.dumps({"schema_version": 1, "status": "SUCCEEDED", "images": images}),
        encoding="utf-8",
    )


class _SlowSuccessRunner:
    """Fake CLI that polls cancellation while "generating", then succeeds."""

    def __init__(self, polls: int = 3):
        self.polls = polls

    def run(self, *, cwd, cancel_requested, **_kwargs):
        output = cwd.parent / "output"
        _png(output / "images" / "out_001.png")
        _write_result_json(output, ["output/images/out_001.png"])
        for _ in range(self.polls):
            # The guarded probe must report "not cancelled" instead of
            # raising out of the supervision loop.
            assert cancel_requested() is False
        return CLIProcessOutcome(exit_code=0)


# ---------------------------------------------------------------------- #140


@pytest.mark.parametrize("channel", ["codex", "antigravity", "grok"])
def test_cancel_probe_db_failure_reports_not_cancelled_and_logs(channel, caplog):
    probe = _cancel_probe(channel, _broken_session_factory)
    with caplog.at_level(logging.ERROR):
        assert probe() is False
    assert any(record.message == _PROBE_LOG_MESSAGE for record in caplog.records)


def test_cancel_probe_still_detects_a_cancelled_job(cli_context):
    _settings, factory, _controller, ids = cli_context
    with factory() as db:
        db.get(GenerationJob, ids["job"]).status = "CANCELLED"
        db.commit()
    probe = _cancel_probe("codex", factory, job_id=ids["job"])
    assert probe() is True


def test_cancel_probe_db_failure_keeps_run_alive_and_adopts_result(cli_context, caplog):
    """Issue #140 chain: one transient DB error during polling must let the
    executor wait for the child, adopt the result and avoid CRASH."""

    _settings, factory, controller, ids = cli_context
    run_id = _prepare(controller, ids)
    probe_wrapper = _cancel_probe("codex", _broken_session_factory, job_id=ids["job"])
    probe_calls = []

    def probe():
        probe_calls.append(1)
        return probe_wrapper()

    with caplog.at_level(logging.ERROR):
        result = controller.execute(
            run_id,
            runner=_SlowSuccessRunner(polls=3),
            argv=("fake-cli",),
            cancel_requested=probe,
        )

    assert result.images
    # Three in-run polls plus the controller's post-run re-check.
    assert len(probe_calls) == 4
    with factory() as db:
        row = db.get(CLIExecutionRun, run_id)
        assert (row.state, row.error_code, row.lease_slot) == ("COMPLETED", None, None)
    assert any(record.message == _PROBE_LOG_MESSAGE for record in caplog.records)


# ---------------------------------------------------------------------- #141


def test_truncated_jpeg_passes_verify_but_fails_inspect(tmp_path):
    """A valid JPEG header with cut entropy data passes Pillow's verify() and
    must be rejected by the full decode added to inspect_upload_image."""

    payload = _truncated(_jpeg_bytes(), keep_ratio=0.6)
    path = tmp_path / "truncated.jpg"
    path.write_bytes(payload)

    with Image.open(path) as image:
        image.verify()  # header-only check: the truncation is invisible here

    with pytest.raises(ValueError, match="损坏"):
        inspect_upload_image(path, max_pixels=1_000_000, max_side=4096)


def test_truncated_png_fails_inspect(tmp_path):
    buffer = BytesIO()
    Image.new("RGB", (96, 128), "teal").save(buffer, format="PNG")
    path = tmp_path / "truncated.png"
    path.write_bytes(_truncated(buffer.getvalue(), keep_ratio=0.5))

    with pytest.raises(ValueError):
        inspect_upload_image(path, max_pixels=1_000_000, max_side=4096)


def test_intact_images_pass_inspect_with_dimensions_and_mime(tmp_path):
    jpeg = tmp_path / "intact.jpg"
    jpeg.write_bytes(_jpeg_bytes(96, 128))
    assert inspect_upload_image(jpeg, max_pixels=1_000_000, max_side=4096) == (
        96,
        128,
        "image/jpeg",
        ".jpg",
    )

    # Floor boundary: the shortest accepted side equals _MIN_IMAGE_SIDE.
    small = tmp_path / "boundary.png"
    _png(small)
    assert inspect_upload_image(small, max_pixels=1_000_000, max_side=4096) == (
        8,
        8,
        "image/png",
        ".png",
    )


def test_degenerate_small_image_is_rejected(tmp_path):
    path = tmp_path / "stub.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (4, 200), "gray").save(path, format="PNG")
    with pytest.raises(ValueError, match="尺寸过小"):
        inspect_upload_image(path, max_pixels=1_000_000, max_side=4096)


class _TruncatedOutputRunner:
    """Reports SUCCEEDED with a truncated JPEG body on disk."""

    def run(self, *, cwd, **_kwargs):
        output = cwd.parent / "output"
        output.joinpath("images").mkdir(parents=True, exist_ok=True)
        (output / "images" / "out_001.jpg").write_bytes(
            _truncated(_jpeg_bytes(), keep_ratio=0.6)
        )
        _write_result_json(output, ["output/images/out_001.jpg"])
        return CLIProcessOutcome(exit_code=0)


def test_adoption_rejects_truncated_output_instead_of_persisting_ready(cli_context):
    settings, factory, controller, ids = cli_context
    run_id = _prepare(controller, ids, output_images=("output/images/out_001.jpg",))

    with pytest.raises(ProviderAdapterError) as caught:
        controller.execute(run_id, runner=_TruncatedOutputRunner(), argv=("fake-cli",))

    assert caught.value.code == "INVALID_OUTPUT"
    with factory() as db:
        row = db.get(CLIExecutionRun, run_id)
        assert (row.state, row.error_code, row.cleanup_state) == (
            "FAILED",
            "INVALID_OUTPUT",
            "RETAINED",
        )
    assert (settings.storage_root / "cli_runs" / run_id).exists()


# ---------------------------------------------------------------------- #142


class _TeardownOverrunRunner:
    """Completes generation, then hangs in teardown like a Job Object that
    refuses to die (cli_process_windows raises TimeoutError there)."""

    def run(self, *, cwd, **_kwargs):
        output = cwd.parent / "output"
        _png(output / "images" / "out_001.png")
        _write_result_json(output, ["output/images/out_001.png"])
        raise TimeoutError("CLI Job Object did not terminate")


def test_teardown_timeout_is_retryable_timeout_not_terminal_crash(cli_context):
    settings, factory, controller, ids = cli_context
    run_id = _prepare(controller, ids)

    with pytest.raises(ProviderAdapterError) as caught:
        controller.execute(run_id, runner=_TeardownOverrunRunner(), argv=("fake-cli",))

    assert caught.value.code == "TIMEOUT"
    assert caught.value.retryable is True
    with factory() as db:
        row = db.get(CLIExecutionRun, run_id)
        assert (row.state, row.error_code, row.lease_slot) == (
            "FAILED",
            "TIMEOUT",
            None,
        )
    # TIMEOUT is not a retained code: the run directory is cleaned, and a
    # locked directory only marks cleanup FAILED instead of masking the error.
    assert not (settings.storage_root / "cli_runs" / run_id).exists()
