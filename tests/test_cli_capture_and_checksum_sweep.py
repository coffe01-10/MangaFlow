"""Red-team sweep regressions for CLI capture, checksums, and timeouts.

Covers the verified defects from issues #241 and #195:

* #241-1 — the Windows runner's 64 KiB capture cap silently destroyed
  successful runs with larger stdout; the drain now spills past the cap and
  flags lossy captures, grok maps the flag to retryable UPSTREAM while
  keeping the billed session on disk, and >64 KiB streaming payloads are
  adopted normally when fully captured.
* #241-3 — the child's handle inheritance is restricted to exactly the three
  std handles via PROC_THREAD_ATTRIBUTE_HANDLE_LIST.
* #195 — the run row's stdout/stderr checksums must verify against the logs
  the controller actually persists, not against a raw stream that is
  replaced/zeroed before persistence.
* #241-4 — Settings rejects cli_run_timeout_seconds > job_timeout_seconds.

Everything stays offline: no real child process is ever spawned (the runner
is exercised against faked kernel primitives like test_cli_process_windows).
"""

import hashlib
import json
import os
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

import app.services.cli_process_windows as cli_process_windows
from app.config import Settings
from app.model_adapters.base import ImageRequest, ProviderAdapterError
from app.model_adapters.grok_build_cli import GrokBuildCLIImageAdapter, GrokBuildCLIRuntime
from app.models import (
    AIModel,
    CLIExecutionRun,
    GenerationJob,
    ModelCallAttempt,
    Project,
    ProviderConnection,
    ProviderProfile,
)
from app.services.cli_executor import CLIExecutionController, _sanitize_diagnostic

# --------------------------------------------------------------------- #241-1
# _OutputDrain: full retention past the memory cap, flag past the spill cap.


def _drain_file_payload(payload: bytes, *, source: Path, **drain_kwargs):
    # The file must outlive the helper on Windows (the drain thread holds its
    # fd open until finish()); pytest's tmp_path cleanup removes it later.
    source.write_bytes(payload)
    drain = cli_process_windows._OutputDrain(os.open(source, os.O_RDONLY), **drain_kwargs)
    drain.start()
    return drain


def test_output_drain_retains_the_full_stream_past_the_memory_cap(tmp_path):
    payload = b"0123456789abcdef" * 4 * 1024 + b"tail-marker"  # 64 KiB + 11 bytes
    assert len(payload) > cli_process_windows._CAPTURE_LIMIT

    drain = _drain_file_payload(payload, source=tmp_path / "stream.bin")
    data, checksum = drain.finish()

    assert data == payload, "a successful run's stdout must not be silently truncated"
    assert checksum == hashlib.sha256(payload).hexdigest()
    assert drain.truncated is False


def test_output_drain_flags_truncation_only_past_the_spill_cap(tmp_path):
    payload = b"z" * (200 * 1024)
    spill_limit = 96 * 1024

    drain = _drain_file_payload(payload, source=tmp_path / "stream.bin", spill_limit=spill_limit)
    data, checksum = drain.finish()

    assert data == payload[:spill_limit]
    assert len(data) == spill_limit
    # The digest still covers the WHOLE stream, so the mismatch between the
    # retained prefix and the checksum is observable, not silent.
    assert checksum == hashlib.sha256(payload).hexdigest()
    assert drain.truncated is True


def test_output_drain_keeps_small_streams_in_memory_without_a_spill_file(tmp_path):
    drain = _drain_file_payload(b"tiny diagnostic stream", source=tmp_path / "stream.bin")
    data, checksum = drain.finish()

    assert data == b"tiny diagnostic stream"
    assert checksum == hashlib.sha256(b"tiny diagnostic stream").hexdigest()
    assert drain.truncated is False
    # Pins that ordinary (sub-cap) runs still never touch the disk: the
    # spooled sink must not have rolled into a temp file.
    assert drain._sink._rolled is False


# --------------------------------------------------------------------- #241-3
# The child inherits exactly the three std handles, nothing else.


class _FakeKernelAPI:
    """Kernel32 stand-in: int handles, every checked call succeeds."""

    def CreateJobObjectW(self, *_args):
        return 1001

    def SetInformationJobObject(self, *_args):
        return 1

    def AssignProcessToJobObject(self, *_args):
        return 1

    def ResumeThread(self, _thread):
        return 1  # anything but 0xFFFFFFFF

    def CloseHandle(self, _handle):
        return 1

    def TerminateJobObject(self, *_args):
        return 1

    def WaitForSingleObject(self, _process, _timeout_ms):
        return cli_process_windows._WAIT_OBJECT_0

    def GetExitCodeProcess(self, _process, _code):
        return 1

    def GetCurrentProcess(self):
        return 4200

    def GetProcessTimes(self, _handle, *_times):
        return 1


def test_windows_runner_restricts_inheritance_to_the_three_std_handles(
    tmp_path, monkeypatch
):
    if os.name != "nt":
        pytest.skip("Windows Job Object runtime path")

    executable = tmp_path / "fake-cli.exe"
    executable.write_bytes(b"MZ fake executable")
    run_directory = tmp_path / "run"
    workspace = run_directory / "workspace"
    workspace.mkdir(parents=True)
    (run_directory / "journal.json").write_text(
        json.dumps({"state": "RUNNING", "token": "tok"}), encoding="utf-8"
    )
    captured = {}

    def fake_create_process(*args, **_kwargs):
        captured["inherit"] = args[4]
        captured["startupinfo"] = args[8]
        return (4242, 5252, 9999, 3333)

    monkeypatch.setattr(cli_process_windows, "_kernel", lambda: _FakeKernelAPI())
    monkeypatch.setattr("_winapi.CreateProcess", fake_create_process)
    active = {"count": 1}

    def fake_active_processes(_api, _job):
        value = active["count"]
        active["count"] = 0
        return value

    monkeypatch.setattr(cli_process_windows, "_active_processes", fake_active_processes)

    outcome = cli_process_windows.WindowsJobCLIProcessRunner().run(
        argv=(str(executable), "--generate"),
        cwd=workspace,
        environment={"PATH": os.environ.get("PATH", "")},
        timeout_seconds=5,
        cancel_requested=lambda: False,
    )

    startup = captured["startupinfo"]
    handle_list = startup.lpAttributeList["handle_list"]
    # PROC_THREAD_ATTRIBUTE_HANDLE_LIST restricting inheritance to exactly
    # the three std handles: unrelated inheritable handles of this process
    # (another run's pipe write end) can no longer leak into the CLI child
    # and block that pipe's EOF.
    assert captured["inherit"] is True
    assert handle_list == [startup.hStdInput, startup.hStdOutput, startup.hStdError]
    assert len(set(handle_list)) == 3, "duplicate handles make CreateProcess fail"
    assert isinstance(outcome, cli_process_windows.WindowsCLIProcessOutcome)
    assert outcome.stdout_truncated is False and outcome.stderr_truncated is False


# --------------------------------------------------------------------- #241-4
# Settings cross-field geometry for the CLI timeout.


def test_cli_run_timeout_above_job_timeout_is_rejected():
    with pytest.raises(ValidationError) as raised:
        Settings(job_timeout_seconds=120, cli_run_timeout_seconds=900)
    assert "cli_run_timeout_seconds" in str(raised.value)
    assert "job_timeout_seconds" in str(raised.value)


def test_cli_run_timeout_equal_to_job_timeout_is_accepted():
    settings = Settings(job_timeout_seconds=120, cli_run_timeout_seconds=120)
    assert settings.cli_run_timeout_seconds == settings.job_timeout_seconds


# ------------------------------------------------- #241-1 / #195 at the grok layer


def _jpeg_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (8, 8), "orange").save(output, format="JPEG")
    return output.getvalue()


def _png_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (8, 8), "teal").save(output, format="PNG")
    return output.getvalue()


def _grok_rows(db_session, tmp_path):
    factory = sessionmaker(
        bind=db_session.get_bind(), autoflush=False, expire_on_commit=False
    )
    settings = Settings(
        storage_root=tmp_path / "storage",
        upload_root=tmp_path / "uploads",
        cli_run_timeout_seconds=30,
    )
    settings.ensure_directories()
    project = Project(name="捕获与校验和回归项目")
    profile = ProviderProfile(
        preset_key="grok-build-cli", name="Grok Build CLI", enabled=True, built_in=True
    )
    db_session.add_all([project, profile])
    db_session.flush()
    connection = ProviderConnection(
        provider_id=profile.id,
        name="默认连接",
        protocol="CLI_GROK_BUILD",
        base_url="cli://grok-build",
        enabled=True,
        health_state="AVAILABLE",
        nonsecret_config={"cli_executable": "grok"},
    )
    db_session.add(connection)
    db_session.flush()
    model = AIModel(
        connection_id=connection.id,
        provider_model_id="grok-build-imagine",
        display_name="Grok Build Imagine",
        model_type="IMAGE",
        input_modalities=["TEXT", "IMAGE"],
        output_modalities=["IMAGE"],
        operations=["image_generate", "image_edit"],
        capabilities={"resolutions": ["1K"], "max_reference_images": 5},
        confidence="DECLARED",
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
    db_session.add_all([model, job])
    db_session.flush()
    attempt = ModelCallAttempt(
        job_id=job.id,
        project_id=job.project_id,
        job_attempt=1,
        dispatch_no=1,
        provider="grok-build-cli",
        model_id=model.provider_model_id,
        catalog_model_id=model.id,
        connection_id=connection.id,
    )
    db_session.add(attempt)
    db_session.commit()
    return factory, settings, connection, model, job, attempt


def _adapter(factory, settings, connection, model, runner):
    return GrokBuildCLIImageAdapter(
        GrokBuildCLIRuntime(
            settings=settings,
            connection_id=connection.id,
            catalog_model_id=model.id,
            provider_model_id=model.provider_model_id,
            session_factory=factory,
        ),
        controller=CLIExecutionController(settings, factory),
        runner_factory=lambda: runner,
        executable_resolver=lambda _value: "C:/tools/grok.exe",
    )


def _invoke(db_session, monkeypatch, tmp_path, runner):
    """Wire one grok dispatch against the SQLite fixture and the fake runner."""

    factory, settings, connection, model, job, attempt = _grok_rows(db_session, tmp_path)
    grok_home = tmp_path / "grok-home"
    monkeypatch.setenv("GROK_HOME", str(grok_home))
    adapter = _adapter(factory, settings, connection, model, runner)
    adapter.bind_execution_context(
        job_id=job.id,
        model_call_attempt_id=attempt.id,
        lease_owner=None,
    )
    return adapter, grok_home


def _session_image(environment: dict, run_id: str) -> Path:
    image = (
        Path(environment["GROK_HOME"])
        / "sessions"
        / "encoded-workspace"
        / run_id
        / "images"
        / "1.jpg"
    )
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(_jpeg_bytes())
    return image


def _streaming_events(run_id: str, image_path: Path, *, tool: str = "image_gen") -> bytes:
    output_type = "ImageEdit" if tool == "image_edit" else "ImageGen"
    events = [
        {"type": "tool_call", "toolCallId": "call-1", "toolName": tool},
        {
            "type": "tool_call_update",
            "toolCallId": "call-1",
            "status": "completed",
            "rawOutput": {
                "output": {
                    "type": output_type,
                    "path": str(image_path),
                    "filename": "1.jpg",
                    "session_folder": "images",
                }
            },
        },
        {"type": "end", "stopReason": "end_turn", "sessionId": run_id,
         "usage": {"input_tokens": 7}},
    ]
    return "\n".join(json.dumps(event) for event in events).encode()


class _PaddedSuccessRunner:
    """Successful media run whose stdout is far past the 64 KiB capture cap."""

    def run(self, *, argv, cwd: Path, environment, **_kwargs):
        if argv[1:] == ("inspect", "--json"):
            return cli_process_windows.WindowsCLIProcessOutcome(0, stdout=b'{"hooks":[]}')
        run_id = argv[argv.index("--session-id") + 1]
        image = _session_image(environment, run_id)
        payload = _streaming_events(run_id, image)
        # Whitespace-only lines are skipped by the parser but counted: pad
        # with 1 KiB lines so the total stays well past _CAPTURE_LIMIT while
        # remaining far below the 1 MiB payload bound and the 5000-line cap.
        padding = (b" " * 1023 + b"\n") * 80
        return cli_process_windows.WindowsCLIProcessOutcome(
            0, stdout=padding + payload
        )


class _TruncatedCaptureRunner:
    """Media run whose capture was truncated past the spill cap by us."""

    def __init__(self, *, inspect_truncated=False):
        self.inspect_truncated = inspect_truncated
        self.calls = 0

    def run(self, *, argv, cwd: Path, environment, **_kwargs):
        self.calls += 1
        if argv[1:] == ("inspect", "--json"):
            if self.inspect_truncated:
                return cli_process_windows.WindowsCLIProcessOutcome(
                    0, stdout=b'{"hooks":[]' + b" " * 9000, stdout_truncated=True
                )
            return cli_process_windows.WindowsCLIProcessOutcome(0, stdout=b'{"hooks":[]}')
        run_id = argv[argv.index("--session-id") + 1]
        _session_image(environment, run_id)  # the billed image exists on disk
        return cli_process_windows.WindowsCLIProcessOutcome(
            0,
            stdout=b'{"type":"tool_call","toolCallId":"call-1",',
            stdout_truncated=True,
        )


class _WrongToolRunner:
    """Valid-stream run whose completed tool is unauthorized (INVALID_OUTPUT)."""

    def run(self, *, argv, cwd: Path, environment, **_kwargs):
        if argv[1:] == ("inspect", "--json"):
            return cli_process_windows.WindowsCLIProcessOutcome(0, stdout=b'{"hooks":[]}')
        run_id = argv[argv.index("--session-id") + 1]
        image = _session_image(environment, run_id)
        payload = _streaming_events(run_id, image, tool="image_edit")
        return cli_process_windows.WindowsCLIProcessOutcome(0, stdout=payload)


class _CleanSuccessRunner:
    """Minimal successful generate run (no reference images)."""

    def run(self, *, argv, cwd: Path, environment, **_kwargs):
        if argv[1:] == ("inspect", "--json"):
            return cli_process_windows.WindowsCLIProcessOutcome(0, stdout=b'{"hooks":[]}')
        run_id = argv[argv.index("--session-id") + 1]
        image = _session_image(environment, run_id)
        return cli_process_windows.WindowsCLIProcessOutcome(
            0, stdout=_streaming_events(run_id, image)
        )


def test_grok_adopts_a_successful_run_whose_stdout_exceeds_the_capture_cap(
    db_session, tmp_path, monkeypatch
):
    runner = _PaddedSuccessRunner()
    adapter, _grok_home = _invoke(db_session, monkeypatch, tmp_path, runner)

    response = adapter.generate_asset(ImageRequest(prompt="超大输出场景"))

    assert response.images == (_jpeg_bytes(),)
    run = db_session.scalar(select(CLIExecutionRun))
    assert (run.state, run.error_code) == ("COMPLETED", None)


def test_grok_truncated_capture_is_retryable_upstream_and_keeps_the_billed_session(
    db_session, tmp_path, monkeypatch
):
    runner = _TruncatedCaptureRunner()
    adapter, grok_home = _invoke(db_session, monkeypatch, tmp_path, runner)

    with pytest.raises(ProviderAdapterError) as caught:
        adapter.generate_asset(ImageRequest(prompt="截断场景"))

    # Our capture layer lost the stream: retryable UPSTREAM, never a terminal
    # INVALID_OUTPUT/UNKNOWN_RESULT.
    assert caught.value.code == "UPSTREAM"
    assert caught.value.retryable is True
    assert "截断" in caught.value.user_message
    run = db_session.scalar(select(CLIExecutionRun))
    assert (run.state, run.error_code, run.cleanup_state) == ("FAILED", "UPSTREAM", "CLEANED")
    # The billed session image is NOT deleted when the failure is ours.
    assert list((grok_home / "sessions").glob(f"*/{run.id}/images/1.jpg")), (
        "capture-truncation failures must preserve the billed session image"
    )


def test_grok_truncated_inspect_preflight_is_retryable_upstream(
    db_session, tmp_path, monkeypatch
):
    runner = _TruncatedCaptureRunner(inspect_truncated=True)
    adapter, _grok_home = _invoke(db_session, monkeypatch, tmp_path, runner)

    with pytest.raises(ProviderAdapterError) as caught:
        adapter.generate_asset(ImageRequest(prompt="预检截断场景"))

    assert caught.value.code == "UPSTREAM"
    assert caught.value.retryable is True
    assert runner.calls == 1, "the media tool must not run after a truncated preflight"


# ---------------------------------------------------------------------- #195
# The retained run's checksums verify against the persisted logs.


def test_grok_retained_failure_checksum_verifies_against_persisted_logs(
    db_session, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "app.model_adapters.grok_build_cli._cleanup_owned_session",
        lambda *_args: {"error_type": "PermissionError"},
    )
    runner = _WrongToolRunner()
    adapter, _grok_home = _invoke(db_session, monkeypatch, tmp_path, runner)

    with pytest.raises(ProviderAdapterError) as caught:
        adapter.generate_asset(ImageRequest(prompt="校验和失败场景"))

    assert caught.value.code == "INVALID_OUTPUT"
    run = db_session.scalar(select(CLIExecutionRun))
    assert run.cleanup_state == "RETAINED"
    output = None
    for candidate in (tmp_path / "storage" / "cli_runs").iterdir():
        if (candidate / "output" / "stdout.log").exists():
            output = candidate / "output"
    assert output is not None, "the retained run directory must still exist"
    stdout_log = (output / "stdout.log").read_bytes()
    stderr_log = (output / "stderr.log").read_bytes()
    # The cleanup-warning stderr is non-empty here, so both streams carry the
    # exact bytes the checksums were computed over.
    assert stderr_log, "the monkeypatched cleanup warning must be persisted"
    assert run.stdout_checksum == hashlib.sha256(stdout_log).hexdigest()
    assert run.stderr_checksum == hashlib.sha256(stderr_log).hexdigest()
    # And the raw prompt never leaks into the persisted diagnostics.
    assert "校验和失败场景" not in stdout_log.decode("utf-8", errors="replace")


def test_grok_success_checksum_covers_the_sanitized_summary(
    db_session, tmp_path, monkeypatch
):
    runner = _CleanSuccessRunner()
    adapter, _grok_home = _invoke(db_session, monkeypatch, tmp_path, runner)

    response = adapter.generate_asset(ImageRequest(prompt="成功校验和场景"))
    assert response.images == (_jpeg_bytes(),)

    run = db_session.scalar(select(CLIExecutionRun))
    summary = json.dumps(
        {"status": "SUCCEEDED", "tool": "image_gen", "images": 1},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    # Precondition of the scheme: the controller's sanitizer is the identity
    # on this payload, so the persisted stdout.log bytes equal the summary.
    assert _sanitize_diagnostic(summary, "utf-8") == summary
    assert run.stdout_checksum == hashlib.sha256(summary).hexdigest()
    assert run.stderr_checksum == hashlib.sha256(b"").hexdigest()
