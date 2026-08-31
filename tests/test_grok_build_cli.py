"""Offline V02-14C acceptance for the Grok Build CLI image channel."""

import json
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.config import Settings, get_settings
from app.model_adapters.base import ImageRequest, ProviderAdapterError
from app.model_adapters.grok_build_cli import (
    GrokBuildCLIImageAdapter,
    GrokBuildCLIProbeAdapter,
    GrokBuildCLIRuntime,
    resolve_grok_build_executable,
)
from app.models import (
    AIModel,
    CLIExecutionRun,
    GenerationJob,
    ModelCallAttempt,
    Project,
    ProviderConnection,
    ProviderProfile,
)
from app.services.cli_executor import CLIExecutionController, CLIProcessOutcome
from app.services.model_router import AdapterBinding, ResolvedModel, bind_adapter
from app.services.provider_presets import ensure_provider_presets
from app.services.worker_handlers import provider as provider_handler


def _jpeg_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (8, 8), "orange").save(output, format="JPEG")
    return output.getvalue()


def _png_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (8, 8), "teal").save(output, format="PNG")
    return output.getvalue()


def _fake_probe_runner(argv: tuple[str, ...]) -> CLIProcessOutcome:
    if argv[-1] == "--version":
        return CLIProcessOutcome(0, stdout=b"grok 1.0.13 (5e9a58528b76)\n")
    if argv[-1] == "models" and "--help" not in argv:
        return CLIProcessOutcome(
            0,
            stdout=b"Default model: grok-4.6\nAvailable models:\n * grok-4.6",
        )
    if argv[-1] == "--help" and "models" not in argv:
        return CLIProcessOutcome(
            0,
            stdout=(
                b"--single --session-id --output-format --prompt-file --verbatim "
                b"--tools --disallowed-tools --permission-mode --sandbox --max-turns "
                b"--no-subagents --disable-web-search"
            ),
        )
    if argv[-2:] == ("models", "--help"):
        return CLIProcessOutcome(0, stdout=b"List available models and exit")
    if argv[-2:] == ("inspect", "--json"):
        return CLIProcessOutcome(0, stdout=b'{"hooks":[]}')
    raise AssertionError(argv)


def _probe_adapter(**overrides) -> GrokBuildCLIProbeAdapter:
    return GrokBuildCLIProbeAdapter(
        Settings(),
        executable="grok",
        command_runner=overrides.get("command_runner", _fake_probe_runner),
        executable_finder=lambda _value: "C:/tools/grok.exe",
    )


def test_grok_build_probe_parses_version_login_and_safe_headless_surfaces():
    adapter = _probe_adapter()

    assert adapter.presence().status == "PASSED"
    version = adapter.version()
    assert version.status == "PASSED" and version.metrics["version"] == "1.0.13"
    assert adapter.login().status == "PASSED"
    capability = adapter.capability()
    assert capability.status == "PASSED"
    assert capability.metrics["operations"] == ["image_generate", "image_edit"]
    assert capability.metrics["image_tools"] == ["image_gen", "image_edit"]


@pytest.mark.parametrize(
    ("failure", "expected"),
    [("login", "UNAUTHENTICATED"), ("capability", "UNSUPPORTED")],
)
def test_grok_build_probe_fails_closed(failure, expected):
    def runner(argv: tuple[str, ...]) -> CLIProcessOutcome:
        if failure == "login" and argv[-1] == "models" and "--help" not in argv:
            return CLIProcessOutcome(0, stdout=b"You are not authenticated")
        if failure == "capability" and argv[-1] == "--help" and "models" not in argv:
            return CLIProcessOutcome(0, stdout=b"--single --output-format")
        return _fake_probe_runner(argv)

    adapter = _probe_adapter(command_runner=runner)
    assert adapter.presence().status == "PASSED"
    assert adapter.version().status == "PASSED"
    login = adapter.login()
    if failure == "login":
        assert login.error_code == expected
    else:
        assert login.status == "PASSED"
        assert adapter.capability().error_code == expected


def test_grok_build_probe_rejects_external_hooks():
    def runner(argv: tuple[str, ...]) -> CLIProcessOutcome:
        if argv[-2:] == ("inspect", "--json"):
            return CLIProcessOutcome(0, stdout=b'{"hooks":[{"event":"SessionStart"}]}')
        return _fake_probe_runner(argv)

    adapter = _probe_adapter(command_runner=runner)
    assert adapter.presence().status == "PASSED"
    assert adapter.version().status == "PASSED"
    assert adapter.login().status == "PASSED"
    capability = adapter.capability()
    assert (capability.status, capability.error_code) == ("FAILED", "UNSUPPORTED")


def test_grok_build_resolver_accepts_only_native_canonical_executable(tmp_path):
    native = tmp_path / "grok.exe"
    native.write_bytes(b"native")
    wrapper = tmp_path / "grok.cmd"
    wrapper.write_text("wrapper", encoding="utf-8")

    assert resolve_grok_build_executable(str(native)) == str(native.resolve())
    assert resolve_grok_build_executable(str(wrapper)) is None
    with pytest.raises(ValueError):
        resolve_grok_build_executable("grok-build")


def test_grok_build_preset_seeds_declared_disabled_cli_channel(client):
    providers = client.get("/api/v1/providers").json()
    provider = next(
        item for item in providers if item["preset_key"] == "grok-build-cli"
    )
    connection = provider["connections"][0]

    assert connection["protocol"] == "CLI_GROK_BUILD"
    assert connection["credential_source"] == "CLI_SESSION"
    assert connection["enabled"] is False
    assert connection["nonsecret_config"]["cli_executable"] == "grok"
    models = client.get(
        f"/api/v1/providers/connections/{connection['id']}/models"
    ).json()
    assert [(item["provider_model_id"], item["confidence"]) for item in models] == [
        ("grok-build-imagine", "DECLARED")
    ]
    assert models[0]["capabilities"]["max_reference_images"] == 5


def test_grok_build_connection_probe_and_executable_update(
    client, db_session, monkeypatch
):
    ensure_provider_presets(db_session, get_settings(), auto_commit=True)
    profile = db_session.scalar(
        select(ProviderProfile).where(ProviderProfile.preset_key == "grok-build-cli")
    )
    connection = db_session.scalar(
        select(ProviderConnection).where(ProviderConnection.provider_id == profile.id)
    )
    monkeypatch.setattr(
        "app.services.connection_verifier.GrokBuildCLIProbeAdapter",
        lambda *_args, **_kwargs: _probe_adapter(),
    )

    verified = client.post(
        f"/api/v1/providers/connections/{connection.id}/verify",
        json={"level": "CREDENTIALS"},
    )
    assert verified.status_code == 200
    assert verified.json()["health"]["health_state"] == "AVAILABLE"
    assert verified.json()["probe"]["probe_type"] == "CLI_CAPABILITY"
    db_session.refresh(connection)
    assert connection.enabled is False
    assert connection.nonsecret_config["cli_version"] == "1.0.13"

    rejected = client.patch(
        f"/api/v1/providers/connections/{connection.id}",
        json={
            "version": connection.version,
            "nonsecret_config": {"cli_executable": "tools/grok.exe"},
        },
    )
    assert rejected.status_code == 422
    updated = client.patch(
        f"/api/v1/providers/connections/{connection.id}",
        json={
            "version": connection.version,
            "nonsecret_config": {"cli_executable": "C:\\tools\\grok.exe"},
        },
    )
    assert updated.status_code == 200
    assert updated.json()["health_state"] == "UNKNOWN"


def test_available_grok_build_model_binds_without_api_key(db_session):
    settings = get_settings()
    ensure_provider_presets(db_session, settings, auto_commit=True)
    profile = db_session.scalar(
        select(ProviderProfile).where(ProviderProfile.preset_key == "grok-build-cli")
    )
    connection = db_session.scalar(
        select(ProviderConnection).where(ProviderConnection.provider_id == profile.id)
    )
    model = db_session.scalar(
        select(AIModel).where(AIModel.connection_id == connection.id)
    )
    connection.enabled = True
    connection.health_state = "AVAILABLE"
    db_session.commit()

    binding = bind_adapter(
        db_session,
        settings,
        operation="image_generate",
        explicit_reference=model.id,
    )

    assert isinstance(binding.adapter, GrokBuildCLIImageAdapter)
    assert binding.selected_key is None


def _cli_rows(db_session, tmp_path):
    factory = sessionmaker(
        bind=db_session.get_bind(), autoflush=False, expire_on_commit=False
    )
    settings = Settings(
        storage_root=tmp_path / "storage",
        upload_root=tmp_path / "uploads",
        cli_run_timeout_seconds=30,
    )
    settings.ensure_directories()
    project = Project(name="Grok Build CLI 离线项目")
    profile = ProviderProfile(
        preset_key="grok-build-cli",
        name="Grok Build CLI",
        enabled=True,
        built_in=True,
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
    return factory, settings, profile, connection, model, job, attempt


class _SuccessfulGrokRunner:
    def __init__(self, *, duplicate=False, wrong_tool=False, out_of_root=False):
        self.calls = 0
        self.timeouts = []
        self.duplicate = duplicate
        self.wrong_tool = wrong_tool
        self.out_of_root = out_of_root
        self.prompt_text = ""

    def run(self, *, argv, cwd: Path, environment, timeout_seconds, **_kwargs):
        self.calls += 1
        self.timeouts.append(timeout_seconds)
        assert argv[0] == "C:/tools/grok.exe"
        if argv[1:] == ("inspect", "--json"):
            assert (cwd / ".git").is_dir()
            assert not (cwd / "mangaflow-prompt.txt").exists()
            return CLIProcessOutcome(0, stdout=b'{"hooks":[]}')
        run_id = argv[argv.index("--session-id") + 1]
        tool = argv[argv.index("--tools") + 1]
        assert argv[argv.index("--prompt-file") + 1] == "mangaflow-prompt.txt"
        assert argv[argv.index("--output-format") + 1] == "streaming-json"
        assert argv[argv.index("--permission-mode") + 1] == "bypassPermissions"
        assert argv[argv.index("--sandbox") + 1] == "strict"
        assert "--disable-web-search" in argv
        assert "--no-subagents" in argv
        assert "漫画私密提示" not in argv
        assert environment["GROK_DISABLE_AUTOUPDATER"] == "1"
        assert "XAI_API_KEY" not in environment
        self.prompt_text = (cwd / "mangaflow-prompt.txt").read_text("utf-8")
        assert "漫画私密提示" in self.prompt_text
        assert "exactly once" in self.prompt_text

        output_type = "ImageEdit" if tool == "image_edit" else "ImageGen"
        grok_home = Path(environment["GROK_HOME"])
        source = (
            grok_home / "sessions" / "encoded-workspace" / run_id / "images" / "1.jpg"
        )
        source.parent.mkdir(parents=True)
        source.write_bytes(_jpeg_bytes())
        emitted_path = (
            grok_home
            / "sessions"
            / "encoded-workspace"
            / "other-run"
            / "images"
            / "1.jpg"
            if self.out_of_root
            else source
        )
        if self.out_of_root:
            emitted_path.parent.mkdir(parents=True)
            emitted_path.write_bytes(_jpeg_bytes())

        call_tool = "read_file" if self.wrong_tool else tool
        events = [
            {
                "type": "tool_call",
                "toolCallId": "call-1",
                "toolName": call_tool,
                "rawInput": {"prompt": "must not be persisted"},
            },
            {
                "type": "tool_call_update",
                "toolCallId": "call-1",
                "status": "completed",
                "rawOutput": {
                    "output": {
                        "type": output_type,
                        "path": str(emitted_path),
                        "filename": "1.jpg",
                        "session_folder": "images",
                    }
                },
            },
        ]
        if self.duplicate:
            events.extend(
                [
                    {
                        "type": "tool_call",
                        "toolCallId": "call-2",
                        "toolName": tool,
                        "rawInput": {},
                    },
                    {
                        "type": "tool_call_update",
                        "toolCallId": "call-2",
                        "status": "completed",
                        "rawOutput": {
                            "output": {
                                "type": output_type,
                                "path": str(source),
                                "filename": "1.jpg",
                                "session_folder": "images",
                            }
                        },
                    },
                ]
            )
        events.append(
            {
                "type": "end",
                "stopReason": "end_turn",
                "sessionId": run_id,
                "usage": {"input_tokens": 7, "unsafe": "ignored"},
            }
        )
        return CLIProcessOutcome(
            0,
            stdout="\n".join(json.dumps(event) for event in events).encode(),
        )


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


def test_grok_build_image_edit_adopts_one_typed_session_artifact(
    db_session, tmp_path, monkeypatch
):
    factory, settings, _profile, connection, model, job, attempt = _cli_rows(
        db_session, tmp_path
    )
    grok_home = tmp_path / "grok-home"
    monkeypatch.setenv("GROK_HOME", str(grok_home))
    runner = _SuccessfulGrokRunner()
    adapter = _adapter(factory, settings, connection, model, runner)
    adapter.bind_execution_context(
        job_id=job.id,
        model_call_attempt_id=attempt.id,
        lease_owner=None,
    )

    response = adapter.edit_region(
        ImageRequest(
            prompt="漫画私密提示",
            reference_images=(_png_bytes(),),
            reference_mime_types=("image/png",),
            aspect_ratio="3:4",
        )
    )

    assert runner.calls == 2 and response.images == (_jpeg_bytes(),)
    assert "image_edit" in runner.prompt_text
    assert "input" in runner.prompt_text and "references" in runner.prompt_text
    assert response.usage == {
        "input_tokens": 7,
        "estimated_cost": None,
        "cost_source": "CLI_EXTERNAL",
    }
    run = db_session.scalar(select(CLIExecutionRun))
    assert (run.state, run.cleanup_state, run.lease_slot) == (
        "COMPLETED",
        "CLEANED",
        None,
    )
    assert not (settings.storage_root / "cli_runs" / run.id).exists()
    assert not list((grok_home / "sessions").glob(f"*/{run.id}"))


def test_grok_build_inspect_and_media_share_one_timeout_deadline(
    db_session, tmp_path, monkeypatch
):
    factory, settings, _profile, connection, model, job, attempt = _cli_rows(
        db_session, tmp_path
    )
    monkeypatch.setenv("GROK_HOME", str(tmp_path / "grok-home"))
    clock = iter((100.0, 112.5))
    monkeypatch.setattr(
        "app.model_adapters.grok_build_cli.perf_counter", lambda: next(clock)
    )
    runner = _SuccessfulGrokRunner()
    adapter = _adapter(factory, settings, connection, model, runner)
    adapter.bind_execution_context(
        job_id=job.id,
        model_call_attempt_id=attempt.id,
        lease_owner=None,
    )

    adapter.generate_asset(ImageRequest(prompt="漫画私密提示"))

    assert runner.timeouts == [30, pytest.approx(17.5)]


@pytest.mark.parametrize(
    ("runner", "expected"),
    [
        (_SuccessfulGrokRunner(duplicate=True), "PARTIAL_OUTPUT"),
        (_SuccessfulGrokRunner(wrong_tool=True), "INVALID_OUTPUT"),
        (_SuccessfulGrokRunner(out_of_root=True), "INVALID_OUTPUT"),
    ],
    ids=["duplicate", "wrong-tool", "wrong-session"],
)
def test_grok_build_rejects_unowned_or_ambiguous_tool_output(
    db_session, tmp_path, monkeypatch, runner, expected
):
    factory, settings, _profile, connection, model, job, attempt = _cli_rows(
        db_session, tmp_path
    )
    grok_home = tmp_path / "grok-home"
    monkeypatch.setenv("GROK_HOME", str(grok_home))
    adapter = _adapter(factory, settings, connection, model, runner)
    adapter.bind_execution_context(
        job_id=job.id,
        model_call_attempt_id=attempt.id,
        lease_owner=None,
    )

    with pytest.raises(ProviderAdapterError) as caught:
        adapter.generate_asset(ImageRequest(prompt="漫画私密提示"))

    assert caught.value.code == expected
    run = db_session.scalar(select(CLIExecutionRun))
    assert (run.state, run.error_code, run.cleanup_state) == (
        "FAILED",
        expected,
        "RETAINED",
    )
    assert not list((grok_home / "sessions").glob(f"*/{run.id}"))
    if runner.out_of_root:
        assert list((grok_home / "sessions").glob("*/other-run"))


def test_grok_build_failed_session_cleanup_warning_is_safely_propagated(
    db_session, tmp_path, monkeypatch
):
    factory, settings, _profile, connection, model, job, attempt = _cli_rows(
        db_session, tmp_path
    )
    monkeypatch.setenv("GROK_HOME", str(tmp_path / "grok-home"))
    monkeypatch.setattr(
        "app.model_adapters.grok_build_cli._cleanup_owned_session",
        lambda *_args: {"error_type": "PermissionError"},
    )
    runner = _SuccessfulGrokRunner(wrong_tool=True)
    adapter = _adapter(factory, settings, connection, model, runner)
    adapter.bind_execution_context(
        job_id=job.id,
        model_call_attempt_id=attempt.id,
        lease_owner=None,
    )

    with pytest.raises(ProviderAdapterError) as caught:
        adapter.generate_asset(ImageRequest(prompt="漫画私密提示"))

    assert caught.value.code == "INVALID_OUTPUT"
    assert "失败会话清理未完成" in caught.value.user_message
    run = db_session.scalar(select(CLIExecutionRun))
    stderr = settings.storage_root / "cli_runs" / run.id / "output" / "stderr.log"
    assert "PermissionError" in stderr.read_text("utf-8")
    assert "漫画私密提示" not in stderr.read_text("utf-8")


def test_grok_build_failure_finalizes_audit_without_http_fallback(
    db_session, tmp_path, monkeypatch
):
    factory, settings, profile, connection, model, job, seeded_attempt = _cli_rows(
        db_session, tmp_path
    )
    db_session.delete(seeded_attempt)
    db_session.commit()
    monkeypatch.setenv("GROK_HOME", str(tmp_path / "grok-home"))

    class Runner:
        calls = 0

        def run(self, **kwargs):
            self.calls += 1
            if kwargs["argv"][1:] == ("inspect", "--json"):
                return CLIProcessOutcome(0, stdout=b'{"hooks":[]}')
            run_id = kwargs["cwd"].parent.name
            partial = (
                Path(kwargs["environment"]["GROK_HOME"])
                / "sessions"
                / "encoded-workspace"
                / run_id
                / "images"
                / "partial.jpg"
            )
            partial.parent.mkdir(parents=True)
            partial.write_bytes(_jpeg_bytes())
            return CLIProcessOutcome(1, stderr=b"You are not authenticated")

    runner = Runner()
    binding = AdapterBinding(
        resolved=ResolvedModel(model=model, connection=connection, provider=profile),
        adapter=_adapter(factory, settings, connection, model, runner),
        selected_key=None,
    )
    db_session.info["job_id"] = job.id
    callback_calls = 0

    def invoke(bound_adapter):
        nonlocal callback_calls
        callback_calls += 1
        return bound_adapter.generate_asset(ImageRequest(prompt="审计失败"))

    with pytest.raises(ProviderAdapterError) as caught:
        provider_handler._invoke_provider(db_session, binding, invoke)

    assert caught.value.code == "UNAUTHENTICATED"
    assert callback_calls == 1 and runner.calls == 2
    attempt = db_session.scalar(select(ModelCallAttempt))
    assert (attempt.outcome, attempt.error_code) == ("FAILED", "UNAUTHENTICATED")
    run = db_session.scalar(select(CLIExecutionRun))
    assert not list((tmp_path / "grok-home" / "sessions").glob(f"*/{run.id}"))


def test_grok_build_runtime_rejects_hooks_before_writing_prompt_or_calling_image_tool(
    db_session, tmp_path, monkeypatch
):
    factory, settings, _profile, connection, model, job, attempt = _cli_rows(
        db_session, tmp_path
    )
    monkeypatch.setenv("GROK_HOME", str(tmp_path / "grok-home"))

    class Runner:
        calls = []

        def run(self, **kwargs):
            self.calls.append(kwargs["argv"])
            return CLIProcessOutcome(
                0,
                stdout=b'{"hooks":[{"event":"PreToolUse","target":"ignored"}]}',
            )

    runner = Runner()
    adapter = _adapter(factory, settings, connection, model, runner)
    adapter.bind_execution_context(
        job_id=job.id,
        model_call_attempt_id=attempt.id,
        lease_owner=None,
    )

    with pytest.raises(ProviderAdapterError) as caught:
        adapter.generate_asset(ImageRequest(prompt="不得传给外部钩子的私密提示"))

    assert caught.value.code == "UNSUPPORTED"
    assert len(runner.calls) == 1 and runner.calls[0][1:] == ("inspect", "--json")
    run = db_session.scalar(select(CLIExecutionRun))
    assert (run.state, run.error_code, run.cleanup_state) == (
        "FAILED",
        "UNSUPPORTED",
        "CLEANED",
    )
    assert not (settings.storage_root / "cli_runs" / run.id).exists()
